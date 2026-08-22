"""
Export rendering — Markdown to HTML, and HTML to PDF.

WeasyPrint is imported inside the function rather than at module scope. It
binds to Pango and Cairo through shared libraries that exist in the container
image but not on a typical developer laptop, so a module-level import would
make every unit test in this package fail on a machine that will never render
a PDF. The cost is one import per export; the benefit is that the merge engine
stays testable anywhere.

Merge markers are stripped before export. They are review scaffolding — a
conflict notice belongs in the editor, not in a document handed to an auditor.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from .three_layer import strip_markers

logger = logging.getLogger(__name__)

#: Print stylesheet. ``__FOOTER__`` and ``__RUNHEAD__`` are substituted with
#: ``str.replace`` rather than %-formatting, because the stylesheet contains
#: literal percent signs (``width: 100%``) that %-formatting reads as
#: conversion specifiers.
#:
#: The palette and proportions are the ones the standalone generator used
#: before this feature was folded into the platform -- navy masthead rule,
#: banded table heads, an accent bar on every sub-heading. An ISMS document is
#: usually met as a PDF attachment on an auditor's desk long before anyone sees
#: the app, so the export is the product's first impression and should look
#: like a controlled document that somebody owns.
_PRINT_CSS = """
@page {
    size: A4;
    margin: 22mm 18mm 20mm 18mm;
    @top-left {
        content: "__RUNHEAD__";
        font-family: Inter, "DejaVu Sans", sans-serif;
        font-size: 7.5pt;
        color: #94a3b8;
        vertical-align: bottom;
        padding-bottom: 5pt;
    }
    @top-right {
        content: "__CLASSIFICATION__";
        font-family: Inter, "DejaVu Sans", sans-serif;
        font-size: 7.5pt;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: #94a3b8;
        vertical-align: bottom;
        padding-bottom: 5pt;
    }
    @bottom-left {
        content: "__FOOTER__";
        font-family: Inter, "DejaVu Sans", sans-serif;
        font-size: 7.5pt;
        color: #94a3b8;
        vertical-align: top;
        padding-top: 5pt;
    }
    @bottom-right {
        content: "Page " counter(page) " of " counter(pages);
        font-family: Inter, "DejaVu Sans", sans-serif;
        font-size: 7.5pt;
        color: #94a3b8;
        vertical-align: top;
        padding-top: 5pt;
    }
}

/* The masthead already carries the brand on page one, so the running header
   would only repeat it. Margin boxes cannot be styled away with display:none
   -- setting the content to nothing is how you empty one. */
@page :first {
    @top-left { content: ""; }
    @top-right { content: ""; }
}

body {
    font-family: Inter, "DejaVu Sans", Helvetica, sans-serif;
    font-size: 9.5pt;
    line-height: 1.6;
    color: #1e293b;
}

/* ── Masthead ─────────────────────────────────────────────────────────── */

