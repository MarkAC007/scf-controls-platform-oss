# SPDX-License-Identifier: AGPL-3.0-only
"""Readers for evidence files: bytes in, plain structures out.

Two callers need the inside of a Word, Excel, PowerPoint, ZIP or email file:
the text extractor that feeds the AI assessment, and the preview endpoint the
viewer draws from. They share these readers so that what a person is shown
and what the model is given come from the same reading of the file.

Every reader takes bytes and works in memory. Nothing is written to disk, so
a member name such as ``../../etc/passwd`` is only ever a string.

Every file here is hostile until read. A 50 MB upload is small; what it
unpacks to need not be. Word, Excel and PowerPoint files are ZIP archives, so
they get the same check as a ZIP (``open_archive``), and no parser is handed
the uploaded container: it is handed a copy made by reads of a bounded size
(``safe_office_container``), because the sizes an archive declares are not
the sizes its streams inflate to. Readers raise ``EvidenceReadError`` with a
sentence a user can be shown; nothing else is expected to escape.
"""
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Optional

from services import evidence_file_types


class EvidenceReadError(Exception):
    """A file could not be read. The message is safe to show to a user."""


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

#: A ZIP uploaded as evidence: most files it may hold.
ZIP_MAX_MEMBERS = 500
#: A ZIP uploaded as evidence: most it may unpack to, in total.
ZIP_MAX_UNPACKED_BYTES = 200 * 1024 * 1024
#: A member larger than this is listed and not read.
ZIP_MAX_MEMBER_BYTES = 25 * 1024 * 1024

#: Word, Excel and PowerPoint containers. These hold many small parts.
OFFICE_MAX_MEMBERS = 5000
OFFICE_MAX_UNPACKED_BYTES = 200 * 1024 * 1024
#: One XML part. The parsers build a tree several times this size in memory.
OFFICE_MAX_XML_PART_BYTES = 60 * 1024 * 1024

#: Pictures: refuse before decoding anything larger (width x height).
IMAGE_MAX_INPUT_PIXELS = 80_000_000
#: Pictures are scaled so neither side is longer than this before they are
#: sent to the model. 2000 px is also the per-side limit the API applies once
#: a request carries more than 20 pictures.
IMAGE_MAX_EDGE = 2000
#: Encoded size ceiling per picture. Two limits sit above it: one picture may
#: not be more than 5 MB once base64 has added its third, and the whole request
#: has a size limit that every picture of a file counts towards. The ceiling is
#: set so the most pictures one file may send
#: (``text_extraction_service.MAX_IMAGES_PER_FILE``) stay well inside both; a
#: test holds the two numbers together. A screenshot at 2000 px is usually
#: well under this as PNG; a photograph goes as JPEG.
IMAGE_MAX_ENCODED_BYTES = 1_500_000
_IMAGE_FORMATS = ("PNG", "JPEG", "GIF", "WEBP")


# ---------------------------------------------------------------------------
# Archives (ZIP, and the ZIP containers Office files are)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContainerLimits:
    """How much of an Office file a caller is prepared to unpack and parse."""

    max_members: int
    max_unpacked: int
    max_xml_part: int
    #: Words for the refusal: "too large to read", "the most that will be read".
    verb: str = "read"
    participle: str = "read"
    #: Spreadsheet rows examined before giving up; None leaves the module's own cap.
    max_rows_scanned: Optional[int] = None


#: For the assessment worker: a job of its own, off the request path.
OFFICE_LIMITS = ContainerLimits(
    max_members=OFFICE_MAX_MEMBERS,
    max_unpacked=OFFICE_MAX_UNPACKED_BYTES,
    max_xml_part=OFFICE_MAX_XML_PART_BYTES,
)
#: For the viewer's preview, which is built inside the API process while the
#: person waits. Parsing Word XML costs between a quarter of a second and a
#: second of CPU per megabyte, and tens of megabytes of memory, so the
#: worker's limits would let one small crafted file hold a request thread for
#: most of a minute. A document past these limits is still read by the
#: assessment and can still be downloaded.
PREVIEW_OFFICE_LIMITS = ContainerLimits(
    max_members=2000,
    max_unpacked=48 * 1024 * 1024,
    max_xml_part=4 * 1024 * 1024,
    verb="preview",
    participle="previewed",
    max_rows_scanned=20_000,
)

