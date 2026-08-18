import { test } from '@playwright/test'

// Diagnostic helper (not part of CI smoke): prints the widest offending
// elements for tabs listed in DIAG_TABS. Skipped unless DIAG_TABS is set.
// Example: DIAG_TABS="Evidence,Audit Log" npx playwright test e2e/diagnose.spec.ts --project=mobile-390

const TABS = (process.env.DIAG_TABS || '').split(',').map(s => s.trim()).filter(Boolean)

test('diagnose horizontal overflow offenders', async ({ page }) => {
  test.skip(TABS.length === 0, 'set DIAG_TABS to run')
  test.setTimeout(240_000)
  await page.goto('/')
  await page.locator('.app-main').waitFor({ state: 'visible', timeout: 45_000 })
  for (const label of TABS) {
    const toggle = page.locator('.mobile-nav-toggle')
    if (await toggle.isVisible()) {
      if (!(await page.locator('.sidebar-nav.mobile-open').isVisible())) await toggle.click()
      await page.locator('.sidebar-nav.mobile-open').waitFor({ state: 'visible' })
    }
    await page.locator('.sidebar-nav-item', { hasText: label }).first().click()
    await page.waitForLoadState('networkidle', { timeout: 20_000 }).catch(() => {})
    await page.waitForTimeout(500)
    const offenders = await page.evaluate(() => {
      const vw = document.documentElement.clientWidth
      const bad: Element[] = []
      document.querySelectorAll('.app-main *').forEach(el => {
        const r = el.getBoundingClientRect()
        if (r.width > 0 && r.right > vw + 1) bad.push(el)
      })
      // keep only leaf offenders (no offending direct child) — O(n)
      const set = new Set(bad)
      const leaves = bad.filter(el => !Array.from(el.children).some(c => set.has(c)))
      const out = leaves.slice(0, 12).map(el => {
        const r = el.getBoundingClientRect()
        const cls = (el as HTMLElement).className?.toString?.().slice(0, 80) || ''
        const pcls = (el.parentElement as HTMLElement)?.className?.toString?.().slice(0, 50) || ''
        return `${el.tagName.toLowerCase()}.${cls} w=${Math.round(r.width)} r=${Math.round(r.right)} parent=${pcls}`
      })
      // widest-child chain from .app-content down
      const chain: string[] = []
      let node: Element | null = document.querySelector('.app-content')
      for (let depth = 0; node && depth < 12; depth++) {
        const kids = Array.from(node.children) as HTMLElement[]
        if (!kids.length) break
        const widest = kids.reduce((a, b) =>
          b.getBoundingClientRect().width > a.getBoundingClientRect().width ? b : a)
        const r = widest.getBoundingClientRect()
        if (r.width <= vw + 1) break
        chain.push(`${widest.tagName.toLowerCase()}.${widest.className?.toString?.().slice(0, 60)} w=${Math.round(r.width)} style=${(widest.getAttribute('style') || '').slice(0, 100)}`)
        node = widest
      }
      return { vw, out, chain }
    })
    console.log(`  CHAIN:`)
    offenders.chain.forEach((c: string) => console.log('   > ' + c))
    console.log(`\n=== ${label} (vw=${offenders.vw}) ===`)
    offenders.out.forEach(o => console.log('  ' + o))
  }
})
