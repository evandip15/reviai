import base64
from pathlib import Path


def _read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def _read_docx(path: Path) -> str:
    from docx import Document
    doc = Document(path)
    chunks = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            chunks.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(chunks)


def _read_odt(path: Path) -> str:
    from odf import text, teletype
    from odf.opendocument import load
    doc = load(path)
    paragraphs = doc.getElementsByType(text.P)
    return "\n".join(teletype.extractText(p).strip() for p in paragraphs if teletype.extractText(p).strip())


def _read_pptx(path: Path) -> str:
    from pptx import Presentation
    prs = Presentation(path)
    chunks = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text.strip():
                chunks.append(shape.text.strip())
    return "\n".join(chunks)


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages).strip()


def lire_fichier(path: Path):
    suffix = path.suffix.lower()

    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        mime = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }[suffix]
        return {
            "type": "image",
            "mime_type": mime,
            "base64": base64.b64encode(path.read_bytes()).decode("ascii")
        }

    if suffix == ".pdf":
        text = _read_pdf(path)
        if text.strip():
            return {"type": "text", "text": text}
        # For image/scanned PDFs, convert pages to images when PyMuPDF is available.
        try:
            import fitz
            doc = fitz.open(path)
            page_images = []
            for page in doc:
                pix = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
                page_images.append(base64.b64encode(pix.tobytes("jpeg")).decode("ascii"))
                if len(page_images) >= 8:
                    break
            if page_images:
                return {
                    "type": "image",
                    "mime_type": "image/jpeg",
                    "base64": page_images[0]
                }
        except Exception:
            pass
        return {"type": "text", "text": ""}

    if suffix == ".docx":
        return {"type": "text", "text": _read_docx(path)}
    if suffix == ".odt":
        return {"type": "text", "text": _read_odt(path)}
    if suffix == ".pptx":
        return {"type": "text", "text": _read_pptx(path)}
    if suffix == ".txt":
        return {"type": "text", "text": _read_text_file(path)}
    if suffix == ".doc":
        raise RuntimeError("Les anciens fichiers .doc ne sont pas supportés sur le serveur. Enregistre-les en .docx.")

    raise RuntimeError(f"Format non supporté : {suffix}")
