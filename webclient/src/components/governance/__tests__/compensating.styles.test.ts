import { describe, expect, it } from 'vitest'

// The effectiveness chips sit beside the acceptance chips and "Not justified":
// Minimal is a rating, not an alarm, so it must not wear the error colours.
describe('compensating chip colours (PIG R9)', () => {
  it('gives the Minimal effectiveness chip a distinct, non-alarm colour', async () => {
    const fs = await import('node' + ':fs')
    const css = fs.readFileSync('src/styles.css', 'utf-8')
    const rule = css.match(/\.compensating-chip--minimal\s*\{([^}]*)\}/)
    expect(rule).not.toBeNull()
    const body = rule![1]
    expect(body).not.toMatch(/--error-bg|--destructive/)
    expect(body).toMatch(/--info-bg/)
    expect(body).toMatch(/--primary-ink/)
  })
})
