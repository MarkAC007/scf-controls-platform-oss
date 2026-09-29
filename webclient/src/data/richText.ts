/**
 * richText — one renderer for user-written descriptions (task descriptions,
 * system descriptions, and anything else a person types into a free-text
 * field that other people then read).
 *
 * The problem it solves: a description like "See https://wiki/runbook and
 * run `terraform plan`" used to render as flat text. Links were not clickable;
 * code was not distinguishable. This module turns a small, predictable subset
 * of Markdown into HTML, and turns bare URLs into links, so people get what
 * they expect without a rich-text editor.
 *
 * Supported (deliberately small — see MarkdownField for the toolbar that
 * inserts exactly these):
 *   - paragraphs (blank line separated), line breaks within a paragraph
 *   - **bold**, *italic*, `inline code`
 *   - ```fenced code blocks```
 *   - [label](https://…) links, and bare http(s):// URLs auto-linked
 *   - "- item" / "* item" bullet lists and "1. item" numbered lists
 *   - "# Heading" through "### Heading"
 *
 * Safety, in order:
 *   1. The SOURCE is HTML-escaped before any markup is generated, so a
 *      description containing "<script>" renders as the literal text
 *      "<script>". VendorReportMarkdown does not do this (its input is
 *      AI-generated, not user-typed); this renderer must.
 *   2. Link targets are allowed only when they parse as http, https or mailto.
 *      "javascript:" and "data:" never become an href — they stay as text.
 *   3. The result goes through DOMPurify with an explicit allow-list, as
 *      defence in depth, and every anchor is forced to
 *      target=_blank rel="noopener noreferrer nofollow".
 *
 * `renderRichText` is pure and synchronous so it is trivially testable;
 * <RichText> in components/ is the one place it meets the DOM.
 */
import DOMPurify from 'dompurify'

// A private DOMPurify instance so the anchor hook below cannot leak into the
// other renderers in the app (VendorReportMarkdown shares the default one).
// DOMPurify drops `target` and `rel` on anchors regardless of ALLOWED_ATTR, so
// forcing them in an afterSanitizeAttributes hook is the documented way to
// get new-tab links that cannot reach back into the opener.
const purifier = typeof window !== 'undefined' ? DOMPurify(window) : DOMPurify
if (purifier !== DOMPurify) {
  purifier.addHook('afterSanitizeAttributes', node => {
    if (node.tagName === 'A') {
      node.setAttribute('target', '_blank')
      node.setAttribute('rel', 'noopener noreferrer nofollow')
    }
  })
}

export const RICH_TEXT_ALLOWED_TAGS = [
  'p', 'br', 'strong', 'em', 'code', 'pre', 'a', 'ul', 'ol', 'li', 'h1', 'h2', 'h3',
] as const

export const RICH_TEXT_ALLOWED_ATTR = ['href', 'target', 'rel', 'class'] as const

const SAFE_PROTOCOLS = new Set(['http:', 'https:', 'mailto:'])

/** True when the href is one we are prepared to emit as a link. */
export function isSafeHref(raw: string): boolean {
  const candidate = raw.trim()
  if (!candidate) return false
  try {
    // Relative URLs throw here without a base; we do not want them anyway —
    // a description is not a place to link into the app by relative path.
    const url = new URL(candidate)
    return SAFE_PROTOCOLS.has(url.protocol)
  } catch {
    return false
  }
}

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/** Unescape only what escapeHtml did, for a URL that was escaped before parsing. */
function unescapeHtml(text: string): string {
  return text
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
}

function anchor(href: string, label: string): string {
  // href is escaped source text; validate its unescaped form and emit the
  // escaped one, so an ampersand in a query string survives both stages.
  if (!isSafeHref(unescapeHtml(href))) return label
  return `<a href="${href}" target="_blank" rel="noopener noreferrer nofollow">${label}</a>`
}

/**
 * Inline markup on already-escaped text. Order matters: code spans first so
 * that asterisks or brackets inside backticks are left alone, then explicit
 * links, then bare URLs, then emphasis.
 */
