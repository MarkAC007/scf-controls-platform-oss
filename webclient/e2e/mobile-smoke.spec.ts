import { test, expect, Page } from '@playwright/test'

// Mobile/responsive smoke: visits every top-level tab and asserts the page
// does not overflow horizontally, capturing a screenshot per tab/viewport.
// Runs against the dev-demo stack (auth disabled) — see playwright.config.ts.

const TABS: { id: string; label: string; optional?: boolean }[] = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'capability-posture', label: 'Analytics' },
  { id: 'library', label: 'Control Library' },
  { id: 'mapping-matrix', label: 'Framework Mappings' },
  { id: 'scoping', label: 'Control Scoping' },
  { id: 'risk-register', label: 'Risk Register' },
  { id: 'vendors', label: 'Vendor Inventory' },
  { id: 'evidence', label: 'Evidence' },
  { id: 'cdm', label: 'Control Documents' },
  { id: 'document-map', label: 'Document Map' },
  { id: 'tasks', label: 'Task Management' },
  { id: 'systems', label: 'Systems Registry' },
  { id: 'users', label: 'User Management' },
  { id: 'engagements', label: 'Engagements' },
  { id: 'webhooks', label: 'Webhooks' },
  { id: 'audit-log', label: 'Audit Log' },
  { id: 'consultant-portal', label: 'Consultant Portal', optional: true },
  { id: 'settings', label: 'Org Settings' },
]

async function waitForShell(page: Page) {
  // THEME=dark runs the same suite on the dark base theme
  if (process.env.THEME === 'dark') {
    await page.addInitScript(() => {
      localStorage.setItem('scf-theme-preference', 'dark')
      localStorage.setItem('scf-theme-base', 'dark')
    })
  }
  await page.goto('/')
  await page.locator('.app-main').waitFor({ state: 'visible', timeout: 45_000 })
}

/** Open a tab via the sidebar; on mobile viewports go through the hamburger drawer. */
async function gotoTab(page: Page, label: string): Promise<boolean> {
  const toggle = page.locator('.mobile-nav-toggle')
  const isMobile = await toggle.isVisible()
  if (isMobile) {
    const drawerOpen = await page.locator('.sidebar-nav.mobile-open').isVisible()
    if (!drawerOpen) {
      await toggle.click()
      await page.locator('.sidebar-nav.mobile-open').waitFor({ state: 'visible' })
    }
  }
  const item = page.locator('.sidebar-nav-item', { hasText: label }).first()
  if ((await item.count()) === 0 || !(await item.isVisible())) {
    // close drawer if we opened it
    if (isMobile) await page.keyboard.press('Escape').catch(() => {})
    return false
  }
  await item.click()
  if (isMobile) {
    await expect(page.locator('.sidebar-nav.mobile-open')).toBeHidden({ timeout: 5_000 })
  }
  await page.waitForLoadState('networkidle', { timeout: 20_000 }).catch(() => {})
  await page.waitForTimeout(400)
  return true
}

async function assertNoHorizontalOverflow(page: Page, label: string) {
  const metrics = await page.evaluate(() => ({
    docScrollW: document.documentElement.scrollWidth,
    docClientW: document.documentElement.clientWidth,
    contentScrollW: document.querySelector('.app-content')?.scrollWidth ?? 0,
    contentClientW: document.querySelector('.app-content')?.clientWidth ?? 0,
  }))
  expect
    .soft(metrics.docScrollW, `${label}: document overflows horizontally`)
    .toBeLessThanOrEqual(metrics.docClientW + 1)
  expect
    .soft(metrics.contentScrollW, `${label}: .app-content overflows horizontally`)
    .toBeLessThanOrEqual(metrics.contentClientW + 1)
}

test.describe('mobile smoke — all tabs', () => {
  test('every tab renders without horizontal overflow', async ({ page }, testInfo) => {
    test.setTimeout(300_000)
    await waitForShell(page)
    const missed: string[] = []
    for (const tab of TABS) {
      const ok = await gotoTab(page, tab.label)
      if (!ok) {
        if (!tab.optional) missed.push(tab.label)
        continue
      }
      await assertNoHorizontalOverflow(page, tab.label)
      await page.screenshot({
        path: `e2e/screenshots/${testInfo.project.name}/${tab.id}.png`,
        fullPage: false,
      })
    }
    expect(missed, `tabs missing from nav: ${missed.join(', ')}`).toEqual([])
  })
})

test.describe('two-pane views — detail reachable on mobile', () => {
  for (const view of [
    { label: 'Control Library', id: 'library' },
    { label: 'Control Scoping', id: 'scoping' },
  ]) {
    test(`${view.id}: list and detail pane both reachable`, async ({ page }, testInfo) => {
      test.setTimeout(120_000)
      await waitForShell(page)
      const ok = await gotoTab(page, view.label)
      expect(ok, `could not navigate to ${view.label}`).toBe(true)
      const layout = page.locator('.layout').first()
      await layout.waitFor({ state: 'visible', timeout: 20_000 })
      const detail = page.locator('.layout .detail').first()
      await detail.waitFor({ state: 'attached', timeout: 20_000 })
      const box = await detail.boundingBox()
      const viewport = page.viewportSize()!
      expect(box, `${view.id}: detail pane has no box`).not.toBeNull()
      if (box) {
        // Detail pane must start within the horizontal viewport (stacked layout)
        expect(box.x, `${view.id}: detail pane pushed off-screen`).toBeLessThan(viewport.width)
        expect(box.width, `${view.id}: detail pane wider than viewport`).toBeLessThanOrEqual(viewport.width + 1)
      }
      await page.screenshot({
        path: `e2e/screenshots/${testInfo.project.name}/${view.id}-two-pane.png`,
        fullPage: false,
      })
    })
  }
})

test.describe('mobile navigation drawer', () => {
  test('hamburger opens and closes the drawer', async ({ page }) => {
    const viewport = page.viewportSize()!
    test.skip(viewport.width > 900, 'mobile-only behaviour')
    await waitForShell(page)
    const toggle = page.locator('.mobile-nav-toggle')
    await expect(toggle).toBeVisible()
    await toggle.click()
    await expect(page.locator('.sidebar-nav.mobile-open')).toBeVisible()
    // labels must be readable in the drawer
    await expect(page.locator('.sidebar-nav-label', { hasText: 'Dashboard' })).toBeVisible()
    // overlay tap closes
    await page.locator('.mobile-nav-overlay').click({ position: { x: 310, y: 400 } })
    await expect(page.locator('.sidebar-nav.mobile-open')).toBeHidden()
  })
})
