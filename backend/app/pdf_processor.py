from __future__ import annotations

import os
import uuid
import fitz  # PyMuPDF


MIN_IMAGE_SIDE_PX = 180  # ignore tiny images (icons, decoration)


def process_pdf(pdf_path: str, images_dir: str) -> dict:
    """
    Reads a PDF and returns:
      - pages: list of {"page": n, "text": "..."}
      - images: dict image_id -> {"page": n, "path": "...", "width": w, "height": h}
      - toc_pages: sorted list of page numbers where a bookmark/table-of-contents
        entry starts (empty if the PDF has no bookmarks). Used to align chunk
        boundaries with chapter/recipe boundaries instead of cutting mid-recipe.
      - metadata_title: the PDF's embedded title metadata, if any (used as a
        cookbook-name hint).
    Images are saved as JPEG/PNG files under images_dir.
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

    toc_pages = _extract_toc_pages(doc)
    doc.close()

    return {
        "pages": pages,
        "images": images,
        "page_count": len(pages),
        "metadata_title": metadata_title,
        "toc_pages": toc_pages,
    }


def _extract_toc_pages(doc: "fitz.Document") -> list[int]:
    """Returns the sorted, de-duplicated list of page numbers where a bookmark
    (table-of-contents entry) starts. Many cookbook PDFs have one bookmark per
    recipe or chapter; the extraction step uses these as natural chunk
    boundaries so a recipe is far less likely to be split across two chunks."""
    try:
        toc = doc.get_toc(simple=True)  # list of [level, title, page] (1-indexed)
    except Exception:
        return []

    pages = {entry[2] for entry in toc if len(entry) >= 3 and entry[2] >= 1}
    return sorted(pages)


def build_text_with_page_markers(pages: list[dict]) -> str:
    """Builds a single text blob with explicit page markers for the AI extraction step."""
    parts = []
    for p in pages:
        parts.append(f"\n\n===== PAGE {p['page']} =====\n{p['text']}")
    return "".join(parts)
