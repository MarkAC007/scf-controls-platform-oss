/**
 * The bell opens the item a notification names.
 *
 * Before `reference_key`, an evidence notification carried the evidence_tracking
 * row UUID and the bell fetched /evidence-tasks to recover the real id. When
 * that row had no tasks the fetch returned [], `tasks[0]` was undefined, and the
 * click did nothing at all — no navigation, no message, dropdown closed.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { NotificationBell } from '../NotificationBell'
import { apiClient } from '../../data/apiClient'

vi.mock('../../data/apiClient', () => ({
  apiClient: { get: vi.fn(), patch: vi.fn() },
}))

const mockGet = vi.mocked(apiClient.get)
const mockPatch = vi.mocked(apiClient.patch)

beforeEach(() => {
  vi.clearAllMocks()
  mockPatch.mockResolvedValue({})
})

function withNotifications(notifications: unknown[]) {
  mockGet.mockResolvedValue({ unread_count: notifications.length, notifications })
}

async function openAndClick(message: string) {
  await waitFor(() => expect(mockGet).toHaveBeenCalled())
  fireEvent.click(screen.getByRole('button', { name: /notifications/i }))
  const row = await screen.findByText(message)
  fireEvent.click(row)
}

describe('NotificationBell target resolution', () => {
  it('opens the evidence item named by an evidence notification', async () => {
    const onNavigateToEvidence = vi.fn()
    withNotifications([
      {
        id: 'n1',
        reference_type: 'evidence',
        reference_id: 'a2c1d0e4-0000-0000-0000-000000000001',
        reference_key: 'E-HRS-16',
        message: 'Alex rejected evidence E-HRS-16',
        is_read: false,
        created_at: new Date().toISOString(),
      },
    ])
    render(<NotificationBell onNavigateToEvidence={onNavigateToEvidence} />)
    await openAndClick('Alex rejected evidence E-HRS-16')
    await waitFor(() => expect(onNavigateToEvidence).toHaveBeenCalledWith('E-HRS-16'))
  })

  it('opens the evidence item named by an overdue-task notification', async () => {
    // The message always named the item; the reference never could reach it.
    const onNavigateToEvidence = vi.fn()
    const onNavigateToTask = vi.fn()
    withNotifications([
      {
        id: 'n2',
        reference_type: 'task',
        reference_id: 'b2c1d0e4-0000-0000-0000-000000000002',
        reference_key: 'E-HRS-16',
        message: 'Evidence collection task for E-HRS-16 is overdue by 4 day(s)',
        is_read: false,
        created_at: new Date().toISOString(),
      },
    ])
    render(
      <NotificationBell
        onNavigateToEvidence={onNavigateToEvidence}
        onNavigateToTask={onNavigateToTask}
      />,
    )
    await openAndClick('Evidence collection task for E-HRS-16 is overdue by 4 day(s)')
    await waitFor(() => expect(onNavigateToEvidence).toHaveBeenCalledWith('E-HRS-16'))
    expect(onNavigateToTask).not.toHaveBeenCalled()
  })

  it('still sends a task with no evidence key to the task list', async () => {
    // Not every task notification is about evidence. This path worked and must
    // keep working.
    const onNavigateToTask = vi.fn()
    withNotifications([
      {
        id: 'n3',
        reference_type: 'task',
        reference_id: 'c2c1d0e4-0000-0000-0000-000000000003',
        reference_key: null,
        message: 'You were assigned a task',
        is_read: false,
        created_at: new Date().toISOString(),
      },
    ])
    render(<NotificationBell onNavigateToTask={onNavigateToTask} />)
    await openAndClick('You were assigned a task')
    await waitFor(() => expect(onNavigateToTask).toHaveBeenCalled())
  })

  it('never fetches evidence-tasks to work out where to go', async () => {
    const onNavigateToEvidence = vi.fn()
    withNotifications([
      {
        id: 'n4',
        reference_type: 'evidence',
        reference_id: 'd2c1d0e4-0000-0000-0000-000000000004',
        reference_key: 'E-BCD-02',
        message: 'Alex rejected evidence E-BCD-02',
        is_read: false,
        created_at: new Date().toISOString(),
      },
    ])
    render(<NotificationBell onNavigateToEvidence={onNavigateToEvidence} />)
    await openAndClick('Alex rejected evidence E-BCD-02')
    await waitFor(() => expect(onNavigateToEvidence).toHaveBeenCalled())
    const paths = mockGet.mock.calls.map(c => String(c[0]))
    expect(paths.some(p => p.includes('/evidence-tasks'))).toBe(false)
  })

  it('says so when the evidence item no longer exists', async () => {
    // This is the case that used to close the dropdown and do nothing.
    withNotifications([
      {
        id: 'n5',
        reference_type: 'evidence',
        reference_id: 'e2c1d0e4-0000-0000-0000-000000000005',
        reference_key: null,
        message: 'Alex rejected evidence E-GONE-01',
        is_read: false,
        created_at: new Date().toISOString(),
      },
    ])
    render(<NotificationBell onNavigateToEvidence={vi.fn()} />)
    await openAndClick('Alex rejected evidence E-GONE-01')
    expect(await screen.findByRole('alert')).toHaveTextContent(/no longer exists/i)
  })

  it('says so for a reference type it has no destination for', async () => {
    // engagement_query fell past every branch: no navigation, dropdown left open,
    // indistinguishable from a missed tap.
    withNotifications([
      {
        id: 'n6',
        reference_type: 'engagement_query',
        reference_id: 'f2c1d0e4-0000-0000-0000-000000000006',
        reference_key: null,
        message: 'A query was raised on your engagement',
        is_read: false,
        created_at: new Date().toISOString(),
      },
    ])
    render(<NotificationBell />)
    await openAndClick('A query was raised on your engagement')
    expect(await screen.findByRole('alert')).toHaveTextContent(/nowhere to open/i)
  })

  it('gives each notification row a keyboard path', async () => {
    const onNavigateToEvidence = vi.fn()
    withNotifications([
      {
        id: 'n7',
        reference_type: 'evidence',
        reference_id: 'g2c1d0e4-0000-0000-0000-000000000007',
        reference_key: 'E-KEY-01',
        message: 'Alex rejected evidence E-KEY-01',
        is_read: false,
        created_at: new Date().toISOString(),
      },
    ])
    render(<NotificationBell onNavigateToEvidence={onNavigateToEvidence} />)
    await waitFor(() => expect(mockGet).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('button', { name: /notifications/i }))
    const row = (await screen.findByText('Alex rejected evidence E-KEY-01')).closest(
      '.notification-item',
    )!
    expect(row).toHaveAttribute('tabindex', '0')
    fireEvent.keyDown(row, { key: 'Enter' })
    await waitFor(() => expect(onNavigateToEvidence).toHaveBeenCalledWith('E-KEY-01'))
  })
})

describe('NotificationBell — risk notifications (PIG R7)', () => {
  // The expiry notice stores the risk_assessments row id; the register opens a
  // risk by its code, and an SCF code like R-GV-1 exists in every organisation.
  const expiry = {
    id: 'n-risk',
    organization_id: 'org-1',
    organization_name: 'Acme',
    type: 'risk_acceptance_expired',
    reference_type: 'risk',
    reference_id: 'ra-0001',
    reference_key: null,
    message: 'The acceptance of risk R-GV-1 lapsed after 2026-10-12. Renew it with a new rationale and expiry, or withdraw it.',
    is_read: false,
    created_at: new Date().toISOString(),
  }

  function routeGets(risks: unknown[]) {
    mockGet.mockImplementation(async (path: string) =>
      path.startsWith('/notifications')
        ? { unread_count: 1, notifications: [expiry] }
        : risks,
    )
  }

  it("opens the risk's detail page by its code, looked up in the notification's organisation", async () => {
    routeGets([
      { id: 'ra-0002', risk_code: 'R-GV-2' },
      { id: 'ra-0001', risk_code: 'R-GV-1' },
    ])
    const onNavigateToRisk = vi.fn()
    render(<NotificationBell currentOrgId="org-1" onNavigateToRisk={onNavigateToRisk} />)
    await openAndClick(expiry.message)
    await waitFor(() => expect(onNavigateToRisk).toHaveBeenCalledWith('R-GV-1'))
    expect(mockGet).toHaveBeenCalledWith('/organizations/org-1/risk-assessments')
    expect(screen.queryByText(expiry.message)).toBeNull()
  })

  it('does not open a same-code risk in the wrong organisation', async () => {
    routeGets([{ id: 'ra-0001', risk_code: 'R-GV-1' }])
    const onNavigateToRisk = vi.fn()
    render(<NotificationBell currentOrgId="org-2" onNavigateToRisk={onNavigateToRisk} />)
    await openAndClick(expiry.message)
    expect(await screen.findByRole('alert')).toHaveTextContent('That risk is in Acme. Switch to it to open the risk.')
    expect(onNavigateToRisk).not.toHaveBeenCalled()
    expect(mockGet).not.toHaveBeenCalledWith('/organizations/org-1/risk-assessments')
  })

  it('says so when the risk no longer exists', async () => {
    routeGets([{ id: 'ra-0002', risk_code: 'R-GV-2' }])
    const onNavigateToRisk = vi.fn()
    render(<NotificationBell currentOrgId="org-1" onNavigateToRisk={onNavigateToRisk} />)
    await openAndClick(expiry.message)
    expect(await screen.findByRole('alert')).toHaveTextContent('That risk no longer exists.')
    expect(onNavigateToRisk).not.toHaveBeenCalled()
  })
})

describe('NotificationBell — control notifications', () => {
  // The server stores the scoped_controls row id and resolves the control's
  // SCF id on read as reference_key. The bell opens the library through the
  // address bar, so these assert the URL the user lands on, and App's popstate
  // listener (the path Back takes) is what opens the control.
  const controlNotification = (
    type: string,
    message: string,
    referenceKey: string | null = 'AST-14',
  ) => ({
    id: `n-${type}`,
    organization_id: 'org-1',
    organization_name: 'Acme',
    type,
    reference_type: 'control',
    reference_id: 'sc-0001',
    reference_key: referenceKey,
    message,
    is_read: false,
    created_at: new Date().toISOString(),
  })
  const DUE = 'Control AST-14 is due for validation by 2026-10-20'

  beforeEach(() => window.history.replaceState(null, '', '/'))
  afterEach(() => window.history.replaceState(null, '', '/'))

  it.each([
    ['control_validation_due', DUE],
    ['control_ready_for_review', 'Sam marked control AST-14 as ready for review'],
    ['composite_insufficient', 'Composite evidence assessment for control AST-14 is now insufficient'],
  ])('opens a %s notification on its control, never on evidence', async (type, message) => {
    const notification = controlNotification(type, message)
    withNotifications([notification])
    const onNavigateToEvidence = vi.fn()
    const onPop = vi.fn()
    window.addEventListener('popstate', onPop)
    render(<NotificationBell currentOrgId="org-1" onNavigateToEvidence={onNavigateToEvidence} />)
    await openAndClick(message)
    await waitFor(() => expect(window.location.search).toBe('?tab=library&mode=in-scope&item=AST-14'))
    window.removeEventListener('popstate', onPop)
    expect(onPop).toHaveBeenCalledTimes(1)
    expect(onNavigateToEvidence).not.toHaveBeenCalled()
    expect(mockPatch).toHaveBeenCalledWith(`/notifications/${notification.id}/read`, {})
    expect(screen.queryByText(message)).toBeNull()
  })

  it('prefers the server key over the loaded scoping data', async () => {
    withNotifications([controlNotification('control_validation_due', DUE)])
    const resolveControlScfId = vi.fn(() => 'GOV-01')
    render(<NotificationBell currentOrgId="org-1" resolveControlScfId={resolveControlScfId} />)
    await openAndClick(DUE)
    await waitFor(() => expect(window.location.search).toBe('?tab=library&mode=in-scope&item=AST-14'))
    expect(resolveControlScfId).not.toHaveBeenCalled()
  })

  it('resolves from the loaded scoping data when the server sent no key', async () => {
    withNotifications([controlNotification('control_validation_due', DUE, null)])
    const resolveControlScfId = vi.fn((id: string) => (id === 'sc-0001' ? 'AST-14' : null))
    render(<NotificationBell currentOrgId="org-1" resolveControlScfId={resolveControlScfId} />)
    await openAndClick(DUE)
    await waitFor(() => expect(window.location.search).toBe('?tab=library&mode=in-scope&item=AST-14'))
    expect(resolveControlScfId).toHaveBeenCalledWith('sc-0001')
  })

  it('does not open a control on top of another organisation', async () => {
    withNotifications([controlNotification('control_validation_due', DUE)])
    const resolveControlScfId = vi.fn(() => 'AST-14')
    render(<NotificationBell currentOrgId="org-2" resolveControlScfId={resolveControlScfId} />)
    await openAndClick(DUE)
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'That control is in Acme. Switch to it to open the control.',
    )
    expect(window.location.search).toBe('')
    expect(resolveControlScfId).not.toHaveBeenCalled()
  })

  it.each([
    ['the scoping data has not loaded', () => undefined],
    ['there is no resolver', undefined],
  ])('opens the library list, with no notice, when %s', async (_case, resolveControlScfId) => {
    withNotifications([controlNotification('control_validation_due', DUE, null)])
    render(<NotificationBell currentOrgId="org-1" resolveControlScfId={resolveControlScfId} />)
    await openAndClick(DUE)
    await waitFor(() => expect(window.location.search).toBe('?tab=library&mode=in-scope'))
    expect(mockPatch).toHaveBeenCalledWith('/notifications/n-control_validation_due/read', {})
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByText(DUE)).toBeNull()
  })

  it('opens the library list and says so when the loaded data has no such control', async () => {
    withNotifications([controlNotification('control_validation_due', DUE, null)])
    render(<NotificationBell currentOrgId="org-1" resolveControlScfId={() => null} />)
    await openAndClick(DUE)
    expect(await screen.findByRole('alert')).toHaveTextContent('That control no longer exists.')
    expect(window.location.search).toBe('?tab=library&mode=in-scope')
  })

  it('clears an open control on the fallback, and Back returns to it', async () => {
    window.history.replaceState(null, '', '/?tab=library&mode=in-scope&item=GOV-01')
    withNotifications([controlNotification('control_validation_due', DUE, null)])
    render(<NotificationBell currentOrgId="org-1" resolveControlScfId={() => undefined} />)
    await openAndClick(DUE)
    await waitFor(() => expect(window.location.search).toBe('?tab=library&mode=in-scope'))
    window.history.back()
    await waitFor(() => expect(window.location.search).toBe('?tab=library&mode=in-scope&item=GOV-01'))
  })
})
