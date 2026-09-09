"""Word (.docx) export — the HTML from :func:`markdown_to_html` walked into python-docx.

**Why this re-parses HTML instead of reading the Markdown.** The obvious shape
for a Word exporter is a second Markdown parser writing paragraphs directly,
and it is the wrong one. HTML, PDF and DOCX would then disagree about which
Markdown features exist: a definition list or a footnote that renders in the
PDF would silently vanish from the Word file, and nobody would notice until an
auditor asked why two exports of the same document say different things. One
parse, three renderers, no drift. The cost is an HTML round-trip; the benefit
is that adding a Markdown extension in ``markdown_to_html`` reaches all three.

**Why this module is not in renderer.py.** renderer.py is already 580 lines and
carries the print stylesheet, the masthead builder and the WeasyPrint path. The
walker below is a different job with a different failure mode, so it gets its
own file; ``render_docx`` is re-exported from renderer.py so callers still find
it where the other exporters live.

**Why lxml rather than html.parser.** ``html.parser`` is a SAX-style event
stream, so using it means hand-rolling the element stack. The cases here are
exactly the ones a hand-rolled stack gets wrong: bold inside a list item inside
a nested list, a link inside a table cell, a run that is both bold and italic.
lxml hands over a real tree and the walk becomes ordinary recursion. It is
already in the image — WeasyPrint depends on it — so this adds no dependency.

**Imports live inside the functions,** matching the WeasyPrint convention this
package already follows. renderer.py imports this module at module scope for
the re-export, so a module-level ``import docx`` here would drag python-docx
into every test that touches the merge engine.

**No branding, structurally.** :func:`render_docx` takes no organisation, no
logo, no subtitle and no classification, because a parameter that does not
exist cannot be passed by mistake. This is deliberate and it is the difference
between this and :func:`render_pdf` next door, which is branded on purpose —
do not "finish" this one by copying that one's signature.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Word's own monospace default. Named rather than looked up so a code block
#: reads as a code block on a machine with no developer fonts installed.
MONOSPACE_FONT = "Courier New"

#: Word's hyperlink blue. Applied as direct formatting rather than through the
#: 'Hyperlink' character style, which is not guaranteed to exist in a template
#: and whose absence would otherwise raise while rendering an ordinary link.
LINK_COLOUR = (0x05, 0x63, 0xC1)

#: Indent added per list level that has no style of its own, and for the body
#: of a blockquote. Quarter of an inch is Word's own list step.
INDENT_STEP_INCHES = 0.25

_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_BOLD_TAGS = {"strong", "b"}
_ITALIC_TAGS = {"em", "i"}
_MONO_TAGS = {"code", "kbd", "samp", "tt", "var"}
_LIST_TAGS = {"ul", "ol"}
_CELL_TAGS = {"td", "th"}


@dataclass(frozen=True)
class _Inline:
    """Character formatting inherited down the inline tree.

    Frozen and passed by value because inline tags nest arbitrarily —
    ``<strong><em><code>x</code></em></strong>`` has to arrive at the run as
    all three at once, and a mutable "current format" object would leak the
    innermost tag back out to the text that follows the closing tag.
    """

    bold: bool = False
    italic: bool = False
    mono: bool = False
    link: bool = False

    def with_tag(self, tag: str) -> "_Inline":
        if tag in _BOLD_TAGS:
            return replace(self, bold=True)
        if tag in _ITALIC_TAGS:
            return replace(self, italic=True)
        if tag in _MONO_TAGS:
            return replace(self, mono=True)
        return self

    def as_link(self) -> "_Inline":
        return replace(self, link=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def render_docx(content: str, *, title: str = "") -> bytes:
    """Render document Markdown to Word (.docx) bytes.

    Word's default styles only — no logo, no masthead, no cover page, no
    organisation name, no themed colour. The parameters stop at ``title``
    for that reason; see the module docstring.

    Args:
        content: The document's merged Markdown. Merge markers are stripped
            on the way through :func:`markdown_to_html`, as in every export.
        title: Document title. Rendered as the opening Heading 1 if the
            Markdown does not already start with one — the same rule the HTML
            and PDF exports follow, because it is the same call.

    Returns:
        The .docx package as bytes. Nothing is written to disk.
    """
    import io

    import lxml.html
    from docx import Document

    # Imported here rather than at module scope: renderer.py imports this
    # module to re-export render_docx, so a module-scope import back into
    # renderer would be a cycle. By call time renderer is fully loaded.
    from .renderer import markdown_to_html

    html = markdown_to_html(content, title=title)
    root = lxml.html.fromstring(html)

    # markdown_to_html returns a standalone page. Walking from the root would
    # put the contents of <head> — the <title> text — into the document body.
    bodies = root.xpath("//body")
    body = bodies[0] if bodies else root

    document = Document()
    _render_blocks(document, body, level=0)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Block level
# ---------------------------------------------------------------------------


def _render_blocks(document: Any, element: Any, *, level: int) -> None:
    """Render every block child of ``element`` in document order.

    ``level`` is the current list nesting depth, carried so that a table or a
    code block inside a list item lands at the right indent.
    """
    _emit_stray_text(document, element.text, level=level)
    for child in element:
        _render_block(document, child, level=level)
        _emit_stray_text(document, child.tail, level=level)


def _render_block(document: Any, element: Any, *, level: int) -> None:
    tag = _tag(element)

    if tag in _HEADING_TAGS:
        _render_heading(document, element, _HEADING_TAGS[tag])
    elif tag == "p":
        _render_paragraph(document, element, level=level)
    elif tag in _LIST_TAGS:
        _render_list(document, element, level=level + 1)
    elif tag == "pre":
        _render_code_block(document, element, level=level)
    elif tag == "table":
        _render_table(document, element)
    elif tag == "hr":
        _render_horizontal_rule(document)
    elif tag == "blockquote":
        _render_blockquote(document, element, level=level)
    elif tag == "":
        # A comment or processing instruction. lxml gives these a callable tag
        # rather than a string. Nothing generates them — raw `<` is escaped
        # upstream — but dropping the node quietly is cheaper than crashing.
        pass
    else:
        # An unrecognised container. Recursing keeps its text, which is the
        # point: an element we have no mapping for must not silently delete
        # the author's words.
        _render_blocks(document, element, level=level)


def _render_heading(document: Any, element: Any, requested_level: int) -> None:
    """Render h1..h6 as a Word heading, degrading if the style is absent.

    python-docx's default template happens to define Heading 1 through 9, so
    this never degrades today. The guard is not theatre: the template is a
    library asset that changes between releases, and it is also the path a
    future custom template would take. A document with an ``h6`` in it must
    not be the thing that turns an export into a 500.
    """
    candidates = [f"Heading {n}" for n in range(requested_level, 0, -1)]
    style = _resolve_style(document, candidates)
    paragraph = _new_paragraph(document, style)
    # No heading style at all: bold text is what is left of "this is a heading".
    fmt = _Inline() if style else _Inline(bold=True)
    _render_inline(paragraph, element, fmt)


def _render_paragraph(document: Any, element: Any, *, level: int) -> None:
    paragraph = _new_paragraph(document, None)
    _indent(paragraph, level * INDENT_STEP_INCHES)
    _render_inline(paragraph, element, _Inline())


def _render_horizontal_rule(document: Any) -> None:
    """A real ruled line rather than a blank paragraph.

    Word has no `<hr>`; the convention is an empty paragraph carrying a bottom
    border, which is what a reader sees as a rule and what survives a copy into
    another document.
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    paragraph = document.add_paragraph()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "auto")
    borders.append(bottom)
    paragraph._p.get_or_add_pPr().append(borders)


