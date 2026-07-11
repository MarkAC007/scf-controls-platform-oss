/**
 * VendorReportMarkdown -- lightweight markdown renderer for vendor
 * assessment reports (no external markdown dependency).
 *
 * Handles headers, bold/italic, inline code, links, horizontal rules,
 * ordered/unordered lists and tables, with a table of contents when the
 * document has more than two headings. Output is sanitised with DOMPurify
 * because report content is AI-generated.
 */
import DOMPurify from 'dompurify'

function inlineFormat(text: string): string {
  return text
    .replace(/`([^`]+)`/g, '<code style="background:var(--secondary);padding:1px 4px;border-radius:3px;font-size:0.85em">$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer" style="color:var(--primary)">$1</a>')
}

function renderMarkdown(md: string): string {
  const lines = md.split('\n')
  const html: string[] = []
  let inList = false
  let listType: 'ul' | 'ol' = 'ul'
  let inTable = false

  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim()

    // Horizontal rule
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      if (inList) { html.push(listType === 'ul' ? '</ul>' : '</ol>'); inList = false }
      if (inTable) { html.push('</tbody></table>'); inTable = false }
      html.push('<hr style="border:none;border-top:1px solid var(--border);margin:1rem 0" />')
      continue
    }

    // Headers
    const headerMatch = trimmed.match(/^(#{1,6})\s+(.+)$/)
    if (headerMatch) {
      if (inList) { html.push(listType === 'ul' ? '</ul>' : '</ol>'); inList = false }
      if (inTable) { html.push('</tbody></table>'); inTable = false }
      const level = headerMatch[1].length
      const sizes: Record<number, string> = { 1: '1.5rem', 2: '1.25rem', 3: '1.1rem', 4: '1rem', 5: '0.9rem', 6: '0.85rem' }
      const id = headerMatch[2].toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
      html.push(
        `<h${level} id="${id}" style="font-size:${sizes[level] || '1rem'};font-weight:600;margin:1rem 0 0.5rem 0;color:var(--text)">${inlineFormat(headerMatch[2])}</h${level}>`
      )
      continue
    }

    // Table row
    if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
      if (inList) { html.push(listType === 'ul' ? '</ul>' : '</ol>'); inList = false }

      // Skip separator rows (|---|---|)
      if (/^\|[\s-:|]+\|$/.test(trimmed)) {
        continue
      }

      const cells = trimmed.slice(1, -1).split('|').map((c) => c.trim())

      if (!inTable) {
        inTable = true
        html.push('<table style="width:100%;border-collapse:collapse;margin:0.75rem 0;font-size:0.85rem">')
        html.push('<thead><tr style="border-bottom:2px solid var(--border)">')
        cells.forEach((cell) => {
          html.push(`<th style="padding:0.375rem 0.625rem;text-align:left;font-weight:600;color:var(--text)">${inlineFormat(cell)}</th>`)
        })
        html.push('</tr></thead><tbody>')
        continue
      }

      html.push('<tr style="border-bottom:1px solid var(--border)">')
      cells.forEach((cell) => {
        html.push(`<td style="padding:0.375rem 0.625rem;color:var(--text)">${inlineFormat(cell)}</td>`)
      })
      html.push('</tr>')
      continue
    } else if (inTable) {
      html.push('</tbody></table>')
      inTable = false
    }

    // Unordered list
    if (/^[-*+]\s+/.test(trimmed)) {
      if (!inList || listType !== 'ul') {
        if (inList) html.push(listType === 'ul' ? '</ul>' : '</ol>')
        html.push('<ul style="margin:0.5rem 0;padding-left:1.5rem">')
        inList = true
        listType = 'ul'
      }
      html.push(`<li style="margin:0.125rem 0;color:var(--text)">${inlineFormat(trimmed.replace(/^[-*+]\s+/, ''))}</li>`)
      continue
    }

    // Ordered list
    if (/^\d+\.\s+/.test(trimmed)) {
      if (!inList || listType !== 'ol') {
        if (inList) html.push(listType === 'ul' ? '</ul>' : '</ol>')
        html.push('<ol style="margin:0.5rem 0;padding-left:1.5rem">')
        inList = true
        listType = 'ol'
      }
      html.push(`<li style="margin:0.125rem 0;color:var(--text)">${inlineFormat(trimmed.replace(/^\d+\.\s+/, ''))}</li>`)
      continue
    }

    // Close list if we're no longer in one
    if (inList && trimmed !== '') {
      html.push(listType === 'ul' ? '</ul>' : '</ol>')
      inList = false
    }

    if (trimmed === '') {
      continue
    }

    html.push(`<p style="margin:0.375rem 0;color:var(--text);line-height:1.6">${inlineFormat(trimmed)}</p>`)
  }

  if (inList) html.push(listType === 'ul' ? '</ul>' : '</ol>')
  if (inTable) html.push('</tbody></table>')

  return html.join('\n')
}

function extractHeadings(md: string): Array<{ level: number; text: string; id: string }> {
  const headings: Array<{ level: number; text: string; id: string }> = []
  for (const line of md.split('\n')) {
    const match = line.trim().match(/^(#{1,6})\s+(.+)$/)
    if (match) {
      const text = match[2].replace(/\*\*([^*]+)\*\*/g, '$1').replace(/\*([^*]+)\*/g, '$1')
      headings.push({
        level: match[1].length,
        text,
        id: text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''),
      })
    }
  }
  return headings
}

export default function VendorReportMarkdown({ content }: { content: string }) {
  const headings = extractHeadings(content)
  const htmlContent = renderMarkdown(content)

  return (
    <div style={{ fontSize: '0.875rem', lineHeight: 1.7, color: 'var(--text)' }}>
      {/* Table of contents */}
      {headings.length > 2 && (
        <div
          style={{
            padding: '0.75rem 1rem',
            backgroundColor: 'var(--secondary)',
            borderRadius: '8px',
            border: '1px solid var(--border)',
            marginBottom: '1rem',
          }}
        >
          <div style={{ fontWeight: 600, fontSize: '0.8rem', marginBottom: '0.5rem', color: 'var(--text)' }}>
            Table of Contents
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.125rem' }}>
            {headings.map((h, i) => (
              <a
                key={i}
                href={`#${h.id}`}
                style={{
                  color: 'var(--primary)',
                  textDecoration: 'none',
                  fontSize: '0.8rem',
                  paddingLeft: `${(h.level - 1) * 0.75}rem`,
                  lineHeight: 1.5,
                }}
                onClick={(e) => {
                  e.preventDefault()
                  const el = document.getElementById(h.id)
                  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
                }}
              >
                {h.text}
              </a>
            ))}
          </div>
        </div>
      )}

      {/* Rendered markdown — sanitised to prevent XSS from AI-generated content */}
      <div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(htmlContent, {
        ALLOWED_TAGS: ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'strong', 'em', 'code', 'a', 'hr', 'div', 'span'],
        ALLOWED_ATTR: ['style', 'id', 'href', 'target', 'rel'],
        ALLOW_DATA_ATTR: false,
      }) }} />
    </div>
  )
}
