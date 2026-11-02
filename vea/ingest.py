"""Parse proposals (PDF / DOCX / PPTX / XLSX / CSV / TXT) into page-tagged text, then split into chunks.

"Page" means: a PDF page, a PowerPoint slide, an Excel sheet, or (for DOCX) a ~3000-character section.
"""
import re
from dataclasses import dataclass
from pathlib import Path

SUPPORTED = {".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".txt"}


@dataclass
class Chunk:
    vendor: str
    page: int
    text: str
    source: str = ""        # file the text came from (a vendor can have several files)


def _read_pdf(path: Path):
    import fitz  # PyMuPDF
    pages = []
    with fitz.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            pages.append((i, page.get_text("text")))
    if sum(len(t.strip()) for _, t in pages) < 200:
        print(f"  WARNING: {path.name} has almost no extractable text (scanned PDF?). Run OCR first.")
    return pages


def _read_docx(path: Path):
    import docx
    d = docx.Document(path)
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for t in d.tables:
        for row in t.rows:
            parts.append(" | ".join(c.text.strip() for c in row.cells))
    # DOCX has no fixed pages: group ~3000 chars as a pseudo-page ("section")
    pages, buf, n = [], "", 1
    for p in parts:
        buf += p + "\n"
        if len(buf) > 3000:
            pages.append((n, buf)); buf, n = "", n + 1
    if buf.strip():
        pages.append((n, buf))
    return pages


def _pptx_shape_text(shape):
    """Text from one shape: groups are searched recursively, tables become 'a | b | c' rows."""
    out = []
    if getattr(shape, "shapes", None) is not None:          # group shape
        for s in shape.shapes:
            out += _pptx_shape_text(s)
    elif getattr(shape, "has_table", False) and shape.has_table:
        for row in shape.table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                out.append(" | ".join(cells))
    elif getattr(shape, "has_text_frame", False) and shape.has_text_frame:
        t = shape.text_frame.text.strip()
        if t:
            out.append(t)
    return out


def _read_pptx(path: Path):
    """One slide = one page. Includes tables and speaker notes. Images and charts are not read."""
    from pptx import Presentation
    prs = Presentation(path)
    pages = []
    for i, slide in enumerate(prs.slides, start=1):
        parts = []
        for shape in slide.shapes:
            parts += _pptx_shape_text(shape)
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parts.append("Speaker notes: " + notes)
        if parts:
            pages.append((i, f"[Slide {i}]\n\n" + "\n\n".join(parts)))
    if not pages:
        print(f"  WARNING: {path.name} has no extractable text (images only?).")
    return pages


def _read_xlsx(path: Path):
    """One sheet = one page. Each non-empty row becomes 'cell | cell | cell'.
    Formulas show their last saved value (open and save the file in Excel if values are missing)."""
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True, read_only=True)
    pages = []
    try:
        for n, ws in enumerate(wb.worksheets, start=1):
            rows = []
            for row in ws.iter_rows(values_only=True):
                cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if cells:
                    rows.append(" | ".join(cells))
            if rows:
                pages.append((n, f"[Sheet: {ws.title}]\n" + "\n".join(rows)))
    finally:
        wb.close()
    if not pages:
        print(f"  WARNING: {path.name} has no extractable text (empty workbook?).")
    return pages


def _read_csv(path: Path):
    """The whole file is one page. Each non-empty row becomes 'cell | cell | cell' (same as a spreadsheet)."""
    import csv
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")          # CSVs saved by Excel are often cp1252
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = []
    for row in csv.reader(text.splitlines(), dialect):
        cells = [c.strip() for c in row if c and c.strip()]
        if cells:
            rows.append(" | ".join(cells))
    if not rows:
        print(f"  WARNING: {path.name} has no extractable text (empty CSV?).")
        return []
    return [(1, "\n".join(rows))]


def _read_txt(path: Path):
    raw = path.read_text(encoding="utf-8", errors="ignore")
    pieces = re.split(r"^=== Page (\d+) ===\s*$", raw, flags=re.M)
    if len(pieces) == 1:
        return [(1, raw)]
    return [(int(pieces[i]), pieces[i + 1]) for i in range(1, len(pieces), 2)]


