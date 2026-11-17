// SPDX-License-Identifier: AGPL-3.0-only
/**
 * Engagement notifications open the engagement's review board on the card.
 *
 * Before this, an `engagement_query` notification (and the new evidence review
 * ones) fell to "There is nowhere to open this notification yet."
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { NotificationBell } from '../NotificationBell'
import { apiClient } from '../../data/apiClient'

vi.mock('../../data/apiClient', () => ({
  apiClient: { get: vi.fn(), patch: vi.fn() },
}))

const mockGet = vi.mocked(apiClient.get)
const mockPatch = vi.mocked(apiClient.patch)

const REVIEW_ROW = {
  id: 'n1',
  reference_type: 'engagement_evidence_review',
  reference_id: 'review-1',
  engagement_id: 'eng-1',
  organization_id: 'org-1',
  organization_name: 'Acme',
  message: 'Auditor needs more on evidence E-IAC-07',
  is_read: false,
  created_at: new Date().toISOString(),
}

const QUERY_ROW = {
  ...REVIEW_ROW,
  id: 'n2',
  reference_type: 'engagement_query',
  reference_id: 'query-1',
  message: 'A query was raised on evidence E-IAC-07',
}

function withNotifications(notifications: unknown[], query?: unknown) {
  mockGet.mockImplementation(async (url: string) => {
    if (url.startsWith('/notifications')) return { unread_count: notifications.length, notifications }
    if (url.includes('/queries/')) return query
    throw new Error(`unexpected GET ${url}`)
  })
}

async function openAndClick(message: string) {
  await waitFor(() => expect(mockGet).toHaveBeenCalled())
  fireEvent.click(screen.getByRole('button', { name: /notifications/i }))
  fireEvent.click(await screen.findByText(message))
}

beforeEach(() => {
  vi.clearAllMocks()
  mockPatch.mockResolvedValue({})
})

describe('NotificationBell engagement notifications', () => {
  it('opens the review board on the card an evidence review notification names', async () => {
    const onOpen = vi.fn()
    withNotifications([REVIEW_ROW])
    render(<NotificationBell onNavigateToEngagementReview={onOpen} currentOrgId="org-1" />)
    await openAndClick(REVIEW_ROW.message)
    await waitFor(() => expect(onOpen).toHaveBeenCalledWith({
      organizationId: 'org-1', engagementId: 'eng-1', focus: { reviewId: 'review-1' },
    }))
  })

  it('opens it for an external auditor, who has no organisation on screen', async () => {
    const onOpen = vi.fn()
    withNotifications([REVIEW_ROW])
    render(<NotificationBell onNavigateToEngagementReview={onOpen} currentOrgId={null} />)
    await openAndClick(REVIEW_ROW.message)
    await waitFor(() => expect(onOpen).toHaveBeenCalledTimes(1))
  })

  it('resolves a query notification to the evidence object the query is on', async () => {
    const onOpen = vi.fn()
    withNotifications([QUERY_ROW], { id: 'query-1', evidence_id: 'E-IAC-07', scf_id: null })
    render(<NotificationBell onNavigateToEngagementReview={onOpen} currentOrgId="org-1" />)
    await openAndClick(QUERY_ROW.message)
    await waitFor(() => expect(onOpen).toHaveBeenCalledWith({
      organizationId: 'org-1', engagementId: 'eng-1', focus: { evidenceId: 'E-IAC-07' },
    }))
    expect(mockGet).toHaveBeenCalledWith('/organizations/org-1/engagements/eng-1/queries/query-1')
  })

  it('resolves a control-level query notification to the control', async () => {
    const onOpen = vi.fn()
    withNotifications([QUERY_ROW], { id: 'query-1', evidence_id: null, scf_id: 'GOV-01' })
    render(<NotificationBell onNavigateToEngagementReview={onOpen} currentOrgId="org-1" />)
    await openAndClick(QUERY_ROW.message)
    await waitFor(() => expect(onOpen).toHaveBeenCalledWith({
      organizationId: 'org-1', engagementId: 'eng-1', focus: { controlScfId: 'GOV-01' },
    }))
  })

  it('does not open a card of another organisation on top of the one on screen', async () => {
    const onOpen = vi.fn()
    withNotifications([REVIEW_ROW])
    render(<NotificationBell onNavigateToEngagementReview={onOpen} currentOrgId="org-2" />)
    await openAndClick(REVIEW_ROW.message)
    expect(await screen.findByText(/That engagement is in Acme/)).toBeInTheDocument()
    expect(onOpen).not.toHaveBeenCalled()
  })

  it('says so when the query can no longer be read (access ended)', async () => {
    const onOpen = vi.fn()
    mockGet.mockImplementation(async (url: string) => {
      if (url.startsWith('/notifications')) return { unread_count: 1, notifications: [QUERY_ROW] }
      throw new Error('Not found')
    })
    render(<NotificationBell onNavigateToEngagementReview={onOpen} currentOrgId="org-1" />)
    await openAndClick(QUERY_ROW.message)
    expect(await screen.findByText('Could not open that item.')).toBeInTheDocument()
    expect(onOpen).not.toHaveBeenCalled()
  })
})
