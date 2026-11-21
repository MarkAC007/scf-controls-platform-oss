# SPDX-License-Identifier: AGPL-3.0-only
"""Sample evidence files, built in memory, one per accepted type (issue #1309).

Shared by the tests of the readers, the extractor and the preview. Each
builder returns bytes; nothing touches disk.
"""
import io
import json
import zipfile

MARKER = "quarterly access review completed"


def png(width=640, height=480, mode="RGB") -> bytes:
    from PIL import Image

    image = Image.new(mode, (width, height), (200, 30, 30) if mode == "RGB" else (200, 30, 30, 128))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg(width=640, height=480) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (30, 200, 30)).save(buffer, format="JPEG")
    return buffer.getvalue()


def gif(frames=3) -> bytes:
    from PIL import Image

    images = [Image.new("P", (120, 80), index) for index in range(frames)]
    buffer = io.BytesIO()
    images[0].save(buffer, format="GIF", save_all=True, append_images=images[1:])
    return buffer.getvalue()


def webp(width=320, height=200) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (30, 30, 200)).save(buffer, format="WEBP")
    return buffer.getvalue()


def text_pdf(text=MARKER) -> bytes:
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def scanned_pdf(pages=2) -> bytes:
    """A PDF whose pages are pictures only: no text layer."""
    import fitz

    doc = fitz.open()
    picture = png(300, 200)
    for _ in range(pages):
        page = doc.new_page()
        page.insert_image(page.rect, stream=picture)
    data = doc.tobytes()
    doc.close()
    return data


def stamped_scan_pdf(pages=2, stamp="CONFIDENTIAL") -> bytes:
    """A scan with a line of text stamped on every page: a text layer, but no content in it."""
    import fitz

    doc = fitz.open()
    picture = png(300, 424)
    for number in range(1, pages + 1):
        page = doc.new_page()
        page.insert_image(page.rect, stream=picture, keep_proportion=False)
        page.insert_text((72, 40), f"{stamp} page {number}")
    data = doc.tobytes()
    doc.close()
    return data


def typed_pdf_with_a_scanned_page() -> bytes:
    """Page 1 typed, page 2 a scanned signature page, page 3 a short page with a small logo."""
    import fitz

    doc = fitz.open()
    first = doc.new_page()
    first.insert_text((72, 72), f"Access Control Policy. {MARKER}. " + "Reviewed and approved. " * 8)
    second = doc.new_page()
    second.insert_image(second.rect, stream=png(300, 424), keep_proportion=False)
    second.insert_text((72, 40), "Signed")
    third = doc.new_page()
    third.insert_image(fitz.Rect(72, 72, 172, 122), stream=png(100, 50))
    third.insert_text((72, 200), "Appendix")
    data = doc.tobytes()
    doc.close()
    return data


