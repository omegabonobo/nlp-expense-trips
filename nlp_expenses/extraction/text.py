from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


BASE_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
HEIC_EXTENSIONS = {".heic", ".heif"}


def initialize_image_support() -> bool:
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener(thumbnails=False)
        return True
    except Exception:
        return False


HEIC_AVAILABLE = initialize_image_support()
IMAGE_EXTENSIONS = BASE_IMAGE_EXTENSIONS | (HEIC_EXTENSIONS if HEIC_AVAILABLE else set())


def supported_receipt_extensions() -> set[str]:
    return {".pdf"} | set(IMAGE_EXTENSIONS)


def validate_receipt_content(path: Path) -> None:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            with path.open("rb") as handle:
                signature = handle.read(5)
            if signature != b"%PDF-":
                raise ValueError
        except (OSError, ValueError) as exc:
            raise ValueError(f"{path.name}: the file is not a readable PDF.") from exc
        return
    if suffix not in IMAGE_EXTENSIONS:
        if suffix in HEIC_EXTENSIONS and not HEIC_AVAILABLE:
            raise ValueError(
                f"{path.name}: HEIC decoding is unavailable. Re-run setup or convert the image to PDF, JPEG, or PNG."
            )
        raise ValueError(f"{path.name}: unsupported receipt image format.")
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.load()
    except Exception as exc:
        raise ValueError(
            f"{path.name}: the image could not be decoded. Export it again as PDF, JPEG, or PNG."
        ) from exc


def extract_text(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = extract_pdf_text(path)
        if text.strip():
            return text, "pdf_text"
        ocr = ocr_pdf(path)
        return ocr, "ocr_pdf" if ocr.strip() else "empty"
    if suffix in IMAGE_EXTENSIONS:
        ocr = ocr_image(path)
        return ocr, "ocr_image" if ocr.strip() else "empty"
    return "", "unsupported"


def extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except Exception:
        return ""
    try:
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return ""


def ocr_pdf(path: Path) -> str:
    try:
        import fitz  # PyMuPDF
    except Exception:
        return ""
    chunks: list[str] = []
    try:
        doc = fitz.open(str(path))
        with tempfile.TemporaryDirectory() as tmp:
            for page_index in range(len(doc)):
                page = doc.load_page(page_index)
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                image_path = Path(tmp) / f"page-{page_index + 1}.png"
                pix.save(str(image_path))
                chunks.append(ocr_image(image_path))
    except Exception:
        return "\n".join(chunks)
    return "\n".join(chunks)


def ocr_image(path: Path) -> str:
    try:
        import pytesseract
        from PIL import Image

        return pytesseract.image_to_string(Image.open(path), lang="eng")
    except Exception:
        pass

    if not shutil.which("tesseract"):
        return ""
    with tempfile.TemporaryDirectory() as tmp:
        out_base = Path(tmp) / "ocr"
        try:
            subprocess.run(
                ["tesseract", str(path), str(out_base), "-l", "eng"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return out_base.with_suffix(".txt").read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""
