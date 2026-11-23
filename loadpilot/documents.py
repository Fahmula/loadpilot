"""Rate confirmations kept with their load, so the agreed rate is on file if a broker disputes it.

A scanned rate con is saved as a pending file before the driver reviews the form; saving the load claims it.
Pending files that are never claimed (the driver abandoned the form) are cleaned up after a day.
"""

import io
import logging
import mimetypes
import re
import time
import uuid
from pathlib import Path

from PIL import Image, ImageOps
from pypdf import PdfWriter

from loadpilot.config import RATECON_DIR

logger = logging.getLogger("loadpilot.documents")

EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic", ".heif"}
PENDING_NAME = re.compile(r"^pending_[0-9a-f]{32}\.[a-z]{3,4}$")
PENDING_MAX_AGE = 24 * 60 * 60

Page = tuple[bytes, str, str | None]  # (content, content type, filename), as uploaded

LETTER_INCHES = (8.5, 11)
MAX_IMAGE_SIDE = 2200  # px: about 200 dpi on a Letter page, sharp enough for a signed BOL or rate con


def extension_for(content_type: str, filename: str | None) -> str | None:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in EXTENSIONS:
        suffix = mimetypes.guess_extension(content_type or "") or ""
    return suffix if suffix in EXTENSIONS else None


def image_pdf(content: bytes) -> bytes:
    """A photo or screenshot as a one-page PDF that fits a Letter page in the photo's own orientation."""
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(content))).convert("RGB")
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    short_side, long_side = sorted(image.size)
    dpi = max(short_side / LETTER_INCHES[0], long_side / LETTER_INCHES[1])
    page = io.BytesIO()
    image.save(page, "PDF", resolution=dpi)
    return page.getvalue()


def with_document(pdf: bytes, document: Path) -> bytes:
    """pdf followed by every page of document (a PDF or a photo), e.g. an invoice with its signed BOL.

    If the document can't be read, pdf comes back unchanged so the invoice still goes out.
    """
    try:
        content = document.read_bytes()
        writer = PdfWriter()
        writer.append(io.BytesIO(pdf))
        writer.append(io.BytesIO(content if document.suffix.lower() == ".pdf" else image_pdf(content)))
        merged = io.BytesIO()
        writer.write(merged)
        return merged.getvalue()
    except Exception as e:
        logger.warning(f"Could not add {document.name} to the PDF: {e}")
        return pdf


def combine_pages(pages: list[Page]) -> tuple[bytes, str] | None:
    """One upload is kept as-is; several (photos, screenshots, PDFs) are merged into one PDF in upload order."""
    if len(pages) == 1:
        content, content_type, filename = pages[0]
        extension = extension_for(content_type, filename)
        return (content, extension) if extension else None

    writer = PdfWriter()
    for content, content_type, filename in pages:
        is_pdf = extension_for(content_type, filename) == ".pdf"
        writer.append(io.BytesIO(content if is_pdf else image_pdf(content)))
    merged = io.BytesIO()
    writer.write(merged)
    return merged.getvalue(), ".pdf"


def save_pending(pages: list[Page]) -> str | None:
    """Saves an uploaded rate con until its load is saved; returns the pending file name, or None if unreadable."""
    clean_pending()
    try:
        combined = combine_pages(pages)
    except Exception as e:
        # Keeping the document is a convenience; never let it block the scan itself
        logger.warning(f"Could not keep the uploaded rate con: {e}")
        return None
    if not combined:
        return None
    content, extension = combined
    name = f"pending_{uuid.uuid4().hex}{extension}"
    (RATECON_DIR / name).write_bytes(content)
    return name


def claim_pending(name: str | None, load_id: int) -> str | None:
    """Moves a pending rate con onto its load; returns the web path to store, or None if there's nothing to claim."""
    if not name or not PENDING_NAME.match(name) or not (RATECON_DIR / name).is_file():
        return None
    final = f"ratecon_{load_id}_{uuid.uuid4().hex[:6]}{Path(name).suffix}"
    (RATECON_DIR / name).rename(RATECON_DIR / final)
    return f"static/ratecons/{final}"


def save_for_load(pages: list[Page], load_id: int) -> str | None:
    """Saves a rate con attached to an existing load; returns the web path, or None if the file type isn't kept."""
    combined = combine_pages(pages)
    if not combined:
        return None
    content, extension = combined
    final = f"ratecon_{load_id}_{uuid.uuid4().hex[:6]}{extension}"
    (RATECON_DIR / final).write_bytes(content)
    return f"static/ratecons/{final}"


def clean_pending(now: float | None = None):
    cutoff = (now or time.time()) - PENDING_MAX_AGE
    for path in RATECON_DIR.glob("pending_*"):
        if path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
