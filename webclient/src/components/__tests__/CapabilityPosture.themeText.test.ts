// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Capability Posture — text colours hold in every colour theme.
 *
 * The status colours (--warning, --destructive, --success, --primary) are
 * fills: pale in the dark themes, bright in the light ones. As text they fell
 * under 4.5:1 (amber band text on a light card 2:1, white on a pale status
 * pill 1.4:1). These tests pin that the rule that wins for each of those
 * texts takes an ink or text token, never a fill colour or a fixed white.
 */
import { describe, expect, it } from 'vitest'

const FILL = /var\(--(warning|destructive|success|primary|info|purple|muted)\)|#fff\b|#ffffff\b|\bwhite\b/i

async function stylesheet(): Promise<string> {
  const fs = await import('node' + ':fs')
  return fs.readFileSync('src/styles.css', 'utf-8').replace(/\/\*[\s\S]*?\*\//g, '')
}

/** The last declaration of `prop` in a rule whose selector list names `selector`. */
function winning(css: string, selector: string, prop: string): string | null {
  let found: string | null = null
  for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selectors = m[1].split(',').map(s => s.trim())
    if (!selectors.includes(selector)) continue
    for (const decl of m[2].split(';')) {
      const colon = decl.indexOf(':')
      if (colon > 0 && decl.slice(0, colon).trim() === prop) found = decl.slice(colon + 1).trim()
    }
  }
  return found
}

describe('capability posture text colours', () => {
  it.each([
    ['.cp-axis-band-strong', '--success-ink'],
    ['.cp-axis-band-moderate', '--warning-ink'],
    ['.cp-axis-band-developing', '--error-ink'],
  ])('band text %s takes %s', async (selector, token) => {
    const value = winning(await stylesheet(), selector, '--cp-axis-color')
    expect(value).toContain(`var(${token})`)
  })

  it.each([
    '.cp-status-badge',
    '.cp-maturity-badge[data-level]',
    '.cp-at-risk-badge',
    '.cp-evidence-weak',
    '.cp-evidence-moderate',
    '.cp-evidence-strong',
    '.cp-evidence-none',
    '.cp-ksi-badge',
    '.cp-axis-dot-label',
    '.cp-control-count',
    '.cp-relevance-primary',
    '.cp-relevance-supporting',
    '.cp-relevance-related',
    '.cp-back-btn',
    '.cp-controls-id-link',
    '.cp-controls-table th',
    '.cp-tab-count',
    '.cp-tab-active .cp-tab-count',
    '.cp-selected-yes',
    '.ehd-assessment-sufficient .ehd-assessment-pill',
    '.ehd-assessment-partial .ehd-assessment-pill',
    '.ehd-assessment-insufficient .ehd-assessment-pill',
    '.ehd-assessment-unassessable .ehd-assessment-pill',
    '.ehd-assessment-pending .ehd-assessment-pill',
  ])('%s is not written in a fill colour', async (selector) => {
    const value = winning(await stylesheet(), selector, 'color')
    expect(value).not.toBeNull()
    expect(value!).not.toMatch(FILL)
    expect(value!).toMatch(/var\(--[a-z-]*(ink|text)\)/)
  })

  it('the status pill tints its background from the status colour and keeps the colour on a dot', async () => {
    const css = await stylesheet()
    expect(winning(css, '.cp-status-badge', 'background')).toMatch(/color-mix\(.*var\(--cp-status-color\)/)
    expect(winning(css, '.cp-status-badge::before', 'background')).toBe('var(--cp-status-color)')
  })
})
