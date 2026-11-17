// SPDX-License-Identifier: AGPL-3.0-only
import { describe, it, expect } from 'vitest'

import { renderMarkdownDocument } from '../markdownDocument'

function dom(md: string): HTMLElement {
  const el = document.createElement('div')
  el.innerHTML = renderMarkdownDocument(md)
  return el
}

describe('renderMarkdownDocument', () => {
  it('returns empty string for empty input', () => {
    expect(renderMarkdownDocument('')).toBe('')
    expect(renderMarkdownDocument('   \n')).toBe('')
    expect(renderMarkdownDocument(null)).toBe('')
  })

  it('renders headings at every level', () => {
    const el = dom('# One\n## Two\n### Three\n#### Four\n##### Five\n###### Six')
    for (let level = 1; level <= 6; level++) {
      expect(el.querySelector(`h${level}`)).not.toBeNull()
    }
    expect(el.querySelector('h4')?.textContent).toBe('Four')
  })

  it('renders unordered and ordered lists', () => {
    const el = dom('- alpha\n- beta\n\n1. first\n2. second')
    expect(el.querySelectorAll('ul > li')).toHaveLength(2)
    expect(el.querySelectorAll('ol > li')).toHaveLength(2)
  })

  it('renders a pipe table with header and body rows', () => {
    const el = dom('| User | Role |\n|------|:----:|\n| alice | admin |\n| bob | viewer |\n\nAfter.')
    expect(Array.from(el.querySelectorAll('thead th')).map(th => th.textContent)).toEqual(['User', 'Role'])
    expect(el.querySelectorAll('tbody tr')).toHaveLength(2)
    expect(el.querySelector('tbody tr td')?.textContent).toBe('alice')
    expect(el.querySelector('p')?.textContent).toBe('After.')
  })

  it('renders fenced code blocks verbatim without inline formatting', () => {
    const el = dom('```bash\nrm -rf **not bold** <b>x</b>\n```')
    const code = el.querySelector('pre > code')
    expect(code?.textContent).toBe('rm -rf **not bold** <b>x</b>')
    expect(el.querySelector('strong')).toBeNull()
    expect(el.querySelector('b')).toBeNull()
  })

  it('renders block quotes and horizontal rules', () => {
    const el = dom('> quoted line\n\n---\n\ntext')
    expect(el.querySelector('blockquote')?.textContent).toBe('quoted line')
    expect(el.querySelector('hr')).not.toBeNull()
  })

  it('renders inline emphasis, code and safe links', () => {
    const el = dom('**bold** *it* `x` [docs](https://example.com/a?b=1&c=2)')
    expect(el.querySelector('strong')?.textContent).toBe('bold')
    expect(el.querySelector('em')?.textContent).toBe('it')
    expect(el.querySelector('code')?.textContent).toBe('x')
    const a = el.querySelector('a')
    expect(a?.getAttribute('href')).toBe('https://example.com/a?b=1&c=2')
    expect(a?.getAttribute('rel')).toContain('noopener')
  })

  it('never emits a script element from raw HTML in the source', () => {
    const html = renderMarkdownDocument('<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>')
    const el = document.createElement('div')
    el.innerHTML = html
    expect(el.querySelector('script')).toBeNull()
    expect(el.querySelector('img')).toBeNull()
    expect(el.querySelector('[onerror]')).toBeNull()
    // Raw HTML is shown as text, not dropped silently.
    expect(el.textContent).toContain('<script>alert(1)</script>')
  })

  it('drops javascript: links, keeping only their label', () => {
    const el = dom('[click](javascript:alert(1)) and [data](data:text/html,<b>x</b>)')
    expect(el.querySelector('a')).toBeNull()
    expect(el.textContent).toContain('click')
  })
})
