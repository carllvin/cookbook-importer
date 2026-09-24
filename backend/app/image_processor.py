from __future__ import annotations

import logging
import os
import uuid

from PIL import Image, ImageOps
import pillow_heif

from .ocr import ocr_image

log = logging.getLogger("tandoor-helper")

pillow_heif.register_heif_opener()  # lets Pillow open .heic/.heif via Image.open()

SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif"}


def process_images(image_paths: list[str], images_dir: str) -> dict:
    """
    Treats a set of directly-uploaded photos (e.g. phone photos of cookbook
    pages) as one "document": each image becomes one page (OCR'd with
    Tesseract) AND is saved as a candidate recipe image for that page, since a
    photo of a recipe page often doubles as a perfectly good recipe photo.
    Returns the same shape as pdf_processor.process_pdf / epub_processor.process_epub.
    """
    os.makedirs(images_dir, exist_ok=True)

    pages = []
    images: dict[str, dict] = {}

    for index, path in enumerate(image_paths, start=1):
        try:
            img = Image.open(path)
            img = ImageOps.exif_transpose(img)  # respect phone-camera rotation metadata
            img = img.convert("RGB")
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not open image %s (%s) - skipping this page", path, exc)
            pages.append({"page": index, "text": ""})
            continue

        text = ocr_image(img)
        pages.append({"page": index, "text": text})

        image_id = uuid.uuid4().hex[:12]
        filename = f"{image_id}.jpg"
        out_path = os.path.join(images_dir, filename)
        img.save(out_path, "JPEG", quality=90)

        images[image_id] = {
            "page": index,
            "path": out_path,
            "filename": filename,
            "width": img.width,
            "height": img.height,
        }

    if pages:
        log.info("OCR used for %d uploaded photo(s)", len(pages))

    return {
        "pages": pages,
        "images": images,
        "page_count": len(pages),
        "metadata_title": "",
        "toc_pages": [],
    }