def _render_blockquote(document: Any, element: Any, *, level: int) -> None:
    """Indent everything the blockquote contains.

    The Quote style is applied only to paragraphs that came out unstyled. A
    blockquote can legitimately hold a list or a code block, and stamping Quote
    over those would take the bullet or the monospace with it — the indent is
    what makes it read as a quotation either way.
    """
    quote_style = _resolve_style(document, ["Quote"])
    first_new = len(document.paragraphs)

    _render_blocks(document, element, level=level)

    for paragraph in document.paragraphs[first_new:]:
        if quote_style and paragraph.style.name == "Normal":
            paragraph.style = quote_style
        _indent(paragraph, INDENT_STEP_INCHES, additive=True)


def _render_code_block(document: Any, element: Any, *, level: int) -> None:
    """Render a fenced code block as one monospace paragraph per line.

    A soft break (``w:br``) inside a single paragraph looks identical in Word
    and is arguably more faithful, but it carries no text, so every extractor —
    python-docx's own ``paragraph.text`` included — reports the block as one
    unbroken line. That would make the line breaks unverifiable and, worse,
    would lose them the moment anyone copied the text back out. One paragraph
    per line survives both.
    """
    style = _resolve_style(document, ["No Spacing"])
    text = element.text_content()
    # `<pre><code>...\n</code></pre>` always carries a closing newline that is
    # fence syntax, not a blank final line of the author's code.
    if text.endswith("\n"):
        text = text[:-1]

    for line in text.split("\n"):
        paragraph = _new_paragraph(document, style)
        _indent(paragraph, level * INDENT_STEP_INCHES)
        # An empty line still needs its paragraph, or blank lines inside the
        # block collapse and the code changes shape.
        run = paragraph.add_run(line)
        _apply_format(run, _Inline(mono=True))


