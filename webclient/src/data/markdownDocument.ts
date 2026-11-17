/**
 * Markdown document → sanitised HTML, for viewing uploaded Markdown evidence.
 *
 * A whole-document sibling of `renderRichText`: same escape-first pipeline and
 * the same inline rules (`formatInline`, which only emits http/https/mailto
 * links), plus the block forms a runbook or exported policy actually uses —
 * headings 1-6, tables, block quotes, rules and fenced code.
 *
 * Evidence is uploaded by people and webhooks, so it is untrusted. The source
 * is HTML-escaped before any Markdown is recognised — raw HTML in the file is
 * shown as text, never parsed — and the result still goes through DOMPurify
 * with a closed tag list as a second layer.
 */
import { escapeHtml, formatInline, purifier } from './richText'

const ALLOWED_TAGS = [
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'br', 'strong', 'em', 'code', 'pre',
  'a', 'ul', 'ol', 'li', 'blockquote', 'hr', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
]
const ALLOWED_ATTR = ['href', 'target', 'rel']

const TABLE_SEPARATOR = /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$/

function splitRow(line: string): string[] {
  let row = line.trim()
  if (row.startsWith('|')) row = row.slice(1)
  if (row.endsWith('|')) row = row.slice(0, -1)
  return row.split('|').map(c => c.trim())
}

export function renderMarkdownDocument(source: string | null | undefined): string {
  if (!source || !source.trim()) return ''

  const lines = escapeHtml(source.replace(/\r\n?/g, '\n')).split('\n')
  const html: string[] = []
  let paragraph: string[] = []
  let list: { type: 'ul' | 'ol'; items: string[] } | null = null
  let quote: string[] = []
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
  const flushQuote = () => {
    if (quote.length) {
      html.push(`<blockquote><p>${quote.map(formatInline).join('<br>')}</p></blockquote>`)
      quote = []
    }
  }
  const flushAll = () => {
    flushParagraph()
    flushList()
    flushQuote()
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]

    if (code !== null) {
      if (/^(```|~~~)/.test(line.trim())) {
        html.push(`<pre><code>${code.join('\n')}</code></pre>`)
        code = null
      } else {
        code.push(line)
      }
      continue
    }

    const trimmed = line.trim()

    if (/^(```|~~~)/.test(trimmed)) {
      flushAll()
      code = []
      continue
    }

    if (!trimmed) {
      flushAll()
      continue
    }

    // `>` was escaped to `&gt;` above, so the quote marker is matched escaped.
    const quoted = trimmed.match(/^&gt;\s?(.*)$/)
    if (quoted) {
      flushParagraph()
      flushList()
      if (quoted[1]) quote.push(quoted[1])
      continue
    }
    flushQuote()

    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      flushAll()
      html.push('<hr>')
      continue
    }

    const heading = trimmed.match(/^(#{1,6})\s+(.+?)\s*#*$/)
    if (heading) {
      flushAll()
      const level = heading[1].length
      html.push(`<h${level}>${formatInline(heading[2])}</h${level}>`)
      continue
    }

    // A table is a pipe row immediately followed by a separator row.
    const next = lines[i + 1]?.trim() ?? ''
    if (trimmed.includes('|') && TABLE_SEPARATOR.test(next)) {
      flushAll()
      const header = splitRow(trimmed)
      const body: string[][] = []
      i += 2
      while (i < lines.length && lines[i].trim().includes('|')) {
        body.push(splitRow(lines[i]))
        i++
      }
      i-- // the for loop's increment moves past the last row consumed
      html.push(
        '<table><thead><tr>'
        + header.map(c => `<th>${formatInline(c)}</th>`).join('')
        + '</tr></thead><tbody>'
        + body.map(r => `<tr>${header.map((_h, ci) => `<td>${formatInline(r[ci] ?? '')}</td>`).join('')}</tr>`).join('')
        + '</tbody></table>',
      )
      continue
    }

    const bullet = trimmed.match(/^[-*+]\s+(.+)$/)
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
  flushAll()

  return purifier.sanitize(html.join(''), {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOWED_URI_REGEXP: /^(?:https?:|mailto:)/i,
  })
}