_READERS = {".pdf": _read_pdf, ".docx": _read_docx, ".pptx": _read_pptx,
            ".xlsx": _read_xlsx, ".csv": _read_csv, ".txt": _read_txt}


def read_pages(path: Path):
    """Returns a list of (page_number, text)."""
    ext = path.suffix.lower()
    if ext in (".ppt", ".doc", ".xls"):
        raise ValueError(f"{path.name}: old Office format. Open it in Office and 'Save As' "
                         f"a {ext}x file first.")
    if ext not in _READERS:
        raise ValueError(f"Unsupported file type: {path.name}")
    return _READERS[ext](path)


def _split_long(p, max_chars):
    """Split an oversized paragraph at line breaks (or spaces) so no piece is longer than max_chars."""
    if len(p) <= max_chars:
        return [p]
    parts, buf = [], ""
    for line in p.split("\n"):
        line = line.strip()
        while len(line) > max_chars:
            cut = line.rfind(" ", 0, max_chars)
            cut = cut if cut > max_chars // 2 else max_chars
            if buf.strip():
                parts.append(buf.strip()); buf = ""
            parts.append(line[:cut].strip())
            line = line[cut:].strip()
        if buf and len(buf) + len(line) + 1 > max_chars:
            parts.append(buf.strip()); buf = ""
        if line:
            buf += line + "\n"
    if buf.strip():
        parts.append(buf.strip())
    return parts


def chunk_pages(vendor, pages, max_chars=900, overlap_chars=150, source=""):
    chunks = []
    for page_no, text in pages:
        raw = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        paras = [piece for p in raw for piece in _split_long(p, max_chars)]
        buf = ""
        for p in paras:
            if buf and len(buf) + len(p) > max_chars:
                chunks.append(Chunk(vendor, page_no, buf.strip(), source))
                buf = buf[-overlap_chars:] + "\n" if overlap_chars else ""
            buf += p + "\n"
        if buf.strip():
            chunks.append(Chunk(vendor, page_no, buf.strip(), source))
    return chunks


def vendor_name(text):
    return text.replace("_", " ").strip()


def ingest_vendor(path: Path, max_chars=900, overlap_chars=150):
    """One file -> (vendor name from the file name, chunks). Used for the RFP and for --per-file runs."""
    vendor = vendor_name(path.stem)
    return vendor, chunk_pages(vendor, read_pages(path), max_chars, overlap_chars, source=path.name)


def ingest_files(vendor, paths, max_chars=900, overlap_chars=150):
    """Several files (any mix of formats) -> one list of chunks, each tagged with its source file."""
    chunks = []
    for p in paths:
        pages = read_pages(p)
        got = chunk_pages(vendor, pages, max_chars, overlap_chars, source=p.name)
        print(f"    {p.name}: {len(pages)} page/slide/sheet(s), {len(got)} chunks")
        chunks += got
    return chunks


def group_vendors(folder, per_file=False):
    """Find proposals and group them by vendor. Returns {vendor name: [file paths]}.

    - a sub-folder = one vendor (every supported file inside it belongs to that vendor)
    - a loose file named 'Vendor__anything.ext' belongs to vendor 'Vendor' (same prefix = same vendor)
    - any other loose file is its own vendor (name = file name)
    - per_file=True: every file is its own vendor (to compare how formats are read)
    """
    folder = Path(folder)

    def ok(p):
        return p.is_file() and p.suffix.lower() in SUPPORTED and not p.name.startswith(("~$", "."))

    groups = {}
    def add(name, path):
        key = vendor_name(name)
        match = next((k for k in groups if k.lower() == key.lower()), key)
        groups.setdefault(match, []).append(path)

    for p in sorted(folder.iterdir()):
        if p.is_dir() and not p.name.startswith("."):
            for f in sorted(p.rglob("*")):
                if ok(f):
                    add(f.stem if per_file else p.name, f)
        elif ok(p):
            add(p.stem if per_file or "__" not in p.stem else p.stem.split("__")[0], p)
    return groups