def _emit_stray_text(document: Any, text: Optional[str], *, level: int) -> None:
    """Text sitting directly between block elements.

    Markdown output puts only newlines here, so this almost never fires. It
    exists so that markup we did not anticipate cannot drop an author's words
    on the floor.
    """
    if not text or not text.strip():
        return
    paragraph = _new_paragraph(document, None)
    _indent(paragraph, level * INDENT_STEP_INCHES)
    paragraph.add_run(text.strip())


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


def _render_list(document: Any, element: Any, *, level: int) -> None:
    base = "List Number" if _tag(element) == "ol" else "List Bullet"
    for child in element:
        if _tag(child) == "li":
            _render_list_item(document, child, base=base, level=level)
        elif _tag(child) in _LIST_TAGS:
            # Malformed nesting — a list directly inside a list. Markdown does
            # not emit it, but recursing costs nothing and keeps the content.
            _render_list(document, child, level=level + 1)
        elif _tag(child):
            _render_block(document, child, level=level)


def _render_list_item(document: Any, item: Any, *, base: str, level: int) -> None:
    """Render one `<li>`, with any nested list following it.

    python-docx has no notion of list nesting — the numbering definitions that
    would give it one are not in the default template — so depth is expressed
    through the numbered style variants ('List Bullet 2', 'List Bullet 3') and,
    past the last one the template defines, through plain indentation.
    """
    style, styled_level = _list_style(document, base, level)
    paragraph = _new_paragraph(document, style)
    # Whatever depth the styles could not express is made up with indent, so a
    # four-deep list still steps in rather than flattening onto level three.
    _indent(paragraph, (level - styled_level) * INDENT_STEP_INCHES)

    fmt = _Inline()
    _add_run(paragraph, item.text, fmt)

    for child in item:
        tag = _tag(child)
        if tag in _LIST_TAGS:
            _render_list(document, child, level=level + 1)
            # A tail here would follow the nested list, so it cannot go back
            # into the bullet's own paragraph without arriving out of order.
            _emit_stray_text(document, child.tail, level=level)
        elif tag == "p":
            # A loose list puts the item text in a `<p>`. The first one belongs
            # to the bullet; any further one is a continuation paragraph, which
            # must not get a second bullet of its own.
            if paragraph.runs:
                continuation = _new_paragraph(document, None)
                _indent(continuation, level * INDENT_STEP_INCHES)
                _render_inline(continuation, child, fmt)
            else:
                _render_inline(paragraph, child, fmt)
            _emit_stray_text(document, child.tail, level=level)
        elif tag in ("pre", "table", "blockquote"):
            _render_block(document, child, level=level)
            _emit_stray_text(document, child.tail, level=level)
        elif tag == "":
            continue
        else:
            _render_inline(paragraph, child, fmt.with_tag(tag))
            _add_run(paragraph, child.tail, fmt)


def _list_style(document: Any, base: str, level: int) -> Tuple[Optional[str], int]:
    """Deepest list style this template defines at or below ``level``.

    Returns the style name and the depth it actually represents, so the caller
    knows how much indent it still has to add by hand.
    """
    candidates = [f"{base} {n}" for n in range(level, 1, -1)] + [base]
    style = _resolve_style(document, candidates)
    if style is None:
        # A template with no list styles at all. Indentation is all that is
        # left; the item loses its bullet glyph, which is ugly but is not a
        # 500 and does not lose the text.
        return None, 0
    trailing = style.rsplit(" ", 1)[-1]
    return style, int(trailing) if trailing.isdigit() else 1


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


def _render_table(document: Any, element: Any) -> None:
    """Render a Markdown table as a real Word table.

    Rows are collected through the explicit thead/tbody/tfoot children rather
    than a ``.//tr`` sweep, so a table that somehow contains another table
    cannot pull the inner rows up into the outer one.
    """
    rows = element.xpath("./tr | ./thead/tr | ./tbody/tr | ./tfoot/tr")
    if not rows:
        return

    grid: List[List[Any]] = [
        [cell for cell in row if _tag(cell) in _CELL_TAGS] for row in rows
    ]
    columns = max((len(row) for row in grid), default=0)
    if columns == 0:
        return

    table = document.add_table(rows=len(grid), cols=columns)
    style = _resolve_style(document, ["Table Grid"])
    if style:
        # Without a bordered style the cells render as invisible boxes, which
        # reads as mangled prose rather than as a table.
        table.style = style

    for row_index, cells in enumerate(grid):
        # A ragged row — fewer `<td>` than the header has columns — leaves the
        # trailing cells empty rather than raising, because a malformed table
        # in one section must not fail the whole export.
        header = bool(cells) and all(_tag(cell) == "th" for cell in cells)
        for column_index, cell in enumerate(cells):
            paragraph = table.cell(row_index, column_index).paragraphs[0]
            _render_inline(paragraph, cell, _Inline(bold=header))


