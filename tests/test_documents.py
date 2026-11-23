import io
import os
import time

from PIL import Image
from pypdf import PdfReader, PdfWriter

from loadpilot.config import RATECON_DIR
from loadpilot.documents import (
    claim_pending,
    clean_pending,
    combine_pages,
    image_pdf,
    save_for_load,
    save_pending,
    with_document,
)


def png(size=(40, 60)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, "PNG")
    return buffer.getvalue()


def pdf(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_single_upload_is_kept_as_is():
    assert combine_pages([(b"%PDF-1.4 rate con", "application/pdf", "ratecon.pdf")]) == (b"%PDF-1.4 rate con", ".pdf")
    assert combine_pages([(b"jpeg bytes", "image/jpeg", None)])[1] == ".jpg"


def test_unknown_file_type_is_not_kept():
    assert combine_pages([(b"MZ", "application/octet-stream", "ratecon.exe")]) is None


def test_several_pages_merge_into_one_pdf_in_order():
    content, extension = combine_pages(
        [(png(), "image/png", "page1.png"), (pdf(pages=2), "application/pdf", "page2.pdf")]
    )

    assert extension == ".pdf"
    assert len(PdfReader(io.BytesIO(content)).pages) == 3


def test_pending_rate_con_is_claimed_by_its_load():
    name = save_pending([(b"%PDF-1.4", "application/pdf", "ratecon.pdf")])

    path = claim_pending(name, 7)

    assert path.startswith("static/ratecons/ratecon_7_") and path.endswith(".pdf")
    assert (RATECON_DIR / path.removeprefix("static/ratecons/")).read_bytes() == b"%PDF-1.4"
    assert not (RATECON_DIR / name).exists()
    assert claim_pending(name, 7) is None  # already claimed


def test_claim_rejects_names_outside_the_pending_files():
    assert claim_pending("../loadpilot.db", 1) is None
    assert claim_pending("pending_" + "0" * 32 + ".pdf", 1) is None  # never saved
    assert claim_pending("", 1) is None


def test_unreadable_upload_does_not_block_the_scan():
    assert save_pending([(b"not an image", "image/png", "a.png"), (b"nor this", "image/png", "b.png")]) is None


def test_clean_pending_removes_only_abandoned_files():
    fresh = save_pending([(b"%PDF-1.4", "application/pdf", "fresh.pdf")])
    stale = save_pending([(b"%PDF-1.4", "application/pdf", "stale.pdf")])
    day_old = time.time() - 25 * 60 * 60
    os.utime(RATECON_DIR / stale, (day_old, day_old))
    kept = save_for_load([(b"%PDF-1.4", "application/pdf", "kept.pdf")], 3)
    os.utime(RATECON_DIR / kept.removeprefix("static/ratecons/"), (day_old, day_old))

    clean_pending()

    assert (RATECON_DIR / fresh).exists()
    assert not (RATECON_DIR / stale).exists()
    assert (RATECON_DIR / kept.removeprefix("static/ratecons/")).exists()


def page_sizes(content: bytes) -> list[tuple[int, int]]:
    return [
        (round(float(page.mediabox.width)), round(float(page.mediabox.height)))
        for page in PdfReader(io.BytesIO(content)).pages
    ]


def test_photos_become_letter_sized_pages():
    # A 12 MP phone photo, portrait and landscape: each fits Letter (612 x 792 pt) in its own orientation
    assert page_sizes(image_pdf(png((3024, 4032)))) == [(594, 792)]
    assert page_sizes(image_pdf(png((4032, 3024)))) == [(792, 594)]


def test_bol_pages_follow_the_invoice(tmp_path):
    photo, scan = tmp_path / "bol.jpg", tmp_path / "bol.pdf"
    photo.write_bytes(png((1200, 1600)))
    scan.write_bytes(pdf(pages=2))

    assert len(PdfReader(io.BytesIO(with_document(pdf(), photo))).pages) == 2
    assert len(PdfReader(io.BytesIO(with_document(pdf(), scan))).pages) == 3


def test_unreadable_bol_leaves_the_invoice_alone(tmp_path):
    broken = tmp_path / "bol.heic"
    broken.write_bytes(b"not an image")
    invoice = pdf()

    assert with_document(invoice, broken) == invoice
    assert with_document(invoice, tmp_path / "missing.pdf") == invoice