export function formatInline(escaped: string): string {
  const codeSpans: string[] = []
  let out = escaped.replace(/`([^`\n]+)`/g, (_m, code: string) => {
    codeSpans.push(`<code>${code}</code>`)
    return `\u0000${codeSpans.length - 1}\u0000`
  })

  // [label](url) — the url may not contain whitespace or a closing paren.
  out = out.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (_m, label: string, href: string) => anchor(href, label))

  // Bare URLs. Stop at whitespace, at a quote (already escaped as &quot;) and
  // at an angle bracket entity; strip trailing punctuation a sentence adds.
  out = out.replace(/(^|[^"'=>\w])(https?:\/\/[^\s<>&]+(?:&amp;[^\s<>&]+)*)/g, (_m, lead: string, url: string) => {
    const trimmed = url.replace(/[.,;:!?)\]]+$/, '')
    const trailing = url.slice(trimmed.length)
    // Skip URLs we have just placed inside an anchor's label or href.
    return `${lead}${anchor(trimmed, trimmed)}${trailing}`
  })

  out = out
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, '$1<em>$2</em>')

  return out.replace(/\u0000(\d+)\u0000/g, (_m, i: string) => codeSpans[Number(i)])
}

/**
 * Markdown-lite → sanitised HTML. Empty or whitespace-only input yields ''.
 */
export function renderRichText(source: string | null | undefined): string {
  if (!source || !source.trim()) return ''

  const lines = escapeHtml(source.replace(/\r\n?/g, '\n')).split('\n')
  const html: string[] = []
  let paragraph: string[] = []
  let list: { type: 'ul' | 'ol'; items: string[] } | null = null
  let code: string[] | null = null

  const flushParagraph = () => {
    if (paragraph.length) {
      html.push(`<p>${paragraph.map(formatInline).join('<br>')}</p>`)
      paragraph = []
    }
  }
  const flushList = () => {
    if (list) {
      html.push(`<${list.type}>${list.items.map(i => `<li>${formatInline(i)}</li>`).join('')}</${list.type}>`)
      list = null
    }
  }

  for (const line of lines) {
    if (code !== null) {
      if (/^```/.test(line.trim())) {
        html.push(`<pre><code>${code.join('\n')}</code></pre>`)
        code = null
      } else {
        code.push(line)
      }
      continue
    }

    const trimmed = line.trim()

    if (/^```/.test(trimmed)) {
      flushParagraph()
      flushList()
      code = []
      continue
    }

    if (!trimmed) {
      flushParagraph()
      flushList()
      continue
    }

    const heading = trimmed.match(/^(#{1,3})\s+(.+)$/)
    if (heading) {
      flushParagraph()
      flushList()
      html.push(`<h${heading[1].length}>${formatInline(heading[2])}</h${heading[1].length}>`)
      continue
    }

    const bullet = trimmed.match(/^[-*]\s+(.+)$/)
    const numbered = trimmed.match(/^\d+[.)]\s+(.+)$/)
    if (bullet || numbered) {
      flushParagraph()
      const type: 'ul' | 'ol' = bullet ? 'ul' : 'ol'
      if (!list || list.type !== type) {
        flushList()
        list = { type, items: [] }
      }
      list.items.push((bullet || numbered)![1])
      continue
    }

    flushList()
    paragraph.push(trimmed)
  }

  if (code !== null) html.push(`<pre><code>${code.join('\n')}</code></pre>`)
  flushParagraph()
  flushList()

  return purifier.sanitize(html.join(''), {
    ALLOWED_TAGS: [...RICH_TEXT_ALLOWED_TAGS],
    ALLOWED_ATTR: [...RICH_TEXT_ALLOWED_ATTR],
    // Belt and braces on top of isSafeHref.
    ALLOWED_URI_REGEXP: /^(?:https?:|mailto:)/i,
  })
}

/** Does this text contain anything the renderer would turn into markup? */
export function hasRichMarkup(source: string | null | undefined): boolean {
  if (!source) return false
  return /https?:\/\/|`|\*\*|\[[^\]]+\]\([^)]+\)|^\s*[-*]\s|^\s*\d+[.)]\s|^\s*#{1,3}\s/m.test(source)
}