# ---------------------------------------------------------------------------
# Inline level
# ---------------------------------------------------------------------------


def _render_inline(paragraph: Any, element: Any, fmt: _Inline) -> None:
    """Walk an element's inline content into runs on ``paragraph``."""
    _add_run(paragraph, element.text, fmt)

    for child in element:
        tag = _tag(child)
        child_fmt = fmt.with_tag(tag)

        if tag == "a":
            _render_hyperlink(paragraph, child, child_fmt)
        elif tag == "br":
            paragraph.add_run().add_break()
        elif tag == "img":
            # No images in an unbranded export, and a Markdown image reference
            # points at a URL this renderer will not fetch. The alt text is the
            # part that carries meaning, so that is what is kept.
            _add_run(paragraph, child.get("alt") or "", child_fmt)
        elif tag == "":
            pass
        else:
            _render_inline(paragraph, child, child_fmt)

        # The tail belongs to the parent's flow, not the child's formatting.
        _add_run(paragraph, child.tail, fmt)


def _add_run(paragraph: Any, text: Optional[str], fmt: _Inline) -> None:
    if not text:
        return
    _apply_format(paragraph.add_run(text), fmt)


def _apply_format(run: Any, fmt: _Inline) -> None:
    """Apply character formatting, setting only the attributes that are on.

    Assigning ``run.bold = False`` is not the same as leaving it alone: it
    writes an explicit "not bold" that overrides the paragraph style, which
    would un-bold every run in a heading.
    """
    from docx.oxml.ns import qn
    from docx.shared import RGBColor

    if fmt.bold:
        run.bold = True
    if fmt.italic:
        run.italic = True
    if fmt.mono:
        run.font.name = MONOSPACE_FONT
        # python-docx sets the ascii and hAnsi faces; east-Asian is a separate
        # slot and Word falls back to the theme font for it, so a document with
        # any CJK in a code span would render half in Courier and half not.
        run._element.rPr.rFonts.set(qn("w:eastAsia"), MONOSPACE_FONT)
    if fmt.link:
        run.underline = True
        run.font.color.rgb = RGBColor(*LINK_COLOUR)


def _render_hyperlink(paragraph: Any, element: Any, fmt: _Inline) -> None:
    """Render `<a href>` as a real Word hyperlink.

    python-docx has no public API for this, so the relationship and the
    ``w:hyperlink`` wrapper are built by hand. The runs are created on the
    paragraph first and then moved into the wrapper: building them detached
    would mean reimplementing run construction, and lxml's append already
    reparents rather than copies.
    """
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    url = (element.get("href") or "").strip()
    if not url:
        # An anchor with no destination is just text. Emitting a relationship
        # to the empty string produces a package Word refuses to open.
        _render_inline(paragraph, element, fmt)
        return

    existing = len(paragraph.runs)
    _render_inline(paragraph, element, fmt.as_link())
    runs = paragraph.runs[existing:]

    if not runs:
        # `[](https://example.com)` — a link with no text. Showing the URL is
        # the only way the destination survives into a printed document.
        run = paragraph.add_run(url)
        _apply_format(run, fmt.as_link())
        runs = [run]

    relationship_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relationship_id)
    for run in runs:
        hyperlink.append(run._element)
    paragraph._p.append(hyperlink)


# ---------------------------------------------------------------------------
# Style and layout helpers
# ---------------------------------------------------------------------------


def _resolve_style(document: Any, candidates: List[str]) -> Optional[str]:
    """First of ``candidates`` that this document's template actually defines.

    Style lookup is the one thing in this renderer that depends on an asset we
    do not control, and a missing style raises KeyError at assignment time —
    late, inside the export, as a 500. Resolving up front turns "this template
    has no Heading 6" into a degraded heading instead of a failed download.
    """
    for name in candidates:
        try:
            document.styles[name]
        except KeyError:
            continue
        return name
    return None


def _new_paragraph(document: Any, style: Optional[str]) -> Any:
    return document.add_paragraph(style=style) if style else document.add_paragraph()


def _indent(paragraph: Any, inches: float, *, additive: bool = False) -> None:
    if inches <= 0:
        return
    from docx.shared import Inches

    existing = paragraph.paragraph_format.left_indent
    base = existing if (additive and existing is not None) else Inches(0)
    paragraph.paragraph_format.left_indent = base + Inches(inches)


def _tag(element: Any) -> str:
    """Lower-cased tag name, or '' for a comment or processing instruction.

    lxml gives those a callable in ``.tag`` rather than a string, and calling
    ``.lower()`` on it is the crash this exists to avoid.
    """
    tag = element.tag
    return tag.lower() if isinstance(tag, str) else ""