.doc-masthead {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12pt;
    border-bottom: 2.5pt solid #1a2744;
    padding-bottom: 10pt;
    margin-bottom: 20pt;
}
.doc-masthead-left { flex: 1; }
.doc-masthead-brand {
    font-size: 7.5pt;
    color: #64748b;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 3pt;
}
.doc-masthead-title {
    font-size: 16pt;
    font-weight: 700;
    color: #1a2744;
    line-height: 1.2;
    margin-bottom: 3pt;
}
.doc-masthead-sub { font-size: 8.5pt; color: #475569; }
.doc-masthead-right {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 6pt;
}
.doc-masthead-logo { height: 52pt; }
.doc-masthead-badge {
    background: #1a2744;
    color: #ffffff;
    font-size: 7.5pt;
    font-weight: 700;
    padding: 2.5pt 8pt;
    border-radius: 3pt;
    letter-spacing: 0.06em;
    text-transform: uppercase;
}

/* ── Typography ───────────────────────────────────────────────────────── */

/* The title is set in the masthead. Leaving the Markdown's own H1 in the flow
   would print it twice. */
h1 { display: none; }

h2 {
    font-family: Inter, "DejaVu Sans", sans-serif;
    font-size: 12.5pt;
    font-weight: 700;
    color: #1a2744;
    border-bottom: 1.5pt solid #1a2744;
    padding-bottom: 3pt;
    margin: 22pt 0 10pt;
}
h3 {
    font-family: Inter, "DejaVu Sans", sans-serif;
    font-size: 10.5pt;
    font-weight: 600;
    color: #1a2744;
    border-left: 2.5pt solid #3b82f6;
    padding-left: 7pt;
    margin: 16pt 0 6pt;
}
h4 {
    font-family: Inter, "DejaVu Sans", sans-serif;
    font-size: 9.5pt;
    font-weight: 600;
    color: #334155;
    margin: 12pt 0 5pt;
}
h2, h3, h4 { page-break-after: avoid; }
p { margin: 0 0 8pt; orphans: 3; widows: 3; }
a { color: #3b82f6; text-decoration: none; }

/* ── Tables ───────────────────────────────────────────────────────────── */

table {
    border-collapse: collapse;
    width: 100%;
    margin: 10pt 0 14pt;
    font-size: 8.5pt;
}
thead th {
    background: #1a2744;
    color: #ffffff;
    font-weight: 600;
    font-size: 7.5pt;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 6pt 7pt;
    text-align: left;
}
td {
    padding: 5pt 7pt;
    border-bottom: 0.5pt solid #e2e8f0;
    vertical-align: top;
}
tbody tr:nth-child(even) { background: #f8fafc; }
tr { page-break-inside: avoid; }
/* Repeat the head when a long register runs over a page break, otherwise the
   continuation reads as an unlabelled grid of values. */
thead { display: table-header-group; }

/* ── Blocks ───────────────────────────────────────────────────────────── */

blockquote {
    border-left: 3pt solid #3b82f6;
    background: #eff6ff;
    margin: 12pt 0;
    padding: 8pt 12pt;
    color: #1e40af;
    font-size: 9pt;
    page-break-inside: avoid;
}
blockquote p { margin: 0; }
ul, ol { margin: 6pt 0; padding-left: 16pt; }
li { margin-bottom: 3pt; }
hr { border: none; border-top: 0.5pt solid #e2e8f0; margin: 18pt 0; }
code {
    font-family: "DejaVu Sans Mono", monospace;
    font-size: 8pt;
    background: #f1f5f9;
    padding: 0.5pt 3pt;
    border-radius: 2pt;
    color: #0f172a;
}
pre {
    background: #0f172a;
    color: #e2e8f0;
    padding: 9pt 11pt;
    border-radius: 4pt;
    font-size: 8pt;
    margin: 10pt 0;
    page-break-inside: avoid;
}
pre code { background: none; padding: 0; color: inherit; }
"""


def build_masthead(
    *,
    title: str,
    organisation: str = "",
    subtitle: str = "",
    domain_id: str = "",
    logo_data_uri: str = "",
) -> str:
    """The page-one masthead: brand line, title, subtitle, logo, domain badge.

    Every part is optional and simply absent when the platform has no value for
    it -- an organisation that has not uploaded a logo gets a masthead without
    one rather than a broken image box.
    """
    left = [f'<div class="doc-masthead-title">{_escape(title)}</div>']
    if organisation:
        left.insert(0, f'<div class="doc-masthead-brand">{_escape(organisation)}</div>')
    if subtitle:
        left.append(f'<div class="doc-masthead-sub">{_escape(subtitle)}</div>')

    right = []
    if logo_data_uri:
        right.append(f'<img class="doc-masthead-logo" src="{logo_data_uri}" alt="">')
    if domain_id:
        right.append(f'<div class="doc-masthead-badge">{_escape(domain_id)}</div>')

    right_html = (
        f'<div class="doc-masthead-right">{"".join(right)}</div>' if right else ""
    )
    return (
        '<div class="doc-masthead">'
        f'<div class="doc-masthead-left">{"".join(left)}</div>'
        f"{right_html}"
        "</div>"
    )


def markdown_to_html(
    content: str,
    *,
    title: str = "",
    include_markers: bool = False,
    body_prefix: str = "",
) -> str:
    """Convert document Markdown to a standalone HTML page.

    Args:
        content: The document's merged Markdown.
        title: Page title, also rendered as the document heading if the
            Markdown does not open with one.
        include_markers: Keep merge markers. Only the in-app preview wants
            this; exports never do.
        body_prefix: Raw HTML placed ahead of the converted Markdown. The PDF
            path passes its masthead here. It is our own markup, never user
            input, and the escaping happens in :func:`build_masthead`.
    """
    import markdown as md

    body = content if include_markers else strip_markers(content)
    html_body = md.markdown(
        _neutralise_raw_html(body),
        # No attr_list. It lets the Markdown source set arbitrary attributes on
        # the element it follows -- `## Heading {: onclick="..." }` really does
        # render as `<h2 onclick="...">` -- which escaping `<` does not close.
        # Nothing generates or stores that syntax, so the extension only ever
        # cost us an injection vector.
        extensions=["tables", "fenced_code", "toc", "sane_lists"],
        output_format="html5",
    )

    heading = ""
    if title and not re.match(r"\s*<h1[ >]", html_body):
        heading = f"<h1>{_escape(title)}</h1>\n"

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en-GB">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{_escape(title)}</title>\n</head>\n<body>\n"
        f"{body_prefix}{heading}{html_body}\n</body>\n</html>\n"
    )


READER_FLAGS = {
    "new": "New — written from controls that have just come into scope.",
    "conflict": (
        "Both you and the generator changed this. Your text was kept; the "
        "generated alternative is in version history."
    ),
    "pending_retirement": (
        "The controls behind this section have left scope. Nothing has been "
        "deleted — retire it deliberately, or keep it."
    ),
    "human_preserved": "Your edit was kept; the generator did not touch this.",
}


def markdown_to_reader_fragment(content: str, sections) -> str:
    """Render the document as an HTML *fragment* for the in-app reader.

    Unlike :func:`markdown_to_html` this returns no page chrome -- the reader
    injects it into a React tree, so a doctype and ``<head>`` would be wrong.
    That mismatch is why the preview endpoint existed for months without a
    caller.

    Each section is wrapped in its own element carrying its stored
    ``section_id`` and merge status. The wrapper is what makes the document
    readable *as a document* while still showing the merge: the contents rail
    scroll-spies on it, the status drives the in-flow styling, and clicking
    "Edit" from a section can open that exact section in the editor.

    Markers are stripped rather than passed through. They are HTML comments, so
    a browser renders them as nothing at all -- the merge state was invisible
    in the very view that most needs to show it. The status class and the
    :data:`READER_FLAGS` banner carry the same meaning visibly.
    """
    import markdown as md

    from .section_parser import (
        flatten_sections,
        pair_sections_to_headings,
        parse_markdown_sections,
    )

    def render(fragment: str) -> str:
        # Every piece of document content reaching the reader goes through
        # here -- preamble, heading and section body alike -- so this is the
        # one place the neutralising has to happen. See
        # :func:`_neutralise_raw_html` for why, and for why attr_list is gone.
        return md.markdown(
            _neutralise_raw_html(fragment),
            extensions=["tables", "fenced_code", "sane_lists"],
            output_format="html5",
        )

    body = strip_markers(content)
    parsed = flatten_sections(parse_markdown_sections(body))

    # Which heading belongs to which stored row, matched by identity. The id
    # emitted below is what the reader hangs its per-section decision controls
    # on, so a wrong answer here does not mislabel a heading -- it points
    # "Take generated" at somebody else's section. Position cannot be used to
    # decide it: a retiree sits at the end of the document and a human edit can
    # introduce a heading line of its own, and either one puts every section
    # after it off by one. See :func:`pair_sections_to_headings`.
    pairing = pair_sections_to_headings(body, sections, parsed=parsed)
    rows_by_id = {row.section_id: row for row in sections}
    status_by_index = {
        index: (
            section_id,
            rows_by_id[section_id].status,
            rows_by_id[section_id].control_ids or [],
        )
        for section_id, index in pairing.heading_index.items()
        if section_id in rows_by_id
    }
    if pairing.unmatched:
        # Not an error: a section retired on an earlier run and already excised
        # from the text has no heading left to carry. Logged rather than
        # dropped in silence so a genuine drift shows up in the logs instead of
        # as an unexplained missing banner.
        logger.debug(
            "doc_gen: %d stored section(s) have no heading in the operative "
            "document: %s",
            len(pairing.unmatched),
            ", ".join(pairing.unmatched),
        )

    lines = body.split("\n")
    first_heading = next(
        (i for i, line in enumerate(lines) if re.match(r"^#{1,6}\s+", line)), None
    )
    parts = []
    if first_heading:
        preamble = "\n".join(lines[:first_heading]).strip()
        if preamble:
            parts.append(f'<div class="docr-preamble">{render(preamble)}</div>')

    for index, section in enumerate(parsed):
        section_id, status, control_ids = status_by_index.get(
            index, (section.section_id, "unchanged", section.control_ids)
        )
        hashes = "#" * section.heading_level
        inner = render(f"{hashes} {section.heading_text}\n\n{section.content}")
        flag = READER_FLAGS.get(status)
        banner = (
            f'<p class="docr-flag docr-flag-{status}">{_escape(flag)}</p>'
            if flag
            else ""
        )
        chips = ""
        if control_ids:
            chips = (
                '<p class="docr-controls">'
                + "".join(
                    f'<span class="docr-control">{_escape(c)}</span>'
                    for c in control_ids
                )
                + "</p>"
            )
        parts.append(
            f'<section class="docr-sec status-{_escape(status)}"'
            f' data-section-id="{_escape(section_id)}"'
            f' data-level="{section.heading_level}">'
            f"{banner}{inner}{chips}</section>"
        )

    return "\n".join(parts)


def _escape(value: str) -> str:
    """Escape a value for either element text or a quoted attribute value.

    The quotes matter. This is interpolated into ``data-section-id="..."`` and
    ``class="docr-sec status-..."`` as well as into element text, and a section
    id or status carrying a double quote would otherwise close the attribute
    and open a new one. Escaping quotes is inert in element context -- the
    browser renders the entity back as the character -- so one helper can
    serve both rather than leaving a choice to get wrong at each call site.
    """
    return (
        (value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _neutralise_raw_html(markdown_source: str) -> str:
    """Strip a Markdown document of its ability to emit raw HTML.

    Document content is Markdown by contract: every generator template emits
    Markdown and the section editor edits Markdown. Raw HTML is not a feature
    of that contract, but Python-Markdown passes it straight through -- it
    dropped ``safe_mode`` at 3.0 and tells you to sanitise downstream instead.
    Downstream here is three different consumers (the in-app reader, the HTML
    export and WeasyPrint), so the sanitising belongs at the one boundary they
    share rather than three times over.

    ``&`` is escaped before ``<`` so that a literal ``&lt;`` typed into a
    section cannot decode back into a tag. ``>`` is deliberately left alone:
    it opens no tag on its own and it is blockquote syntax at line start, so
    escaping it would break legitimate Markdown to buy nothing.
    """
    return markdown_source.replace("&", "&amp;").replace("<", "&lt;")


def render_pdf(
    content: str,
    *,
    title: str = "",
    organisation: str = "",
    classification: str = "Internal",
    subtitle: str = "",
    domain_id: str = "",
    logo_data_uri: str = "",
) -> bytes:
    """Render document Markdown to branded PDF bytes.

    Args:
        content: The document's merged Markdown.
        title: Document title. Set in the masthead; the Markdown's own H1 is
            hidden so it does not print twice.
        organisation: The owning organisation. Appears in the masthead brand
            line and in the page footer, so a page separated from the file is
            still attributable.
        classification: Printed in the running header of every page after the
            first, where a reader flicking through will actually see it.
        subtitle: What kind of document this is and where it stands -- e.g.
            "Domain Policy · Version 2 · Draft".
        domain_id: SCF domain code for the badge, e.g. ``GOV``.
        logo_data_uri: ``data:`` URI for the organisation's logo. A URL would
            make rendering depend on the network and on the renderer being
            able to authenticate to our own API, so the caller inlines it.

    Returns:
        PDF bytes.

    Raises:
        RuntimeError: if WeasyPrint's native dependencies are unavailable,
            with a message that says so rather than surfacing a bare
            ``OSError`` about a missing shared library.
    """
    try:
        from weasyprint import CSS, HTML
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "PDF export is unavailable: WeasyPrint's native dependencies "
            "(Pango/Cairo) are not installed in this environment. Markdown "
            "export is unaffected."
        ) from exc

    stamped = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    footer = " · ".join(
        part for part in (organisation, f"Generated {stamped}") if part
    )
    masthead = build_masthead(
        title=title,
        organisation=organisation,
        subtitle=subtitle,
        domain_id=domain_id,
        logo_data_uri=logo_data_uri,
    )
    html = markdown_to_html(
        content, title=title, include_markers=False, body_prefix=masthead
    )

    # Margin-box content is a CSS string literal, so a stray double quote would
    # end it early and drop the rest of the stylesheet on the floor.
    css = (
        _PRINT_CSS
        .replace("__FOOTER__", _css_string(footer))
        .replace("__RUNHEAD__", _css_string(" · ".join(p for p in (organisation, title) if p)))
        .replace("__CLASSIFICATION__", _css_string(classification))
    )
    document = HTML(string=html).render(stylesheets=[CSS(string=css)])
    return document.write_pdf()


def _css_string(value: str) -> str:
    """Make a value safe to sit inside a CSS ``content: "..."`` literal."""
    return value.replace("\\", "").replace('"', "'").replace("\n", " ")


def export_markdown(content: str, *, title: str = "") -> str:
    """Markdown export — markers stripped, title prepended if absent."""
    body = strip_markers(content).strip()
    if title and not body.startswith("# "):
        return f"# {title}\n\n{body}\n"
    return body + "\n"


def safe_filename(title: str, extension: str, domain_id: Optional[str] = None) -> str:
    """Build a download filename from a document title.

    Everything outside ``[a-z0-9-]`` is collapsed, so a title containing a
    slash or a quote cannot produce a path or a broken Content-Disposition
    header.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "document").lower()).strip("-")
    slug = slug or "document"
    if domain_id:
        slug = f"{slug}-{domain_id.lower()}"
    return f"{slug}.{extension.lstrip('.')}"
