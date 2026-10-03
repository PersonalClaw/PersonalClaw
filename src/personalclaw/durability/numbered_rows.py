"""Merging a table a store numbers itself, by the key a person knows its rows by.

A sqlite store can number its rows itself (``INTEGER PRIMARY KEY``) while a set of columns names
each row uniquely: the knowledge library numbers its tags and keeps their names unique, and files
each item under a tag by its number. Another home numbers its own rows from 1 too, so merged by
number (``INSERT OR IGNORE``, the merge restore's, an archive import's and a folder sync's) every
row of the other home's came in filed under this home's row of the same number: a garden note
tagged "kitchen". Such a table takes the rows whose key the store lacks under numbers of its own,
and every reference to it follows its row (``snapshot._merge_sqlite_attach``).

Every name this module puts in a statement is read from the live schema (``main``), never from
the archive's.
"""

from __future__ import annotations

import re

from personalclaw.sqlite_compat import sqlite3


def numbered_tables(conn: "sqlite3.Connection", tables: list[str]) -> dict[str, tuple[str, ...]]:
    """Each of *tables* the store numbers itself, mapped to its number column followed by the
    columns of its key: a table whose primary key is its row number (``INTEGER PRIMARY KEY``) and
    whose rows a set of required columns names uniquely (a ``UNIQUE`` index). Another store's
    number names another row; the key is how a person knows the row, a tag by its name. Read from
    the live schema."""
    out: dict[str, tuple[str, ...]] = {}
    for table in tables:
        sql = conn.execute(
            "SELECT sql FROM main.sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if sql is None or re.search(r"\bWITHOUT\s+ROWID\b", sql[0] or "", re.IGNORECASE):
            continue
        info = conn.execute(f'PRAGMA main.table_info("{table}")').fetchall()
        key = [r for r in info if r[5]]
        if len(key) != 1 or (key[0][2] or "").strip().upper() != "INTEGER":
            continue
        required = {r[1] for r in info if r[3]}
        for _seq, index, unique, origin, partial in conn.execute(
            f'PRAGMA main.index_list("{table}")'
        ).fetchall():
            if not unique or origin == "pk" or partial:
                continue
            names = [r[2] for r in conn.execute(f'PRAGMA main.index_info("{index}")').fetchall()]
            if names and all(n in required for n in names):
                out[table] = (key[0][1], *names)
                break
    return out


def references(
    conn: "sqlite3.Connection", table: str, numbered: dict[str, tuple[str, ...]]
) -> list[tuple[str, str]]:
    """``(column, numbered table)`` for each column of *table* that refers to a row of a numbered
    table by its number (a declared ``REFERENCES``)."""
    out: list[tuple[str, str]] = []
    for row in conn.execute(f'PRAGMA main.foreign_key_list("{table}")').fetchall():
        parent, column, to = row[2], row[3], row[4]
        if parent in numbered and to in (None, numbered[parent][0]):
            out.append((column, parent))
    return out


def same_columns(conn: "sqlite3.Connection", table: str) -> list[str]:
    """*table*'s columns in the live schema's order, when the archive's copy has the same ones. A
    column-set mismatch raises, as a ``SELECT *`` copy of the table would."""
    here = [r[1] for r in conn.execute(f'PRAGMA main.table_info("{table}")').fetchall()]
    there = {r[1] for r in conn.execute(f'PRAGMA src.table_info("{table}")').fetchall()}
    if set(here) != there:
        raise sqlite3.OperationalError(f"{table} has other columns in the archive")
    return here


def number_map(table: str) -> str:
    """The temporary table holding, for each row of the archive's *table*, its number here."""
    return f"_numbers_{table}"


def merge_numbered(conn: "sqlite3.Connection", numbered: dict[str, tuple[str, ...]]) -> int:
    """Merge each numbered table by its key and return how many rows came in.

    A row whose key this store lacks comes in under a number this store gives it; a row whose key
    it has is the same row, and stays as it is. Each archive row's number here is kept in
    :func:`number_map`, so the references that come in afterwards follow their rows
    (:func:`translated_insert`), and the references the rows that came in carry (a tag's parent)
    are carried across the same way. A reference to a row the archive does not hold is dropped
    rather than left naming another row. One savepoint: when any of it fails, none of it is
    kept.
    """
    if not numbered:
        return 0
    imported = 0
    conn.execute('SAVEPOINT "numbered"')
    try:
        tops: dict[str, int] = {}
        for table, (number, *key) in numbered.items():
            names = ", ".join(f'"{c}"' for c in same_columns(conn, table) if c != number)
            tops[table] = conn.execute(
                f'SELECT COALESCE(MAX("{number}"), 0) FROM main."{table}"'
            ).fetchone()[0]
            before = conn.total_changes
            conn.execute(
                f'INSERT OR IGNORE INTO main."{table}" ({names}) SELECT {names} FROM src."{table}"'
            )
            imported += conn.total_changes - before
            conn.execute(f'DROP TABLE IF EXISTS temp."{number_map(table)}"')
            conn.execute(
                f'CREATE TEMP TABLE "{number_map(table)}" '
                "(src INTEGER PRIMARY KEY, dst INTEGER NOT NULL)"
            )
            on = " AND ".join(f's."{c}" = m."{c}"' for c in key)
            conn.execute(
                f'INSERT INTO temp."{number_map(table)}" (src, dst) '
                f'SELECT s."{number}", m."{number}" FROM src."{table}" AS s '
                f'JOIN main."{table}" AS m ON {on}'
            )
        for table, top in tops.items():
            number = numbered[table][0]
            for column, target in references(conn, table, numbered):
                conn.execute(
                    f'UPDATE main."{table}" SET "{column}" = (SELECT dst FROM '
                    f'temp."{number_map(target)}" WHERE src = "{table}"."{column}") '
                    f'WHERE "{number}" > ? AND "{column}" IS NOT NULL',
                    (top,),
                )
        conn.execute('RELEASE "numbered"')
    except sqlite3.Error:
        conn.execute('ROLLBACK TO "numbered"')
        conn.execute('RELEASE "numbered"')
        raise
    return imported


def translated_insert(conn: "sqlite3.Connection", table: str, refs: list[tuple[str, str]]) -> str:
    """The statement merging *table*, each of whose *refs* names a numbered table's row by the
    archive's number: carried across to this store's number for that row (:func:`merge_numbered`).
    A reference with no row here becomes NULL, so a required one leaves its row out."""
    maps = dict(refs)
    cols = same_columns(conn, table)
    picked = ", ".join(
        (
            f'(SELECT dst FROM temp."{number_map(maps[c])}" WHERE src = r."{c}")'
            if c in maps
            else f'r."{c}"'
        )
        for c in cols
    )
    names = ", ".join(f'"{c}"' for c in cols)
    return f'INSERT OR IGNORE INTO main."{table}" ({names}) SELECT {picked} FROM src."{table}" AS r'
