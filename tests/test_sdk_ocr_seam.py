"""``personalclaw.sdk.ocr`` — the OCR-engine seam on the app boundary.

An OCR app (RapidOCR, Tesseract, PaddleOCR, docTR, a cloud vision API) implements
:class:`OcrProvider` and reaches core only through this facade. Three properties the app
front depends on, and one the facade deliberately withholds:

* every promoted name resolves to the SAME object core uses — a facade that re-implements
  is a second implementation wearing a re-export's clothes, and for the true-type gate
  that would mean a second magic-number table, which is the drift the gate exists to stop;
* ``register_provider`` / ``unregister_provider`` / ``get_provider`` / ``list_providers``
  are deliberately NOT promoted: registration is core's job through the manifest's ``ocr``
  type handler, so a provider is visible exactly while its app is enabled. An
  app-registered engine reaching the shared registry directly would make ``active_ocr()``
  stop being a property of the build;
* the gate an app actually calls before handing bytes to a decoder refuses on the BYTES,
  not on the name — so a ``.png`` holding a PDF is rejected through the facade, which is
  the only definition of "the app inherited the ARCC allowlist" worth asserting;
* ``recognize`` takes a ``Sequence[str]``, so the gate is promoted in its BATCH form too:
  ``partition_images`` collects the refusals instead of raising on the first one. A
  provider handed forty pages must not discard thirty-nine readable ones because page
  seven lied about its type, and a facade that promoted only the single-path raising gate
  would leave every app to re-derive that loop — the second implementation this seam
  exists to prevent.
"""

from __future__ import annotations

import pytest

import personalclaw.ocr as core_ocr
from personalclaw.ocr.filetype import MAX_IMAGE_BYTES as core_max_image_bytes
from personalclaw.ocr.filetype import TrueTypeRejected as core_true_type_rejected
from personalclaw.ocr.filetype import assert_image as core_assert_image
from personalclaw.ocr.filetype import detect_image_type as core_detect_image_type
from personalclaw.ocr.filetype import partition_images as core_partition_images
from personalclaw.ocr.provider import OcrError as core_ocr_error
from personalclaw.ocr.provider import OcrProvider as core_ocr_provider
from personalclaw.ocr.provider import OcrRejected as core_ocr_rejected
from personalclaw.ocr.provider import OcrResult as core_ocr_result
from personalclaw.ocr.registry import active_ocr as core_active_ocr
from personalclaw.ocr.registry import ocr_available as core_ocr_available
from personalclaw.sdk.ocr import (
    MAX_IMAGE_BYTES,
    OcrError,
    OcrProvider,
    OcrRejected,
    OcrResult,
    TrueTypeRejected,
    active_ocr,
    assert_image,
    detect_image_type,
    ocr_available,
    partition_images,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PDF_MAGIC = b"%PDF-1.7\n"


def test_every_promoted_name_is_the_core_object_not_a_copy() -> None:
    """A facade that re-implemented the gate would pass a behavioural check and still be
    a fork, so identity is what gets asserted."""
    for name, promoted, core in (
        ("OcrProvider", OcrProvider, core_ocr_provider),
        ("OcrResult", OcrResult, core_ocr_result),
        ("OcrError", OcrError, core_ocr_error),
        ("OcrRejected", OcrRejected, core_ocr_rejected),
        ("active_ocr", active_ocr, core_active_ocr),
        ("ocr_available", ocr_available, core_ocr_available),
        ("assert_image", assert_image, core_assert_image),
        ("partition_images", partition_images, core_partition_images),
        ("detect_image_type", detect_image_type, core_detect_image_type),
        ("TrueTypeRejected", TrueTypeRejected, core_true_type_rejected),
        ("MAX_IMAGE_BYTES", MAX_IMAGE_BYTES, core_max_image_bytes),
    ):
        assert promoted is core, f"sdk.ocr.{name} is not the core object — the facade forked"


def test_registration_is_not_promoted_so_a_provider_tracks_its_app() -> None:
    """Core exports the registry mutators; the SDK facade does not. An app that could
    call them would put an engine in the shared registry independently of whether its
    app is enabled."""
    import personalclaw.sdk.ocr as sdk_ocr

    for withheld in ("register_provider", "unregister_provider", "get_provider", "list_providers"):
        assert withheld in core_ocr.__all__, f"core stopped exporting {withheld}"
        assert withheld not in sdk_ocr.__all__, (
            f"sdk.ocr promoted {withheld} — registration must stay core's job through the "
            "manifest's `ocr` type handler"
        )


def test_the_promoted_gate_refuses_on_bytes_not_on_the_name(tmp_path) -> None:
    """The mismatch case is the one the gate exists for, and an app only inherits it if
    the facade's `assert_image` is the gate rather than a thin wrapper around a decoder."""
    liar = tmp_path / "scan.png"
    liar.write_bytes(PDF_MAGIC + b"0" * 64)
    with pytest.raises(TrueTypeRejected) as caught:
        assert_image(str(liar))
    assert "png" in str(caught.value)

    honest = tmp_path / "scan.png"
    honest.write_bytes(PNG_MAGIC + b"0" * 64)
    assert assert_image(str(honest)) == "png"


def test_the_promoted_batch_gate_collects_refusals_instead_of_raising(tmp_path) -> None:
    """The batch form is the one a `recognize(Sequence[str])` implementation reaches for,
    and its contract is the opposite of `assert_image`'s: it must return rather than raise,
    keeping the readable pages and reporting why the others were refused. Asserting the
    partition (not just "it did not raise") is what stops a future edit from turning one
    bad page into a discarded batch."""
    good_one = tmp_path / "page1.png"
    good_one.write_bytes(PNG_MAGIC + b"0" * 64)
    liar = tmp_path / "page2.png"
    liar.write_bytes(PDF_MAGIC + b"0" * 64)
    good_two = tmp_path / "page3.png"
    good_two.write_bytes(PNG_MAGIC + b"0" * 64)

    accepted, rejected = partition_images([str(good_one), str(liar), str(good_two)])

    assert accepted == [
        str(good_one),
        str(good_two),
    ], "the batch gate dropped a readable page — one refusal must not discard the rest"
    assert len(rejected) == 1, f"expected exactly one refusal reason, got {rejected}"
    assert (
        "page2.png" in rejected[0]
    ), f"the refusal reason does not name the input it refused: {rejected[0]!r}"

    # An empty batch is the graceful-skip path, not an error.
    assert partition_images([]) == ([], [])


def test_the_promoted_sniffer_is_byte_only_and_allowlisted() -> None:
    """`detect_image_type` takes no path and no declared type, so an app cannot pass it
    a `Content-Type` by accident; and an unlisted format answers None rather than a guess."""
    assert detect_image_type(PNG_MAGIC + b"rest") == "png"
    assert detect_image_type(PDF_MAGIC) is None
    assert detect_image_type(b"") is None


def test_no_engine_is_the_normal_state_through_the_facade() -> None:
    """Core ships no OCR engine, so with no app installed the facade must answer "none"
    rather than raise — the graceful-skip path ingestion depends on."""
    assert ocr_available() is (active_ocr() is not None)
