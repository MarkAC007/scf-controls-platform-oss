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

#: Print stylesheet. ``__FOOTER__`` is substituted with ``str.replace`` rather
#: than %-formatting, because the stylesheet contains literal percent signs
#: (``width: 100%``) that %-formatting reads as conversion specifiers.
#: Deliberately plain: an ISMS document is read for its
#: content and is often bound into a larger evidence pack, so it needs to look
#: like a controlled document rather than like marketing.
_PRINT_CSS = """
@page {
    size: A4;
    margin: 20mm 18mm 22mm 18mm;
    @bottom-center {
        content: counter(page) " of " counter(pages);
        font-family: "DejaVu Sans", sans-serif;
        font-size: 8pt;
        color: #666;
    }
    @bottom-left {
        content: "__FOOTER__";
        font-family: "DejaVu Sans", sans-serif;
        font-size: 7pt;
        color: #888;
    }
}
body {
    font-family: "DejaVu Serif", Georgia, serif;
    font-size: 10.5pt;
    line-height: 1.5;
    color: #1a1a1a;
}
h1, h2, h3, h4 {
    font-family: "DejaVu Sans", Helvetica, sans-serif;
    color: #0f2a4a;
    line-height: 1.25;
}
h1 { font-size: 20pt; margin: 0 0 4pt; }
h2 { font-size: 14pt; margin: 18pt 0 6pt; border-bottom: 0.5pt solid #c9d4e0; padding-bottom: 3pt; }
h3 { font-size: 11.5pt; margin: 13pt 0 4pt; }
h2, h3, h4 { page-break-after: avoid; }
p { margin: 0 0 7pt; orphans: 3; widows: 3; }
table {
    border-collapse: collapse;
    width: 100%;
    margin: 8pt 0 12pt;
    font-size: 8.5pt;
    page-break-inside: auto;
}
th, td {
    border: 0.5pt solid #c9d4e0;
    padding: 3.5pt 5pt;
    text-align: left;
    vertical-align: top;
}
th { background: #eef3f8; font-family: "DejaVu Sans", sans-serif; font-weight: 600; }
tr { page-break-inside: avoid; }
blockquote {
    margin: 8pt 0;
    padding: 5pt 10pt;
    border-left: 2.5pt solid #3b6ea5;
    background: #f6f9fc;
    font-style: italic;
}
code { font-family: "DejaVu Sans Mono", monospace; font-size: 8.5pt; background: #f2f4f7; padding: 0 2pt; }
pre { background: #f2f4f7; padding: 6pt; page-break-inside: avoid; }
"""


def markdown_to_html(
    content: str,
    *,
    title: str = "",
    include_markers: bool = False,
) -> str:
    """Convert document Markdown to a standalone HTML page.

    Args:
        content: The document's merged Markdown.
        title: Page title, also rendered as the document heading if the
            Markdown does not open with one.
        include_markers: Keep merge markers. Only the in-app preview wants
            this; exports never do.
    """
    import markdown as md

    body = content if include_markers else strip_markers(content)
    html_body = md.markdown(
        body,
        extensions=["tables", "fenced_code", "toc", "sane_lists", "attr_list"],
        output_format="html5",
    )

    heading = ""
    if title and not re.match(r"\s*<h1[ >]", html_body):
        heading = f"<h1>{_escape(title)}</h1>\n"

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en-GB">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{_escape(title)}</title>\n</head>\n<body>\n"
        f"{heading}{html_body}\n</body>\n</html>\n"
    )


def _escape(value: str) -> str:
    return (
        (value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def render_pdf(
    content: str,
    *,
    title: str = "",
    organisation: str = "",
    classification: str = "Internal",
) -> bytes:
    """Render document Markdown to PDF bytes.

    Args:
        content: The document's merged Markdown.
        title: Document title.
        organisation: Shown in the page footer, so a printed page is
            attributable when it is separated from the file.
        classification: Also shown in the footer.

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
        part for part in (organisation, classification, f"Generated {stamped}") if part
    )
    html = markdown_to_html(content, title=title, include_markers=False)

    document = HTML(string=html).render(
        stylesheets=[CSS(string=_PRINT_CSS.replace("__FOOTER__", footer.replace('"', "'")))]
    )
    return document.write_pdf()


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
