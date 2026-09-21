from __future__ import annotations

import logging

from PIL import Image
import pytesseract

from .config import settings

log = logging.getLogger("cookbook-importer")


def ocr_image(img: "Image.Image") -> str:
    """Runs Tesseract OCR on a PIL image using the configured language(s)
    (OCR_LANGUAGES, e.g. "eng+deu"). Returns an empty string rather than
    raising if OCR fails or Tesseract isn't available, so callers can degrade
    gracefully (keep whatever text was already there, or none)."""
    try:
        return pytesseract.image_to_string(img, lang=settings.ocr_languages)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "OCR failed (%s) - continuing without OCR text. Is Tesseract installed "
            "with the language pack(s) for OCR_LANGUAGES=%s?",
            exc, settings.ocr_languages,
        )
        return ""