#: The only ways a member may be compressed. ``zipfile`` can bound how much
#: one read of a deflate stream inflates; for bzip2 and LZMA it cannot, so a
#: member of a few hundred bytes could be made to fill memory.
_BOUNDED_COMPRESSION = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
#: Bytes asked for at a time when a container is copied.
_COPY_CHUNK = 1024 * 1024
#: An entity declaration, in each encoding the spreadsheet parser can read.
#: (Its other encodings are single-byte and keep ASCII where ASCII is.)
_ENTITY_MARKERS = tuple("<!ENTITY".encode(codec) for codec in ("ascii", "utf-16-le", "utf-16-be"))


def open_archive(
    data: bytes,
    *,
    what: str,
    max_members: int,
    max_unpacked: int,
    max_xml_part: Optional[int] = None,
    verb: str = "read",
    participle: str = "read",
) -> zipfile.ZipFile:
    """Open a ZIP container after checking what it claims to unpack to.

    The sizes checked are the ones the archive declares. ``zipfile`` never
    hands back more than a member's declared size, so the declared total is a
    ceiling on what a reader is *given*. It is not a ceiling on the memory
    used to get there: an unbounded ``read()`` inflates the whole stream first
    and cuts it down afterwards. So everything past this check reads members
    in bounded pieces (``read_zip_member``, ``safe_office_container``), and
    nothing here calls ``ZipFile.read`` on an uploaded archive. An archive of
    overlapping members (the "better zip bomb") declares its full expanded
    size and is refused here.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, OSError) as exc:
        raise EvidenceReadError(f"This {what} could not be opened: it is not a valid archive.") from exc

    if len(infos) > max_members:
        zf.close()
        raise EvidenceReadError(
            f"This {what} holds {len(infos):,} files; the most that will be {participle} is {max_members:,}."
        )
    total = sum(max(0, i.file_size) for i in infos)
    if total > max_unpacked:
        zf.close()
        raise EvidenceReadError(
            f"This {what} unpacks to {_human_size(total)}; the most that will be {participle} is "
            f"{_human_size(max_unpacked)}."
        )
    if max_xml_part is not None:
        for i in infos:
            if i.file_size > max_xml_part and i.filename.lower().endswith((".xml", ".rels")):
                zf.close()
                raise EvidenceReadError(
                    f"This {what} has a part that unpacks to {_human_size(i.file_size)}, "
                    f"which is too large to {verb}."
                )
    return zf


def _open_office(data: bytes, what: str, limits: Optional[ContainerLimits] = None) -> zipfile.ZipFile:
    limits = limits or OFFICE_LIMITS
    return open_archive(
        data,
        what=what,
        max_members=limits.max_members,
        max_unpacked=limits.max_unpacked,
        max_xml_part=limits.max_xml_part,
        verb=limits.verb,
        participle=limits.participle,
    )


def check_office_container(data: bytes, what: str, limits: Optional[ContainerLimits] = None) -> None:
    """Refuse an oversized Word, Excel or PowerPoint file before parsing it."""
    _open_office(data, what, limits).close()


def _could_be_xml(start: bytes) -> bool:
    """Whether a part with this beginning could be parsed as XML.

    Judged from the bytes and not the name: Office finds its parts through
    relationships, so a worksheet may be called ``image1.png``. An XML
    document begins with ``<`` after at most a byte-order mark and white
    space. A beginning that is nothing but those is counted as XML too.
    """
    return start.lstrip(b"\xef\xbb\xbf\xff\xfe\x00 \t\r\n")[:1] in (b"<", b"")


def safe_office_container(
    data: bytes,
    what: str,
    limits: Optional[ContainerLimits] = None,
    *,
    refuse_entities: bool = False,
) -> bytes:
    """A copy of a Word, Excel or PowerPoint file that is safe to hand to a parser.

    The uploaded container is checked (``open_archive``) and then copied part
    by part, each part read in pieces of a fixed size. A bounded read stops at
    the size the part declares and never inflates more than it was asked for,
    so the copy holds exactly what was declared and no parser can be made to
    inflate more. While copying:

    - a part compressed with anything but the standard ZIP method is refused;
    - a part that could be parsed as XML is held to ``max_xml_part`` whatever
      it is called;
    - with ``refuse_entities``, a part that declares XML entities is refused.
      Office never writes them. The spreadsheet parser expands them, and a
      few kilobytes can be made to expand to gigabytes. (Word and PowerPoint
      parts go through a parser that has expansion switched off.)
    """
    limits = limits or OFFICE_LIMITS
    source = _open_office(data, what, limits)
    copy = io.BytesIO()
    try:
        with zipfile.ZipFile(copy, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as target:
            # Where a name is repeated the last entry wins, as it does for a
            # reader that asks for a part by name.
            for info in {i.filename: i for i in source.infolist()}.values():
                if info.is_dir():
                    continue
                if info.compress_type not in _BOUNDED_COMPRESSION:
                    raise EvidenceReadError(
                        f"This {what} is packed with a compression method Office does not use; it is not {limits.participle}."
                    )
                with source.open(info) as part, target.open(info.filename, "w") as sink:
                    left = max(0, info.file_size)
                    xml = None
                    overlap = b""
                    while left > 0:
                        chunk = part.read(min(_COPY_CHUNK, left))
                        if not chunk:
                            break
                        left -= len(chunk)
                        if xml is None:
                            xml = _could_be_xml(chunk)
                            if xml and info.file_size > limits.max_xml_part:
                                raise EvidenceReadError(
                                    f"This {what} has a part that unpacks to {_human_size(info.file_size)}, "
                                    f"which is too large to {limits.verb}."
                                )
                        if xml and refuse_entities:
                            window = overlap + chunk
                            if any(marker in window for marker in _ENTITY_MARKERS):
                                raise EvidenceReadError(
                                    f"This {what} has a part that defines its own XML entities. Office does "
                                    f"not write these and they are not {limits.participle}. Open the file in "
                                    "its own application and save a fresh copy."
                                )
                            overlap = window[-32:]
                        sink.write(chunk)
    except EvidenceReadError:
        raise
    except Exception as exc:  # noqa: BLE001 — bad CRC, encrypted part, truncated stream
        raise EvidenceReadError(f"This {what} could not be unpacked: {str(exc)[:200]}") from exc
    finally:
        source.close()
    return copy.getvalue()


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} bytes"


# ---------------------------------------------------------------------------
# Word
# ---------------------------------------------------------------------------

DOCX_MAX_TABLE_DEPTH = 3


def _docx_body_text(doc) -> list:
    """Paragraphs and table rows of a Word document, in reading order.

    A table row becomes one line, its cells separated by tabs, so an access
    matrix or a sign-off table kept in Word is read and not skipped. A merged
    cell is reported once. Tables inside cells are followed a few levels deep.
    """
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    lines: list = []

    def cell_text(cell, depth: int) -> str:
        parts = []
        for child in cell._tc.iterchildren():
            if child.tag.endswith("}p"):
                text = Paragraph(child, cell).text.strip()
                if text:
                    parts.append(text)
            elif child.tag.endswith("}tbl") and depth < DOCX_MAX_TABLE_DEPTH:
                for row in table_rows(Table(child, cell), depth + 1):
                    parts.append(row.replace("\t", " | "))
        return " ".join(parts)

    def table_rows(table, depth: int) -> list:
        rows = []
        for row in table.rows:
            cells, seen = [], set()
            for cell in row.cells:
                if id(cell._tc) in seen:
                    continue
                seen.add(id(cell._tc))
                cells.append(cell_text(cell, depth))
            if any(cells):
                rows.append("\t".join(cells))
        return rows

    for child in doc.element.body.iterchildren():
        if child.tag.endswith("}p"):
            text = Paragraph(child, doc).text
            if text.strip():
                lines.append(text)
        elif child.tag.endswith("}tbl"):
            lines.extend(table_rows(Table(child, doc), 1))
    return lines

def read_docx_paragraphs(data: bytes, limits: Optional[ContainerLimits] = None) -> list:
    """The text of a Word document, in order: paragraphs, and table rows as lines."""
    safe = safe_office_container(data, "Word document", limits)
    from docx import Document

    try:
        doc = Document(io.BytesIO(safe))
        return _docx_body_text(doc)
    except EvidenceReadError:
        raise
    except Exception as exc:  # noqa: BLE001 — python-docx raises many types on a bad file
        raise EvidenceReadError(f"This Word document could not be read: {str(exc)[:200]}") from exc


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------

@dataclass
class SheetData:
    name: str
    rows: list = field(default_factory=list)   # list of list of str
    truncated: bool = False


#: Rows examined across a whole workbook before giving up. Bounds the time a
#: workbook with millions of rows can hold a worker, whatever the text budget.
XLSX_MAX_ROWS_SCANNED = 200_000
XLSX_MAX_COLUMNS = 200


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, datetime):
        if value.time() == time(0, 0):
            return value.date().isoformat()
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, (date, time)):
        return value.isoformat()
    return str(value).strip()


def read_xlsx_sheets(
    data: bytes,
    *,
    max_chars: Optional[int] = None,
    max_rows_per_sheet: Optional[int] = None,
    max_columns: int = XLSX_MAX_COLUMNS,
    max_sheets: Optional[int] = None,
    limits: Optional[ContainerLimits] = None,
) -> list:
    """The cell values of every worksheet, as text.

    Values, not formulas: a formula cell gives the result Excel last saved.
    Empty rows are dropped and trailing empty cells trimmed. Reading stops,
    and the sheet is marked ``truncated``, when ``max_chars`` of cell text has
    been collected, when a sheet reaches ``max_rows_per_sheet``, or when
    ``XLSX_MAX_ROWS_SCANNED`` rows (fewer, if ``limits`` says so) have been
    examined.
    """
    safe = safe_office_container(data, "Excel workbook", limits, refuse_entities=True)
    from openpyxl import load_workbook

    row_cap = XLSX_MAX_ROWS_SCANNED
    if limits is not None and limits.max_rows_scanned is not None:
        row_cap = min(row_cap, limits.max_rows_scanned)
    try:
        wb = load_workbook(io.BytesIO(safe), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 — openpyxl raises many types on a bad file
        raise EvidenceReadError(f"This Excel workbook could not be read: {str(exc)[:200]}") from exc

    sheets: list = []
    chars = 0
    scanned = 0
    stop = False
    try:
        for index, ws in enumerate(wb.worksheets):
            if max_sheets is not None and index >= max_sheets:
                break
            sheet = SheetData(name=str(ws.title))
            sheets.append(sheet)
            if stop:
                sheet.truncated = True
                continue
            # A workbook can declare a used range it does not have (a million
            # rows, sixteen thousand columns). Read what is there instead.
            if hasattr(ws, "reset_dimensions"):
                ws.reset_dimensions()
            for row in ws.iter_rows(values_only=True):
                scanned += 1
                if scanned > row_cap:
                    sheet.truncated = True
                    stop = True
                    break
                cells = [_cell_text(v) for v in row[:max_columns]]
                while cells and not cells[-1]:
                    cells.pop()
                if not cells:
                    continue
                if max_rows_per_sheet is not None and len(sheet.rows) >= max_rows_per_sheet:
                    sheet.truncated = True
                    break
                sheet.rows.append(cells)
                chars += sum(len(c) for c in cells) + len(cells)
                if max_chars is not None and chars > max_chars:
                    sheet.truncated = True
                    stop = True
                    break
    except EvidenceReadError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise EvidenceReadError(f"This Excel workbook could not be read: {str(exc)[:200]}") from exc
    finally:
        wb.close()
    return sheets


# ---------------------------------------------------------------------------
# PowerPoint
# ---------------------------------------------------------------------------

@dataclass
class SlideData:
    number: int
    paragraphs: list = field(default_factory=list)


_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
PPTX_MAX_SLIDES = 500


def _xml(raw: bytes):
    """Parse an XML part with nothing that reaches outside it.

    No entity expansion, no DTD, no network: a slide is data, and an entity
    is how an XML file reads a local file or grows without limit.
    """
    from lxml import etree

    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False,
        dtd_validation=False, huge_tree=False, recover=False,
    )
    return etree.fromstring(raw, parser=parser)


def _slide_order(zf: zipfile.ZipFile, names: set) -> list:
    """Slide part names in presentation order, falling back to file number."""
    def by_number(name: str) -> int:
        match = re.search(r"slide(\d+)\.xml$", name)
        return int(match.group(1)) if match else 0

    slide_parts = sorted(
        (n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=by_number,
    )
    try:
        rels = _xml(zf.read("ppt/_rels/presentation.xml.rels"))
        targets = {
            rel.get("Id"): rel.get("Target", "")
            for rel in rels.iter(f"{{{_REL_NS}}}Relationship")
        }
        pres = _xml(zf.read("ppt/presentation.xml"))
        ordered = []
        for sld in pres.iter(f"{{{_P_NS}}}sldId"):
            target = targets.get(sld.get(f"{{{_R_NS}}}id"), "")
            name = "ppt/" + target.lstrip("/").removeprefix("ppt/")
            if name in names and name not in ordered:
                ordered.append(name)
        if ordered:
            return ordered
    except Exception:  # noqa: BLE001 — order is a nicety; the text is the point
        pass
    return slide_parts


def read_pptx_slides(data: bytes, limits: Optional[ContainerLimits] = None) -> list:
    """The text of each slide of a PowerPoint presentation, in order."""
    safe = safe_office_container(data, "PowerPoint presentation", limits)
    zf = zipfile.ZipFile(io.BytesIO(safe))
    try:
        names = set(zf.namelist())
        order = _slide_order(zf, names)
        if not order:
            raise EvidenceReadError("This PowerPoint presentation has no slides that could be read.")
        slides = []
        for number, name in enumerate(order[:PPTX_MAX_SLIDES], start=1):
            root = _xml(zf.read(name))
            paragraphs = []
            for para in root.iter(f"{{{_A_NS}}}p"):
                text = "".join(t.text or "" for t in para.iter(f"{{{_A_NS}}}t")).strip()
                if text:
                    paragraphs.append(text)
            slides.append(SlideData(number=number, paragraphs=paragraphs))
        return slides
    except EvidenceReadError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise EvidenceReadError(
            f"This PowerPoint presentation could not be read: {str(exc)[:200]}"
        ) from exc
    finally:
        zf.close()


# ---------------------------------------------------------------------------
# ZIP evidence
# ---------------------------------------------------------------------------

@dataclass
class ArchiveMember:
    name: str
    size_bytes: int
    content_type: Optional[str] = None   # None when the platform does not read this kind
    label: Optional[str] = None
    readable: bool = False
    note: Optional[str] = None
    _info: Optional[zipfile.ZipInfo] = field(default=None, repr=False, compare=False)


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _display_name(raw: str) -> str:
    return _CONTROL_CHARS.sub("", raw)[:300]


def open_evidence_zip(data: bytes) -> tuple:
    """Open a ZIP uploaded as evidence and describe what is inside.

    Returns ``(zipfile, members)``; the caller closes the zipfile. Folders are
    left out. Each file is marked readable only when it is a kind the
    platform already reads on its own. An archive inside the archive is
    listed and never opened: one level is what a person can check by eye, and
    nesting is how an archive hides its size.
    """
    zf = open_archive(
        data,
        what="ZIP archive",
        max_members=ZIP_MAX_MEMBERS,
        max_unpacked=ZIP_MAX_UNPACKED_BYTES,
    )
    members = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = _display_name(info.filename)
        base = name.rsplit("/", 1)[-1]
        member = ArchiveMember(name=name, size_bytes=max(0, info.file_size), _info=info)
        kind = evidence_file_types.type_for_filename(base)
        if name.startswith("__MACOSX/") or base.startswith("._") or base == ".DS_Store":
            member.note = "macOS metadata, not read"
        elif kind is not None and kind.ai == evidence_file_types.AI_ARCHIVE:
            member.label = kind.label
            member.note = "an archive inside the archive, not opened"
        elif kind is None:
            member.note = "not a file type the platform reads"
        elif info.flag_bits & 0x1:
            member.label = kind.label
            member.note = "password protected, not read"
        elif info.compress_type not in _BOUNDED_COMPRESSION:
            member.label = kind.label
            member.note = "packed with a compression method that is not read"
        elif info.file_size > ZIP_MAX_MEMBER_BYTES:
            member.label = kind.label
            member.note = f"larger than {_human_size(ZIP_MAX_MEMBER_BYTES)}, not read"
        else:
            member.content_type = kind.content_type
            member.label = kind.label
            member.readable = True
        members.append(member)
    return zf, members


def read_zip_member(zf: zipfile.ZipFile, member: ArchiveMember) -> bytes:
    """The bytes of one readable member, never more than the member cap."""
    if not member.readable or member._info is None or member._info.compress_type not in _BOUNDED_COMPRESSION:
        raise EvidenceReadError(f"{member.name} is not read.")
    try:
        with zf.open(member._info) as handle:
            blob = handle.read(ZIP_MAX_MEMBER_BYTES + 1)
    except Exception as exc:  # noqa: BLE001 — bad CRC, unsupported compression, truncated stream
        raise EvidenceReadError(f"{member.name} could not be unpacked: {str(exc)[:200]}") from exc
    if len(blob) > ZIP_MAX_MEMBER_BYTES:
        raise EvidenceReadError(f"{member.name} is larger than it declares; not read.")
    return blob


# ---------------------------------------------------------------------------
# Email (.eml)
# ---------------------------------------------------------------------------

@dataclass
class EmailData:
    headers: dict = field(default_factory=dict)       # from, to, cc, subject, date
    body: str = ""
    attachments: list = field(default_factory=list)   # dicts: name, content_type, size_bytes


def _html_to_text(html: str) -> str:
    from html.parser import HTMLParser

    class _Text(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []
            self._skip = 0

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "head"):
                self._skip += 1
            elif tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4"):
                self.parts.append("\n")

        def handle_endtag(self, tag):
            if tag in ("script", "style", "head") and self._skip:
                self._skip -= 1

        def handle_data(self, data):
            if not self._skip:
                self.parts.append(data)

    parser = _Text()
    parser.feed(html)
    text = "".join(parser.parts)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+\n", "\n", text)).strip()


def _part_text(part) -> str:
    try:
        content = part.get_content()
    except Exception:  # noqa: BLE001 — unknown charset or transfer encoding
        raw = part.get_payload(decode=True) or b""
        content = raw.decode("utf-8", errors="replace")
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    return str(content)


#: The headers shown to a person and given to the model.
_EML_SHOWN_HEADERS = ("from", "to", "cc", "subject", "date")
#: A shown header longer than this is shown as it is written, cut short, and
#: is not handed to the header parser.
EML_MAX_PARSED_HEADER_CHARS = 8 * 1024
#: A header that says how a part is built (its name starts ``Content-``)
#: longer than this stops the message being read. Those are the headers the
#: parser must read to find the body; real ones are a few hundred characters.
EML_MAX_STRUCTURE_HEADER_CHARS = 2 * 1024
#: Most parts (bodies, attachments, containers) a message may have.
EML_MAX_PARTS = 100


def _check_eml(data: bytes) -> dict:
    """Refuse a message the full parser cannot be trusted with.

    The standard library's modern header parser takes time and memory that
    grow with the square of a header's length: one 400 KB Subject line costs
    gigabytes. So the message is first read with the legacy parser, which
    keeps headers as plain strings, and every header the full parser would
    have to read is measured. Returns the shown headers that are too long to
    parse, as written, for the caller to show cut short.
    """
    import email
    from email import policy

    light = email.message_from_bytes(data, policy=policy.compat32)
    too_long: dict = {}
    parts = 0
    for part in light.walk():
        parts += 1
        if parts > EML_MAX_PARTS:
            raise EvidenceReadError(
                f"This email message has more than {EML_MAX_PARTS} parts; it is not read."
            )
        for name, value in part.raw_items():
            key, length = name.lower(), len(str(value))
            if key.startswith("content-") and length > EML_MAX_STRUCTURE_HEADER_CHARS:
                raise EvidenceReadError(
                    f"This email message has a {name} header of {_human_size(length)}, "
                    "which is too long to read."
                )
            if part is light and key in _EML_SHOWN_HEADERS and length > EML_MAX_PARSED_HEADER_CHARS:
                too_long.setdefault(key, str(value))
    return too_long


def _shortened_header(raw: str) -> str:
    """The start of a header too long to parse, decoded where that is cheap."""
    from email.header import decode_header, make_header

    start = " ".join(raw[:4000].split())
    if len(raw) > 4000 and " " in start:
        start = start.rsplit(" ", 1)[0]     # do not cut an encoded word in half
    try:
        start = str(make_header(decode_header(start)))
    except Exception:  # noqa: BLE001 — shown as written
        pass
    return _display_name(start)[:1000] + " … (shortened)"


def read_eml(data: bytes) -> EmailData:
    """Headers, body text and the attachment list of an email message.

    The body is the plain-text part when there is one, otherwise the HTML
    part with its markup removed. Attachments are listed and not opened.
    """
    import email
    from email import policy

    try:
        too_long = _check_eml(data)
        msg = email.message_from_bytes(data, policy=policy.default)
        result = EmailData()
        for key in _EML_SHOWN_HEADERS:
            if key in too_long:
                result.headers[key] = _shortened_header(too_long[key])
                continue
            value = msg.get(key)
            if value:
                result.headers[key] = _display_name(str(value))[:1000]

        body = msg.get_body(preferencelist=("plain", "html"))
        if body is not None:
            text = _part_text(body)
            if body.get_content_type() == "text/html":
                text = _html_to_text(text)
            result.body = text.strip()

        for part in msg.iter_attachments():
            payload = part.get_payload(decode=True)
            result.attachments.append({
                "name": _display_name(part.get_filename() or "(unnamed)"),
                "content_type": part.get_content_type(),
                "size_bytes": len(payload) if isinstance(payload, bytes) else 0,
            })
    except EvidenceReadError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise EvidenceReadError(f"This email message could not be read: {str(exc)[:200]}") from exc

    # The parser accepts any bytes at all as a message with no headers. A file
    # with none of From, To, Cc, Subject or Date is not an email worth judging.
    if not result.headers:
        raise EvidenceReadError("This file does not look like an email message: it has no message headers.")
    return result


# ---------------------------------------------------------------------------
# Pictures
# ---------------------------------------------------------------------------

@dataclass
class ImagePart:
    """One picture, ready to be sent to the model."""

    media_type: str
    data: bytes
    label: str
    width: int = 0
    height: int = 0


def image_size(data: bytes) -> tuple:
    """Width and height of a picture, read from its header without decoding it.

    For callers that only describe a picture and never send it. Refuses the
    same files ``prepare_image`` refuses up front.
    """
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data), formats=_IMAGE_FORMATS) as image:
            width, height = image.size
    except Exception as exc:  # noqa: BLE001 — Pillow raises many types on a bad file
        raise EvidenceReadError(
            "This picture could not be read: it is not a valid PNG, JPEG, GIF or WebP file."
        ) from exc
    if width <= 0 or height <= 0 or width * height > IMAGE_MAX_INPUT_PIXELS:
        raise EvidenceReadError(
            f"This picture is {width:,} x {height:,} pixels, which is too large to read."
        )
    return width, height


def prepare_image(data: bytes, label: str) -> ImagePart:
    """Decode a picture and re-encode it for the model.

    Re-encoding does three things at once. It proves the bytes are a picture
    of an accepted format and not something merely named like one. It scales
    a large picture down to what the model will actually look at. And it
    drops everything that is not pixels (camera location, device and author
    metadata), which the assessment has no use for and should not be sent.

    Only the first frame of an animation is kept; that is all the model reads.
    """
    from PIL import Image, ImageOps

    try:
        image = Image.open(io.BytesIO(data), formats=_IMAGE_FORMATS)
        width, height = image.size
    except Exception as exc:  # noqa: BLE001 — Pillow raises many types on a bad file
        raise EvidenceReadError(
            "This picture could not be read: it is not a valid PNG, JPEG, GIF or WebP file."
        ) from exc

    if width <= 0 or height <= 0 or width * height > IMAGE_MAX_INPUT_PIXELS:
        raise EvidenceReadError(
            f"This picture is {width:,} x {height:,} pixels, which is too large to read."
        )

    try:
        image.seek(0)
        if image.format == "JPEG":
            # Let the decoder scale a large photograph down as it reads it,
            # instead of decoding every pixel and shrinking afterwards.
            image.draft("RGB", (IMAGE_MAX_EDGE, IMAGE_MAX_EDGE))
        image = ImageOps.exif_transpose(image) or image
        if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
            # Flatten onto white: a transparent screenshot otherwise turns black.
            rgba = image if image.mode == "RGBA" else image.convert("RGBA")
            flat = Image.new("RGB", rgba.size, (255, 255, 255))
            flat.paste(rgba, mask=rgba.getchannel("A"))
            image = flat
            del rgba
        elif image.mode != "RGB":
            image = image.convert("RGB")

        edge = IMAGE_MAX_EDGE
        while True:
            # Each pass is no larger than the last, so shrink in place: a
            # full-size copy per pass is what made a large picture expensive.
            if max(image.size) > edge:
                image.thumbnail((edge, edge), Image.LANCZOS)
            sized = image
            for fmt, media_type, options in (
                ("PNG", "image/png", {"optimize": True}),
                ("JPEG", "image/jpeg", {"quality": 85, "optimize": True}),
            ):
                buffer = io.BytesIO()
                sized.save(buffer, format=fmt, **options)
                if buffer.tell() <= IMAGE_MAX_ENCODED_BYTES:
                    return ImagePart(
                        media_type=media_type, data=buffer.getvalue(), label=label,
                        width=sized.size[0], height=sized.size[1],
                    )
            if edge <= 400:
                raise EvidenceReadError("This picture could not be made small enough to read.")
            edge = int(edge * 0.75)
    except EvidenceReadError:
        raise
    except Exception as exc:  # noqa: BLE001 — includes Pillow's own decompression-bomb error
        raise EvidenceReadError(f"This picture could not be read: {str(exc)[:200]}") from exc
