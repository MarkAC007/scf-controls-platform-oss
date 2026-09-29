/**
 * richText — the one renderer for user-written descriptions.
 *
 * What these tests defend:
 *   - user text is never interpreted as HTML: "<script>" renders as text;
 *   - links: bare http(s) URLs and [label](url) become anchors that open in a
 *     new tab with rel=noopener; javascript:/data: targets never become hrefs;
 *   - the Markdown-lite subset (bold, italic, code, fenced code, lists,
 *     headings) renders, and plain text still comes out as a paragraph.
 */
import { describe, expect, it } from 'vitest'

import { escapeHtml, hasRichMarkup, isSafeHref, renderRichText } from '../richText'

describe('renderRichText — safety', () => {
  it('renders empty input as nothing', () => {
    expect(renderRichText('')).toBe('')
    expect(renderRichText('   \n ')).toBe('')
    expect(renderRichText(null)).toBe('')
    expect(renderRichText(undefined)).toBe('')
  })

  it('escapes HTML in the source instead of rendering it', () => {
    const html = renderRichText('Run <script>alert(1)</script> now')
    expect(html).not.toContain('<script>')
    expect(html).toContain('&lt;script&gt;')
  })

  it('never emits a javascript: or data: href', () => {
    expect(renderRichText('[x](javascript:alert(1))')).not.toContain('href')
    expect(renderRichText('[x](data:text/html;base64,AAAA)')).not.toContain('href')
    // The label survives as text so nothing is silently dropped.
    expect(renderRichText('[click me](javascript:alert(1))')).toContain('click me')
  })

  it('turns literal tags into text: no element carries an event handler', () => {
    const html = renderRichText('<img src=x onerror=alert(1)> and <a href="https://a.b" onclick="x">a</a>')
    const doc = new DOMParser().parseFromString(html, 'text/html')
    expect(doc.querySelector('img')).toBeNull()
    for (const el of Array.from(doc.body.querySelectorAll('*'))) {
      for (const attr of Array.from(el.attributes)) {
        expect(attr.name.startsWith('on')).toBe(false)
      }
    }
    // The tag text itself survives as text, so nothing is silently dropped.
    expect(doc.body.textContent).toContain('<img src=x onerror=alert(1)>')
  })

  it('forces every anchor to open in a new tab without an opener', () => {
    const doc = new DOMParser().parseFromString(renderRichText('https://a.example and [b](https://b.example)'), 'text/html')
    const anchors = Array.from(doc.querySelectorAll('a'))
    expect(anchors).toHaveLength(2)
    for (const a of anchors) {
      expect(a.getAttribute('target')).toBe('_blank')
      expect(a.getAttribute('rel')).toBe('noopener noreferrer nofollow')
    }
  })

  it('escapeHtml covers the five characters that matter', () => {
    expect(escapeHtml(`<a href="x">&'</a>`)).toBe('&lt;a href=&quot;x&quot;&gt;&amp;&#39;&lt;/a&gt;')
  })
})

describe('isSafeHref', () => {
  it('accepts http, https and mailto', () => {
    expect(isSafeHref('https://example.com/path?a=1&b=2')).toBe(true)
    expect(isSafeHref('http://example.com')).toBe(true)
    expect(isSafeHref('mailto:someone@example.com')).toBe(true)
  })

  it('refuses javascript, data, relative and empty targets', () => {
    expect(isSafeHref('javascript:alert(1)')).toBe(false)
    expect(isSafeHref('JAVASCRIPT:alert(1)')).toBe(false)
    expect(isSafeHref('data:text/html,hi')).toBe(false)
    expect(isSafeHref('/settings')).toBe(false)
    expect(isSafeHref('')).toBe(false)
  })
})

describe('renderRichText — links', () => {
  it('auto-links a bare URL and opens it in a new tab', () => {
    const html = renderRichText('See https://wiki.example.com/runbook for steps.')
    expect(html).toContain('<a href="https://wiki.example.com/runbook" target="_blank" rel="noopener noreferrer nofollow">https://wiki.example.com/runbook</a>')
    // Trailing full stop stays outside the link.
    expect(html).toContain('</a> for steps.')
  })

  it('keeps a trailing full stop out of the link', () => {
    const html = renderRichText('Go to https://example.com.')
    expect(html).toContain('href="https://example.com"')
    expect(html).toContain('</a>.')
  })

  it('renders [label](url) links', () => {
    const html = renderRichText('Read the [runbook](https://example.com/rb) first')
    expect(html).toContain('<a href="https://example.com/rb" target="_blank" rel="noopener noreferrer nofollow">runbook</a>')
  })

  it('preserves an ampersand inside a URL query string', () => {
    const html = renderRichText('https://example.com/?a=1&b=2')
    expect(html).toContain('href="https://example.com/?a=1&amp;b=2"')
  })

  it('renders mailto links', () => {
    const html = renderRichText('[email us](mailto:help@example.com)')
    expect(html).toContain('href="mailto:help@example.com"')
  })
})

describe('renderRichText — markdown subset', () => {
  it('wraps plain text in a paragraph and keeps line breaks', () => {
    expect(renderRichText('line one\nline two')).toBe('<p>line one<br>line two</p>')
  })

  it('splits paragraphs on blank lines', () => {
    expect(renderRichText('one\n\ntwo')).toBe('<p>one</p><p>two</p>')
  })

  it('renders bold, italic and inline code', () => {
    const html = renderRichText('run **now** with *care* using `terraform plan`')
    expect(html).toContain('<strong>now</strong>')
    expect(html).toContain('<em>care</em>')
    expect(html).toContain('<code>terraform plan</code>')
  })

  it('leaves markup inside inline code alone', () => {
    const html = renderRichText('use `**not bold**` here')
    expect(html).toContain('<code>**not bold**</code>')
    expect(html).not.toContain('<strong>')
  })

  it('renders fenced code blocks verbatim', () => {
    const html = renderRichText('```\nkubectl get pods\n  -n scf\n```')
    expect(html).toBe('<pre><code>kubectl get pods\n  -n scf</code></pre>')
  })

  it('renders bullet and numbered lists', () => {
    expect(renderRichText('- a\n- b')).toBe('<ul><li>a</li><li>b</li></ul>')
    expect(renderRichText('1. a\n2. b')).toBe('<ol><li>a</li><li>b</li></ol>')
  })

  it('renders headings up to level three', () => {
    expect(renderRichText('# Title')).toBe('<h1>Title</h1>')
    expect(renderRichText('### Sub')).toBe('<h3>Sub</h3>')
    // Four hashes is not a heading we support: it stays text.
    expect(renderRichText('#### nope')).toBe('<p>#### nope</p>')
  })
})

describe('hasRichMarkup', () => {
  it('detects links, code and list markers', () => {
    expect(hasRichMarkup('plain words')).toBe(false)
    expect(hasRichMarkup('see https://x.y')).toBe(true)
    expect(hasRichMarkup('use `x`')).toBe(true)
    expect(hasRichMarkup('- item')).toBe(true)
    expect(hasRichMarkup(null)).toBe(false)
  })
})
