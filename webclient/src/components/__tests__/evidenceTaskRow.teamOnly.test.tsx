/**
 * The collapsed evidence task row answers ownership with a team, at every width.
 *
 * Two halves, because the defect had two halves and either one alone still
 * produces the symptom:
 *
 * 1. **Markup.** The row used to carry an individual-assignee label that said
 *    `Unassigned` whenever no person was named -- on tasks a team plainly owned.
 * 2. **Stylesheet.** The responsive rules hid the owning-team badge at a wider
 *    breakpoint than the assignee label. Between those two widths the row
 *    dropped the field carrying the truth and kept the field being retired, so
 *    the only thing it said about ownership was the wrong thing.
 *
 * The stylesheet half reads the source text with `?raw` on purpose. jsdom does
 * not evaluate media queries at all, so a render-only assertion passes just as
 * happily against the broken stylesheet as against the fixed one -- and fetching
 * the stylesheet at runtime is no better, because the dev server hands back a
 * JavaScript module rather than CSS.
 */
import { render, screen, waitFor } from '@testing-library/react'
import STYLESHEET from '../../styles.css?raw'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { EvidenceTaskList } from '../EvidenceTaskList'
import {
  apiClient,
  getOrgMemberSummaries,
  getTeam,
  listTeamAssignments,
  listTeams,
} from '../../data/apiClient'
import type { Team, TeamAssignment, TeamDetail } from '../../types'

vi.mock('../../data/apiClient', () => ({
  apiClient: { get: vi.fn(), patch: vi.fn(), post: vi.fn() },
  listTeamAssignments: vi.fn(),
  listTeams: vi.fn(),
  getTeam: vi.fn(),
  getOrgMemberSummaries: vi.fn(),
}))

vi.mock('../ModernCommentThread', () => ({ ModernCommentThread: () => null }))

const mockApi = vi.mocked(apiClient)
const mockListAssignments = vi.mocked(listTeamAssignments)
const mockListTeams = vi.mocked(listTeams)
const mockGetTeam = vi.mocked(getTeam)
const mockMembers = vi.mocked(getOrgMemberSummaries)

const ORG = 'org-team-only'
const TRACKING = 'tracking-team-only'

const TEAMS: Team[] = [
  {
    id: 'team-platform',
    organization_id: ORG,
    function_id: 'fn-platform',
    name: 'Platform Engineering',
    description: null,
    is_active: true,
  },
]

const ACCOUNTABLE: TeamAssignment = {
  id: 'assign-team-only',
  type: 'evidence',
  item_id: TRACKING,
  team_id: 'team-platform',
  organization_id: ORG,
  is_accountable: true,
  assigned_at: '2026-01-01T00:00:00',
  team: {
    id: 'team-platform',
    name: 'Platform Engineering',
    is_active: true,
    function_id: 'fn-platform',
    function: {
      id: 'fn-platform',
      key: 'platform',
      name: 'Platform Engineering',
      is_active: true,
    },
    primary: {
      user_id: 'u-primary',
      membership_role: 'primary',
      user: {
        id: 'u-primary',
        email: 'primary@example.com',
        display_name: 'Team Primary',
      },
    },
    delegate: null,
  },
}

const TEAM_DETAIL: TeamDetail = {
  id: 'team-platform',
  organization_id: ORG,
  function_id: 'fn-platform',
  name: 'Platform Engineering',
  description: null,
  is_active: true,
  members: [],
  health: {
    has_primary: true,
    has_members: true,
    function_is_active: true,
    warnings: [],
  },
}

/**
 * One row with nobody named and one row still carrying a person from before the
 * rule changed. Both must read the same way: the team owns it.
 */
const TASKS = [
  {
    id: 'task-no-person',
    evidence_tracking_id: TRACKING,
    task_type: 'collection',
    title: 'Collect the quarterly export',
    priority: 'medium',
    status: 'not_started',
    due_date: '2026-12-31',
    owning_team_id: null,
  },
  {
    id: 'task-legacy-person',
    evidence_tracking_id: TRACKING,
    task_type: 'review',
    title: 'Review the quarterly export',
    priority: 'high',
    status: 'not_started',
    due_date: '2026-12-31',
    owning_team_id: null,
    assigned_user: {
      id: 'u-departed',
      email: 'departed@example.com',
      display_name: 'Departed Person',
    },
  },
]

beforeEach(() => {
  vi.clearAllMocks()
  mockApi.get.mockResolvedValue(TASKS)
  mockListTeams.mockResolvedValue(TEAMS)
  mockListAssignments.mockResolvedValue({ [TRACKING]: [ACCOUNTABLE] })
  mockGetTeam.mockResolvedValue(TEAM_DETAIL)
  mockMembers.mockResolvedValue([])
})

function renderList() {
  return render(
    <EvidenceTaskList
      evidenceTrackingId={TRACKING}
      evidenceId="E-TEAM-ONLY"
      organizationId={ORG}
    />
  )
}

describe('collapsed evidence task row: ownership', () => {
  it('names the owning team and never says Unassigned', async () => {
    renderList()

    expect(
      await screen.findByText('Collect the quarterly export')
    ).toBeInTheDocument()
    await waitFor(
      () =>
        expect(
          screen.getAllByText('Platform Engineering').length
        ).toBeGreaterThan(0),
      { timeout: 5000 }
    )

    expect(screen.queryByText('Unassigned')).not.toBeInTheDocument()
  })

  it('does not put a legacy individual on the row beside the team', async () => {
    renderList()

    expect(
      await screen.findByText('Review the quarterly export')
    ).toBeInTheDocument()
    await waitFor(
      () =>
        expect(
          screen.getAllByText('Platform Engineering').length
        ).toBeGreaterThan(0),
      { timeout: 5000 }
    )

    // The row still receives the field; it simply no longer answers with it.
    expect(screen.queryByText('Departed Person')).not.toBeInTheDocument()
    expect(screen.queryByText('Unassigned')).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// The responsive half
// ---------------------------------------------------------------------------


/** Every `max-width` breakpoint whose block hides the given selector text. */
function widthsHiding(selector: string): number[] {
  const widths: number[] = []
  const blockStart = /@media[^{]*?\(max-width:\s*(\d+)px\s*\)[^{]*\{/g
  let match: RegExpExecArray | null

  while ((match = blockStart.exec(STYLESHEET)) !== null) {
    const width = Number(match[1])
    // Walk to the matching close brace of the media block.
    let depth = 1
    let i = blockStart.lastIndex
    while (i < STYLESHEET.length && depth > 0) {
      if (STYLESHEET[i] === '{') depth += 1
      else if (STYLESHEET[i] === '}') depth -= 1
      i += 1
    }
    const body = STYLESHEET.slice(blockStart.lastIndex, i)
    if (body.includes(selector) && /display:\s*none/.test(body)) {
      widths.push(width)
    }
  }
  return widths
}

describe('evidence task row: responsive ownership', () => {
  it('never hides the owning-team badge on a narrow viewport', () => {
    // The badge may be restyled at any width; it may not be removed. A row that
    // has run out of horizontal space can drop a date -- the title and status
    // imply the urgency -- but "who has this" is not implied by anything else
    // on the row, and a narrow viewport is where it is most needed.
    expect(widthsHiding('.evidence-task-row .task-owning-team-badge')).toEqual(
      []
    )
  })

  it('has no individual-assignee column left to outlive the team badge', () => {
    // The original defect was an ordering one: the team badge went first, the
    // assignee label second. Removing the assignee column is what makes the
    // ordering unfalsifiable rather than merely currently correct.
    expect(STYLESHEET).not.toContain('evidence-task-row-assignee')
  })
})
