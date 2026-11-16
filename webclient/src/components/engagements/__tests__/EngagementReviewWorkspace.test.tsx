/**
 * EngagementReviewWorkspace: a deep link carries no framework, so when the
 * board cannot find the focus on one tab the workspace tries the next
 * framework of the engagement, and only gives up once every tab was tried.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import EngagementReviewWorkspace from '../EngagementReviewWorkspace'
import type { ReviewBoardProps } from '../ReviewBoard'
import type { AuditEngagement } from '../../../data/apiClient'

const missResults: boolean[] = []

vi.mock('../ReviewBoard', () => ({
  default: (p: ReviewBoardProps) => (
    <div data-testid="board">
      <span data-testid="board-framework">{p.framework}</span>
      <span data-testid="board-focus">{JSON.stringify(p.focus)}</span>
      <button type="button" onClick={() => { missResults.push(p.onFocusMissed?.() ?? false) }}>miss</button>
      <button type="button" onClick={p.onFocusConsumed}>consume</button>
    </div>
  ),
}))
vi.mock('../EngagementDashboard', () => ({ default: () => <div data-testid="dashboard" /> }))

const ENGAGEMENT: AuditEngagement = {
  id: 'eng-1', organization_id: 'org-1', name: 'CE and ISO', frameworks: ['ce', 'iso', 'nist'],
  status: 'active', start_date: null, end_date: null, created_by_user_id: null,
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', scope_count: 2,
}

function renderWorkspace() {
  render(
    <EngagementReviewWorkspace
      organizationId="org-1"
      engagement={ENGAGEMENT}
      frameworkName={id => id.toUpperCase()}
      onBack={vi.fn()}
      initialFocus={{ evidenceId: 'E-IAC-07' }}
    />,
  )
}

afterEach(() => { missResults.length = 0 })

describe('EngagementReviewWorkspace deep links (M2)', () => {
  it('moves the focus to each untried framework, then lets the board give up', async () => {
    const user = userEvent.setup()
    renderWorkspace()
    expect(screen.getByTestId('board-framework')).toHaveTextContent('ce')

    await user.click(screen.getByRole('button', { name: 'miss' }))
    expect(missResults).toEqual([true])
    expect(screen.getByTestId('board-framework')).toHaveTextContent('iso')
    expect(screen.getByTestId('board-focus')).toHaveTextContent('{"evidenceId":"E-IAC-07"}')
    expect(screen.getByRole('button', { name: 'ISO' })).toHaveAttribute('aria-pressed', 'true')

    await user.click(screen.getByRole('button', { name: 'miss' }))
    expect(screen.getByTestId('board-framework')).toHaveTextContent('nist')

    await user.click(screen.getByRole('button', { name: 'miss' }))
    expect(missResults).toEqual([true, true, false])
    expect(screen.getByTestId('board-framework')).toHaveTextContent('nist')
  })

  it('starts the search afresh for the next focus once one is consumed', async () => {
    const user = userEvent.setup()
    renderWorkspace()
    await user.click(screen.getByRole('button', { name: 'miss' }))
    await user.click(screen.getByRole('button', { name: 'consume' }))
    expect(screen.getByTestId('board-focus')).toHaveTextContent('null')
  })
})
