"""A warning a command prints goes to stderr, so stdout carries only what the command makes.

``snapshot`` printed "⚠️ Skipping symlinked core file" among its transcript, ``restore`` its
integrity failure, the parts it left unchanged and its restart reminder, ``backup export`` what it
could not export, ``project export`` a store it could not read, and the merge helpers every table
they skipped. A script reading stdout read a warning as the output. Every warning is on stderr now.

The rail walks the package for a ``print`` whose text opens with ⚠ and that writes to stdout:
a string, an f-string, a ``+`` chain, either branch of a conditional, through a wrapper such as
``terminal_text``, or a warning constant such as ``DATA_WARNING``. Where the ⚠ line IS the
output, a report's own finding (``doctor``'s sections, ``auth status``, ``security audit`` and
``verify``, ``memory audit``, ``retrieval-eval``'s report), the narrowest function that prints
it is named in the rail, with why. So is each that prints the standing data-handling reminder:
``setup`` and ``chat``, the interactive commands, open with it, a finished ``update`` keeps it in
its transcript, and the gateway's start banner carries it.
"""

from __future__ import annotations

import textwrap

from a_warning_prints_on_stderr_shared import _warnings_on_stdout


def test_the_detector_finds_a_warning_on_stdout_and_nothing_else():
    """Calibrated both ways, or the rail passes over a walk that detects nothing."""
    source = textwrap.dedent("""
        import sys

        def warns():
            print("⚠️  Skipping symlinked core file")

        def warns_indented(x):
            print(f"  ⚠️  {x}: skipped")

        def warns_after_a_blank_line():
            print("\\n⚠️  Restart the gateway")

        def warns_on_stdout_by_name():
            print("⚠ drifted", file=sys.stdout)

        def warns_with_a_tail(x):
            print("⚠️  could not export " + x)

        def warns_on_stderr():
            print("⚠️  Skipping symlinked core file", file=sys.stderr)

        def says_it_worked():
            print("✅ Snapshot created")

        def marks_by_a_variable(mark, x):
            print(f"{mark} {x}")

        def logs_it(logger):
            logger.warning("⚠️ only a log line")

        def prints_a_message(message):
            print(message)

        DATA_WARNING = "⚠️  Do not share confidential data with AI models."

        def opens_with_the_reminder():
            print(DATA_WARNING)

        def indents_the_reminder():
            print(f"  {DATA_WARNING.replace(chr(10), chr(10) + '  ')}")

        def frames_the_reminder():
            print(f"\\n{DATA_WARNING}\\n")

        def says_a_part_through_a_wrapper(w, ok):
            print(terminal_text(str(f"  ✅ {w}" if ok else f"  ⚠️  {w}: left")))

        def marks_by_a_conditional(left):
            print(f"{'⚠️ ' if left else '✅'} Imported")

        def says_a_part_on_the_stream_it_belongs_to(w, ok):
            if ok:
                print(terminal_text(str(f"  ✅ {w}")))
            else:
                print(terminal_text(str(f"  ⚠️  {w}: left")), file=sys.stderr)

        class Banner:
            def run(self):
                print(f"\\n{DATA_WARNING}\\n")
        """)
    found = {s.function for s in _warnings_on_stdout(source, "fixture.py")}
    assert found == {
        "warns",
        "warns_indented",
        "warns_after_a_blank_line",
        "warns_on_stdout_by_name",
        "warns_with_a_tail",
        "opens_with_the_reminder",
        "indents_the_reminder",
        "frames_the_reminder",
        "says_a_part_through_a_wrapper",
        "marks_by_a_conditional",
        "Banner.run",
    }