def docx_with_table() -> bytes:
    """A paragraph, a table with a merged cell and a table inside a cell, then a paragraph."""
    from docx import Document

    doc = Document()
    doc.add_paragraph("Access matrix")
    table = doc.add_table(rows=3, cols=3)
    for column, heading in enumerate(("User", "System", "Reviewed")):
        table.cell(0, column).text = heading
    table.cell(1, 0).text = "alice"
    table.cell(1, 1).text = "Payroll"
    table.cell(1, 2).text = MARKER
    merged = table.cell(2, 0).merge(table.cell(2, 1))
    merged.text = "bob (all systems)"
    inner = table.cell(2, 2).add_table(rows=1, cols=2)
    inner.cell(0, 0).text = "signed"
    inner.cell(0, 1).text = "2026-11-02"
    doc.add_paragraph("End of matrix")
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def docx_with_document_part(megabytes: int, part_name: str = "word/document.xml") -> bytes:
    """A Word container whose document part unpacks to about ``megabytes`` MB of real paragraphs.

    ``part_name`` stores that part under another name, with the package's own
    pointers changed to match: Word finds parts by relationship, not by name.
    """
    paragraph = b"<w:p><w:r><w:t>quarterly access review</w:t></w:r></w:p>"
    body = paragraph * ((megabytes * 1024 * 1024) // len(paragraph) + 1)
    source = zipfile.ZipFile(io.BytesIO(docx()))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for info in source.infolist():
            raw = source.read(info.filename)
            name = info.filename
            if name == "word/document.xml":
                head, tail = raw.split(b"<w:body>", 1)
                raw = head + b"<w:body>" + body + tail
                name = part_name
            elif name in ("_rels/.rels", "[Content_Types].xml"):
                raw = raw.replace(b"word/document.xml", part_name.encode())
            elif name == "word/_rels/document.xml.rels" and part_name != "word/document.xml":
                folder, base = part_name.rsplit("/", 1)
                name = f"{folder}/_rels/{base}.rels"
            zf.writestr(name, raw)
    return buffer.getvalue()


def declaring(archive: bytes, member: str, declared: bytes) -> bytes:
    """Rewrite ``member``'s directory entry so it claims to hold only ``declared``.

    The compressed stream is left alone and still inflates to everything that
    was written. ``declared`` must be the start of the real content, so the
    checksum of the claimed bytes is right and the file reads as valid.
    """
    import struct
    import zlib

    zf = zipfile.ZipFile(io.BytesIO(archive))
    info = zf.getinfo(member)
    crc = zlib.crc32(declared)
    out = bytearray(archive)
    struct.pack_into("<LLL", out, info.header_offset + 14, crc, info.compress_size, len(declared))
    position = zf.start_dir
    for entry in zf.infolist():
        assert out[position:position + 4] == b"PK\x01\x02"
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", out, position + 28)
        if entry.filename == member:
            struct.pack_into("<LLL", out, position + 16, crc, entry.compress_size, len(declared))
        position += 46 + name_len + extra_len + comment_len
    return bytes(out)


def office_hiding_its_size(build, megabytes: int = 64, compression=zipfile.ZIP_DEFLATED) -> bytes:
    """An Office file (``build`` is ``docx``, ``xlsx`` or ``pptx``) with a part that says it is small.

    ``docProps/pad.xml`` declares a few bytes and inflates to ``megabytes`` MB.
    """
    source = zipfile.ZipFile(io.BytesIO(build()))
    claimed = b"<pad/>"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for info in source.infolist():
            zf.writestr(info.filename, source.read(info.filename))
        zf.writestr(
            zipfile.ZipInfo("docProps/pad.xml"), claimed + b" " * (megabytes * 1024 * 1024),
            compress_type=compression,
        )
    return declaring(buffer.getvalue(), "docProps/pad.xml", claimed)


def docx_whose_document_hides_its_size(megabytes: int = 64) -> bytes:
    """A Word file that reads normally: its document part is followed by padding it does not declare."""
    source = zipfile.ZipFile(io.BytesIO(docx()))
    document = source.read("word/document.xml")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for info in source.infolist():
            raw = source.read(info.filename)
            if info.filename == "word/document.xml":
                raw = document + b" " * (megabytes * 1024 * 1024)
            zf.writestr(info.filename, raw)
    return declaring(buffer.getvalue(), "word/document.xml", document)


def zip_hiding_its_size(megabytes: int = 64, compression=zipfile.ZIP_BZIP2) -> bytes:
    """A ZIP whose one text file declares two bytes and inflates to ``megabytes`` MB."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(zipfile.ZipInfo("notes.txt"), b"ok" + b" " * (megabytes * 1024 * 1024), compress_type=compression)
    return declaring(buffer.getvalue(), "notes.txt", b"ok")


def xlsx_with_entities(megabytes_of_padding: int = 3, references: int = 2000, encoding: str = "utf-8") -> bytes:
    """A workbook whose sheet defines entities in itself and uses them in one cell.

    Read by a parser that expands them, the cell grows to hundreds of
    megabytes. The comment is there because the parser allows expansion in
    proportion to what it has read.
    """
    sheet = (
        '<?xml version="1.0" encoding="{enc}"?>'
        '<!DOCTYPE worksheet [<!ENTITY a "{a}"><!ENTITY b "{b}">]>'
        "<!--{pad}-->"
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1"><c r="A1" t="inlineStr"><is><t>{refs}</t></is></c></row>'
        "</sheetData></worksheet>"
    ).format(
        enc=encoding, a="A" * 1000, b="&a;" * 100,
        pad="x" * (megabytes_of_padding * 1024 * 1024), refs="&b;" * references,
    )
    source = zipfile.ZipFile(io.BytesIO(xlsx({"Reviews": [["placeholder"]]})))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for info in source.infolist():
            raw = source.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                raw = sheet.encode(encoding)
            zf.writestr(info.filename, raw)
    return buffer.getvalue()


def eml_with_long_header(name: str = "Subject", words: int = 30_000, word: str = "=?utf-8?q?a?=") -> bytes:
    """An email in which one header is ``words`` copies of ``word``."""
    separator = ", " if name.lower() in ("to", "cc", "from") else " "
    lines = [
        "From: Alice Admin <alice@example.com>",
        "To: auditor@example.com",
        "Subject: Access review sign-off",
        "Date: Mon, 02 Nov 2026 10:00:00 +0000",
        "Content-Type: text/plain; charset=utf-8",
    ]
    lines = [line for line in lines if not line.lower().startswith(name.lower() + ":")]
    lines.append(f"{name}: " + separator.join([word] * words))
    return ("\r\n".join(lines) + f"\r\n\r\nHello,\r\n\r\n{MARKER}.\r\n").encode()


def docx(paragraphs=("Access Control Policy", MARKER)) -> bytes:
    from docx import Document

    doc = Document()
    for paragraph in paragraphs:
        doc.add_paragraph(paragraph)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def xlsx(sheets=None) -> bytes:
    from openpyxl import Workbook

    sheets = sheets or {
        "Reviews": [["User", "Reviewed", "Count"], ["alice", MARKER, 3], ["bob", None, 4.5]],
        "Empty": [],
        "Owners": [["System", "Owner"], ["Payroll", "Finance"]],
    }
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


_SLIDE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree>'
    "{paragraphs}</p:spTree></p:cSld></p:sld>"
)
_PARA = "<p:sp><p:txBody><a:p><a:r><a:t>{a}</a:t></a:r><a:r><a:t>{b}</a:t></a:r></a:p></p:txBody></p:sp>"


def pptx(slides=(("Security ", "awareness"), (MARKER, "")), reverse_order=False) -> bytes:
    """A minimal PowerPoint file. ``reverse_order`` lists the slides in the
    presentation part in the opposite order to their file numbers."""
    numbers = list(range(1, len(slides) + 1))
    order = list(reversed(numbers)) if reverse_order else numbers
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        zf.writestr(
            "ppt/presentation.xml",
            '<?xml version="1.0"?><p:presentation '
            'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<p:sldIdLst>"
            + "".join(f'<p:sldId id="{255 + n}" r:id="rId{n}"/>' for n in order)
            + "</p:sldIdLst></p:presentation>",
        )
        zf.writestr(
            "ppt/_rels/presentation.xml.rels",
            '<?xml version="1.0"?><Relationships '
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(
                f'<Relationship Id="rId{n}" Type="slide" Target="slides/slide{n}.xml"/>' for n in numbers
            )
            + "</Relationships>",
        )
        for n, (a, b) in zip(numbers, slides):
            zf.writestr(f"ppt/slides/slide{n}.xml", _SLIDE.format(paragraphs=_PARA.format(a=a, b=b)))
    return buffer.getvalue()


def eml(html_only=False, attachment=True) -> bytes:
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = "Alice Admin <alice@example.com>"
    msg["To"] = "auditor@example.com"
    msg["Subject"] = "Access review sign-off"
    msg["Date"] = "Mon, 02 Nov 2026 10:00:00 +0000"
    if html_only:
        msg.set_content(
            f"<html><head><style>p{{color:red}}</style></head><body><p>{MARKER}</p>"
            "<script>alert(1)</script></body></html>",
            subtype="html",
        )
    else:
        msg.set_content(f"Hello,\n\n{MARKER}.\n\nAlice")
    if attachment:
        msg.add_attachment(b"col\n1\n", maintype="text", subtype="csv", filename="export.csv")
    return msg.as_bytes()


def zip_of(members: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def evidence_zip() -> bytes:
    return zip_of({
        "notes/readme.txt": f"{MARKER} (from the text file)".encode(),
        "export.csv": b"user,reviewed\nalice,yes\n",
        "screens/console.png": png(200, 100),
        "inner.zip": zip_of({"deep.txt": b"must never be read"}),
        "tool.exe": b"MZ\x00\x00",
        "__MACOSX/._readme.txt": b"\x00\x05\x16\x07",
        "folder/": b"",
    })


def zip_declaring(total_bytes: int, members: int = 1) -> bytes:
    """A real ZIP whose members are zero-filled and unpack to ``total_bytes``."""
    buffer = io.BytesIO()
    each = total_bytes // members
    block = b"\0" * (1024 * 1024)
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for index in range(members):
            with zf.open(f"zeros-{index}.txt", "w") as handle:
                left = each
                while left > 0:
                    handle.write(block[:left])
                    left -= min(left, len(block))
    return buffer.getvalue()


XML = (
    '<?xml version="1.0"?>\n<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>\n'
    f"<report><finding>{MARKER}</finding><leak>&x;</leak></report>\n"
).encode()

#: content type -> (file name, bytes builder, a string the extracted text must hold)
SAMPLES = {
    "application/pdf": ("report.pdf", text_pdf, MARKER),
    "image/png": ("screen.png", png, "[Picture: PNG image"),
    "image/jpeg": ("photo.jpg", jpeg, "[Picture: JPEG image"),
    "image/gif": ("anim.gif", gif, "[Picture: GIF image"),
    "image/webp": ("screen.webp", webp, "[Picture: WebP image"),
    "text/csv": ("export.csv", lambda: f"user,note\nalice,{MARKER}\n".encode(), MARKER),
    "text/plain": ("server.log", lambda: f"2026-11-02 INFO {MARKER}\n".encode(), MARKER),
    "text/markdown": ("notes.md", lambda: f"# Notes\n\n{MARKER}\n".encode(), MARKER),
    "text/yaml": ("config.yaml", lambda: f"review: {MARKER}\n".encode(), MARKER),
    "application/json": ("data.json", lambda: json.dumps({"review": MARKER}).encode(), MARKER),
    "application/xml": ("report.xml", lambda: XML, MARKER),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ("policy.docx", docx, MARKER),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ("reviews.xlsx", xlsx, MARKER),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ("deck.pptx", pptx, MARKER),
    "application/zip": ("bundle.zip", evidence_zip, MARKER),
    "message/rfc822": ("signoff.eml", eml, MARKER),
}


def eml_at_the_limits(parts: int = 199, header_chars: int = 2000, subject_chars: int = 8000) -> bytes:
    """The costliest message the reader still accepts: many parts, each with headers just under the caps."""
    params = "; ".join(f"p{n}=v" for n in range(header_chars // 8))[: header_chars - 40]
    subject = " ".join(["=?utf-8?q?a?="] * (subject_chars // 14))
    out = [
        "From: Alice Admin <alice@example.com>",
        f"To: {', '.join(['a@b.example'] * (subject_chars // 13))}",
        f"Subject: {subject}",
        "Date: Mon, 02 Nov 2026 10:00:00 +0000",
        "MIME-Version: 1.0",
        'Content-Type: multipart/mixed; boundary="edge"',
        "",
        "--edge",
        "Content-Type: text/plain; charset=utf-8",
        "",
        f"{MARKER}.",
    ]
    for n in range(parts - 2):
        out += [
            "--edge",
            f"Content-Type: text/csv; {params}",
            f'Content-Disposition: attachment; filename="export-{n}.csv"; {params}',
            "",
            "col",
        ]
    out += ["--edge--", ""]
    return "\r\n".join(out).encode()
