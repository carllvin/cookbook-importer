from __future__ import annotations

import os
import uuid
import fitz  # PyMuPDF


MIN_IMAGE_SIDE_PX = 180  # zu kleine Bilder (Icons, Deko) ignorieren


def process_pdf(pdf_path: str, images_dir: str) -> dict:
    """
    Liest ein PDF ein und liefert:
      - pages: Liste von {"page": n, "text": "..."}
      - images: dict image_id -> {"page": n, "path": "...", "width": w, "height": h}
    Bilder werden als JPEG/PNG-Dateien unter images_dir gespeichert.
    """
    os.makedirs(images_dir, exist_ok=True)
    doc = fitz.open(pdf_path)

    pages = []
    images: dict[str, dict] = {}
    metadata_title = (doc.metadata or {}).get("title", "").strip()

    for page_index in range(len(doc)):
        page = doc[page_index]
        page_number = page_index + 1

        text = page.get_text("text")
        pages.append({"page": page_number, "text": text})

        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                base_image = doc.extract_image(xref)
            except Exception:
                continue

            width = base_image.get("width", 0)
            height = base_image.get("height", 0)
            if width < MIN_IMAGE_SIDE_PX or height < MIN_IMAGE_SIDE_PX:
                continue

            ext = base_image.get("ext", "png")
            image_id = uuid.uuid4().hex[:12]
            filename = f"{image_id}.{ext}"
            out_path = os.path.join(images_dir, filename)
            with open(out_path, "wb") as f:
                f.write(base_image["image"])

            images[image_id] = {
                "page": page_number,
                "path": out_path,
                "filename": filename,
                "width": width,
                "height": height,
            }

    doc.close()
    return {
        "pages": pages,
        "images": images,
        "page_count": len(pages),
        "metadata_title": metadata_title,
    }


def build_text_with_page_markers(pages: list[dict]) -> str:
    """Baut einen einzigen Text mit expliziten Seitenmarkierungen für die Claude-Extraktion."""
    parts = []
    for p in pages:
        parts.append(f"\n\n===== SEITE {p['page']} =====\n{p['text']}")
    return "".join(parts)
