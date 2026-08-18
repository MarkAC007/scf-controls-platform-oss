// Deep mobile verification (not part of CI smoke): sub-tabs, detail panes,
// modals/drawers at 390px against the dev-demo stack, plus PWA offline reload
// against the vite preview build. Run: node e2e/deep-checks.mjs
import { webkit } from 'playwright'

const DEMO = process.env.E2E_BASE_URL || 'http://127.0.0.1:7794'
const PREVIEW = process.env.PREVIEW_URL || 'http://127.0.0.1:7801'
const results = []
const shot = (p, name) => p.screenshot({ path: `e2e/screenshots/deep-390/${name}.png` })

async function overflow(page) {
  return page.evaluate(() => {
    const d = document.documentElement
    const c = document.querySelector('.app-content')
    return {
      doc: d.scrollWidth - d.clientWidth,
      content: c ? c.scrollWidth - c.clientWidth : 0,
    }
  })
}

async function record(page, name) {
  const o = await overflow(page)
  const pass = o.doc <= 1 && o.content <= 1
  results.push({ name, pass, ...o })
  await shot(page, name)
  console.log(`${pass ? 'PASS' : 'FAIL'} ${name} doc=${o.doc} content=${o.content}`)
}

async function gotoTab(page, label) {
  const toggle = page.locator('.mobile-nav-toggle')
  if (await toggle.isVisible()) {
    if (!(await page.locator('.sidebar-nav.mobile-open').isVisible())) await toggle.click()
    await page.locator('.sidebar-nav.mobile-open').waitFor({ state: 'visible' })
  }
  await page.locator('.sidebar-nav-item', { hasText: label }).first().click()
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {})
  await page.waitForTimeout(500)
}

const b = await webkit.launch()
const page = await b.newPage({ viewport: { width: 390, height: 844 } })
await page.goto(DEMO + '/')
await page.locator('.app-main').waitFor({ state: 'visible', timeout: 45000 })

// --- dashboard sub-tabs
await gotoTab(page, 'Dashboard')
const dashTabs = page.locator('.app-content [role="tab"], .app-content .dashboard-tabs button, .app-content .tab-button')
const nDash = await dashTabs.count()
console.log(`dashboard sub-tab candidates: ${nDash}`)
for (let i = 0; i < nDash && i < 6; i++) {
  const label = ((await dashTabs.nth(i).textContent()) || `tab${i}`).trim().toLowerCase().replace(/[^a-z0-9]+/g, '-')
  await dashTabs.nth(i).click().catch(() => {})
  await page.waitForTimeout(700)
  await record(page, `dashboard-sub-${label}`)
}

// --- evidence workspace -> control detail
await gotoTab(page, 'Evidence')
const wsTab = page.locator('.evidence-workspace-tabs button, .app-content button', { hasText: /workspace/i }).first()
if (await wsTab.isVisible().catch(() => false)) {
  await wsTab.click(); await page.waitForTimeout(900)
}
await record(page, 'evidence-workspace')
const evRow = page.locator('.app-content tbody tr, .app-content .control-row, .app-content .workspace-row').first()
if (await evRow.isVisible().catch(() => false)) {
  await evRow.click(); await page.waitForTimeout(900)
  await record(page, 'evidence-control-detail')
} else console.log('SKIP evidence-control-detail (no row visible)')

// --- vendor detail
await gotoTab(page, 'Vendor Inventory')
const vRow = page.locator('.app-content tbody tr, .app-content .vendor-card, .app-content .vendor-row').first()
if (await vRow.isVisible().catch(() => false)) {
  await vRow.click(); await page.waitForTimeout(900)
  await record(page, 'vendor-detail')
} else console.log('SKIP vendor-detail (no vendor row)')

// --- webhooks create modal
await gotoTab(page, 'Webhooks')
const whBtn = page.locator('.app-content button', { hasText: /add|create|new/i }).first()
if (await whBtn.isVisible().catch(() => false)) {
  await whBtn.click(); await page.waitForTimeout(700)
  await record(page, 'webhooks-modal')
  await page.keyboard.press('Escape').catch(() => {})
} else console.log('SKIP webhooks-modal (no create button)')

// --- engagements drawer
await gotoTab(page, 'Engagements')
const engBtn = page.locator('.app-content button', { hasText: /new engagement|add|create/i }).first()
if (await engBtn.isVisible().catch(() => false)) {
  await engBtn.click(); await page.waitForTimeout(700)
  await record(page, 'engagements-drawer')
  await page.keyboard.press('Escape').catch(() => {})
} else console.log('SKIP engagements-drawer (no create button)')

// --- theme-color meta follows dark base
await page.evaluate(() => {
  localStorage.setItem('scf-theme-preference', 'dark')
  localStorage.setItem('scf-theme-base', 'dark')
})
await page.reload(); await page.waitForTimeout(1500)
const darkMeta = await page.evaluate(() =>
  document.querySelector('meta[name="theme-color"]')?.getAttribute('content'))
console.log(`dark theme-color meta: ${darkMeta}`)
results.push({ name: 'dark-theme-color', pass: !!darkMeta && darkMeta.toLowerCase() !== '#ffffff' })
await page.close()

// --- offline reload serves shell (preview build, SW active)
const ctx = await b.newContext({ viewport: { width: 390, height: 844 } })
const p2 = await ctx.newPage()
await p2.goto(PREVIEW + '/')
await p2.waitForTimeout(3500) // let SW install + activate
const swActive = await p2.evaluate(async () => {
  const reg = await navigator.serviceWorker?.getRegistration()
  return !!reg?.active
})
console.log(`preview SW active: ${swActive}`)
await ctx.setOffline(true)
await p2.reload().catch(() => {})
await p2.waitForTimeout(2000)
const offlineOk = await p2.evaluate(() => !!document.getElementById('root') && document.title.length > 0)
results.push({ name: 'offline-reload-shell', pass: swActive && offlineOk })
console.log(`${swActive && offlineOk ? 'PASS' : 'FAIL'} offline-reload-shell (sw=${swActive}, shell=${offlineOk})`)
await p2.screenshot({ path: 'e2e/screenshots/deep-390/offline-reload.png' })
await ctx.setOffline(false)

await b.close()
const fails = results.filter(r => !r.pass)
console.log(`\n=== ${results.length - fails.length}/${results.length} passed`)
if (fails.length) { console.log('FAILURES:', JSON.stringify(fails)); process.exit(1) }
