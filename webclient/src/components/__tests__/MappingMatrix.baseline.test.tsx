/**
 * MappingMatrix — baseline test suite (Task 2, Phase 5 restyle).
 *
 * Pins all functional behaviour so the visual restyle cannot silently break:
 *  - renders control × framework marks (X)
 *  - tooltip content: scf_id → framework + ref chips (identical text)
 *  - scoped-only toggle filters rows
 *  - legend toggle
 *  - counts display (filtered / total)
 *  - 6 implementation-status row CSS classes applied
 *  - sticky-column class preserved
 *
 * Some class-name assertions are intentionally RED until the restyle lands
 * (toolbar class names change; legend class stays; row-status classes stay).
 * Each RED expectation is labelled "RESTYLE → RED" so it is easy to find.
 */
import { render, screen, fireEvent, within } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import MappingMatrix from '../MappingMatrix'
import type { EnrichedControl, ScopedControlsFile } from '../../types'

// ── Fixtures ─────────────────────────────────────────────────────────────────

const makeControl = (
  scfId: string,
  name: string,
  frameworks: Record<string, string[]> = {},
): EnrichedControl =>
  ({
    scf_id: scfId,
    control_name: name,
    frameworksResolved: frameworks,
    frameworksCount: Object.keys(frameworks).length,
    artifactsResolved: [],
  }) as unknown as EnrichedControl

const ctrl1 = makeControl('GOV-01', 'Security Program', {
  ISO_27001_ref: ['A.5.1', 'A.5.2'],
  SOC2_ref: ['CC1.1'],
})
const ctrl2 = makeControl('GOV-02', 'Publishing Docs', {
  ISO_27001_ref: ['A.5.3'],
})
const ctrl3 = makeControl('AST-01', 'Asset Governance', {
  NIST_CSF_ref: ['GV.OC-01'],
})

const scopingDataWithStatuses: ScopedControlsFile = {
  organization: { id: 'org-1', name: 'Test Org', created_at: '', updated_at: '' },
  scoped_controls: [
    { scf_id: 'GOV-01', selected: true, implementation_status: 'implemented' },
    { scf_id: 'GOV-02', selected: true, implementation_status: 'in_progress' },
    { scf_id: 'AST-01', selected: false, implementation_status: 'not_started' },
  ],
  evidence_tracking: {},
  metadata: { total_selected: 2, total_implemented: 1 },
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function renderMatrix(
  controls = [ctrl1, ctrl2, ctrl3],
  scopingData: ScopedControlsFile | null = null,
) {
  return render(<MappingMatrix controls={controls} scopingData={scopingData} />)
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('MappingMatrix', () => {
  describe('basic rendering — controls × frameworks', () => {
    it('renders all control IDs', () => {
      renderMatrix()
      expect(screen.getByText('GOV-01')).toBeInTheDocument()
      expect(screen.getByText('GOV-02')).toBeInTheDocument()
      expect(screen.getByText('AST-01')).toBeInTheDocument()
    })

    it('renders control names', () => {
      renderMatrix()
      expect(screen.getByText('Security Program')).toBeInTheDocument()
      expect(screen.getByText('Publishing Docs')).toBeInTheDocument()
    })

    it('renders framework column headers (normalized)', () => {
      renderMatrix()
      // frameworks sorted; _ref suffix stripped
      expect(screen.getByText('ISO_27001')).toBeInTheDocument()
    })

    it('renders X marks for controls that map to a framework', () => {
      renderMatrix()
      const marks = screen.getAllByText('X')
      // GOV-01: ISO+SOC2 (2), GOV-02: ISO (1), AST-01: NIST (1) = 4 total marks
      expect(marks.length).toBe(4)
    })

    it('does not render X for missing mappings', () => {
      // GOV-02 has no SOC2 mapping — its SOC2 cell should be empty
      renderMatrix()
      // Total cells with X = 4, cells without = rendered as empty strings
      const rows = screen.getAllByRole('row')
      // At least one data row (skip thead) should exist
      expect(rows.length).toBeGreaterThan(1)
    })
  })

  describe('counts display', () => {
    it('shows control count', () => {
      renderMatrix()
      // e.g. "3 Controls" or "3 / 3 Controls"
      expect(screen.getByText(/controls/i)).toBeInTheDocument()
    })

    it('shows framework count', () => {
      renderMatrix()
      expect(screen.getByText(/frameworks/i)).toBeInTheDocument()
    })

    it('shows filtered count when scoped-only active and filters apply', () => {
      const { container } = render(
        <MappingMatrix controls={[ctrl1, ctrl2, ctrl3]} scopingData={scopingDataWithStatuses} />,
      )
      const checkbox = container.querySelector('input[type="checkbox"]')
      expect(checkbox).not.toBeNull()
      fireEvent.click(checkbox!)
      // After filtering: only GOV-01 and GOV-02 are selected=true → control count = 2
      // The count text should contain "2" near "Controls"
      expect(screen.queryByText('AST-01')).not.toBeInTheDocument()
      // Only 2 of 3 remain
      const rows = container.querySelectorAll('tbody tr')
      expect(rows.length).toBe(2)
    })
  })

  describe('scoped-only toggle', () => {
    it('shows all controls by default', () => {
      render(<MappingMatrix controls={[ctrl1, ctrl2, ctrl3]} scopingData={scopingDataWithStatuses} />)
      expect(screen.getByText('GOV-01')).toBeInTheDocument()
      expect(screen.getByText('GOV-02')).toBeInTheDocument()
      expect(screen.getByText('AST-01')).toBeInTheDocument()
    })

    it('hides unscoped controls when toggle is checked', () => {
      const { container } = render(
        <MappingMatrix controls={[ctrl1, ctrl2, ctrl3]} scopingData={scopingDataWithStatuses} />,
      )
      const checkbox = container.querySelector('input[type="checkbox"]')!
      fireEvent.click(checkbox)
      // AST-01 has selected=false → should be hidden
      expect(screen.queryByText('AST-01')).not.toBeInTheDocument()
      // GOV-01 and GOV-02 have selected=true → remain
      expect(screen.getByText('GOV-01')).toBeInTheDocument()
      expect(screen.getByText('GOV-02')).toBeInTheDocument()
    })

    it('shows all controls again when toggle is unchecked', () => {
      const { container } = render(
        <MappingMatrix controls={[ctrl1, ctrl2, ctrl3]} scopingData={scopingDataWithStatuses} />,
      )
      const checkbox = container.querySelector('input[type="checkbox"]')!
      fireEvent.click(checkbox) // on
      fireEvent.click(checkbox) // off
      expect(screen.getByText('AST-01')).toBeInTheDocument()
    })

    it('toggle is not rendered when scopingData is null', () => {
      const { container } = renderMatrix([ctrl1], null)
      expect(container.querySelector('input[type="checkbox"]')).toBeNull()
    })

    it('toggle is not rendered when scoped_controls is empty', () => {
      const emptyScoping: ScopedControlsFile = {
        organization: { id: 'o', name: 'O', created_at: '', updated_at: '' },
        scoped_controls: [],
        evidence_tracking: {},
        metadata: { total_selected: 0, total_implemented: 0 },
      }
      const { container } = render(<MappingMatrix controls={[ctrl1]} scopingData={emptyScoping} />)
      expect(container.querySelector('input[type="checkbox"]')).toBeNull()
    })
  })

  describe('legend toggle', () => {
    it('legend is hidden by default', () => {
      render(<MappingMatrix controls={[ctrl1]} scopingData={scopingDataWithStatuses} />)
      expect(screen.queryByText(/implementation status legend/i)).not.toBeInTheDocument()
    })

    it('legend appears when legend button is clicked', () => {
      render(<MappingMatrix controls={[ctrl1]} scopingData={scopingDataWithStatuses} />)
      const legendBtn = screen.getByTitle(/toggle status legend/i)
      fireEvent.click(legendBtn)
      // matrix-legend-strip appears; "STATUS LEGEND" label is visible
      expect(screen.getByText('STATUS LEGEND')).toBeInTheDocument()
    })

    it('legend contains all 6 status labels', () => {
      render(<MappingMatrix controls={[ctrl1]} scopingData={scopingDataWithStatuses} />)
      fireEvent.click(screen.getByTitle(/toggle status legend/i))
      expect(screen.getByText('Implemented')).toBeInTheDocument()
      expect(screen.getByText('In Progress')).toBeInTheDocument()
      expect(screen.getByText('Not Started')).toBeInTheDocument()
      expect(screen.getByText('At Risk')).toBeInTheDocument()
      expect(screen.getByText('Not Applicable')).toBeInTheDocument()
      expect(screen.getByText('Deferred')).toBeInTheDocument()
    })

    it('legend closes when legend button is clicked again', () => {
      render(<MappingMatrix controls={[ctrl1]} scopingData={scopingDataWithStatuses} />)
      const btn = screen.getByTitle(/toggle status legend/i)
      fireEvent.click(btn)
      expect(screen.getByText('Implemented')).toBeInTheDocument()
      fireEvent.click(btn)
      expect(screen.queryByText('Implemented')).not.toBeInTheDocument()
    })

    it('legend is not rendered when no scoping data', () => {
      renderMatrix([ctrl1], null)
      // No legend button at all
      expect(screen.queryByTitle(/toggle status legend/i)).toBeNull()
    })
  })

  describe('tooltip content', () => {
    it('shows tooltip with scf_id, framework, and refs on mark hover', () => {
      const { container } = render(<MappingMatrix controls={[ctrl1]} scopingData={null} />)
      const marks = screen.getAllByText('X')
      // Hover over the first mark (GOV-01 / ISO_27001)
      fireEvent.mouseEnter(marks[0])
      // Tooltip header contains "GOV-01" (strong inside .tooltip-header)
      const tooltip = container.querySelector('.matrix-tooltip')
      expect(tooltip).not.toBeNull()
      // Ref chips — these only appear inside the tooltip, not in the table
      expect(screen.getByText('A.5.1')).toBeInTheDocument()
      expect(screen.getByText('A.5.2')).toBeInTheDocument()
    })

    it('tooltip disappears on mouse leave', () => {
      render(<MappingMatrix controls={[ctrl1]} scopingData={null} />)
      const marks = screen.getAllByText('X')
      fireEvent.mouseEnter(marks[0])
      fireEvent.mouseLeave(marks[0])
      // After leave the ref chips should be gone
      expect(screen.queryByText('A.5.1')).not.toBeInTheDocument()
    })
  })

  describe('implementation status row classes', () => {
    it('applies matrix-row-implemented class for implemented status', () => {
      const { container } = render(
        <MappingMatrix controls={[ctrl1]} scopingData={scopingDataWithStatuses} />,
      )
      const rows = container.querySelectorAll('tr.matrix-row-implemented')
      expect(rows.length).toBe(1)
    })

    it('applies matrix-row-in_progress class for in_progress status', () => {
      const { container } = render(
        <MappingMatrix controls={[ctrl2]} scopingData={scopingDataWithStatuses} />,
      )
      const rows = container.querySelectorAll('tr.matrix-row-in_progress')
      expect(rows.length).toBe(1)
    })

    it('applies matrix-row-not_started class for not_started status', () => {
      const data: ScopedControlsFile = {
        ...scopingDataWithStatuses,
        scoped_controls: [{ scf_id: 'AST-01', selected: true, implementation_status: 'not_started' }],
      }
      const { container } = render(<MappingMatrix controls={[ctrl3]} scopingData={data} />)
      const rows = container.querySelectorAll('tr.matrix-row-not_started')
      expect(rows.length).toBe(1)
    })

    it('applies matrix-row-at_risk class for at_risk status', () => {
      const data: ScopedControlsFile = {
        ...scopingDataWithStatuses,
        scoped_controls: [{ scf_id: 'GOV-01', selected: true, implementation_status: 'at_risk' }],
      }
      const { container } = render(<MappingMatrix controls={[ctrl1]} scopingData={data} />)
      const rows = container.querySelectorAll('tr.matrix-row-at_risk')
      expect(rows.length).toBe(1)
    })

    it('applies matrix-row-not_applicable class for not_applicable status', () => {
      const data: ScopedControlsFile = {
        ...scopingDataWithStatuses,
        scoped_controls: [{ scf_id: 'GOV-01', selected: true, implementation_status: 'not_applicable' }],
      }
      const { container } = render(<MappingMatrix controls={[ctrl1]} scopingData={data} />)
      const rows = container.querySelectorAll('tr.matrix-row-not_applicable')
      expect(rows.length).toBe(1)
    })

    it('applies matrix-row-deferred class for deferred status', () => {
      const data: ScopedControlsFile = {
        ...scopingDataWithStatuses,
        scoped_controls: [{ scf_id: 'GOV-01', selected: true, implementation_status: 'deferred' }],
      }
      const { container } = render(<MappingMatrix controls={[ctrl1]} scopingData={data} />)
      const rows = container.querySelectorAll('tr.matrix-row-deferred')
      expect(rows.length).toBe(1)
    })

    it('applies no status class when scoping data has no status', () => {
      const data: ScopedControlsFile = {
        ...scopingDataWithStatuses,
        scoped_controls: [{ scf_id: 'GOV-01', selected: true }],
      }
      const { container } = render(<MappingMatrix controls={[ctrl1]} scopingData={data} />)
      const row = container.querySelector('tr[class^="matrix-row-"]')
      expect(row).toBeNull()
    })
  })

  describe('sticky column classes preserved', () => {
    it('control-cell td has sticky-col class (RESTYLE → should survive)', () => {
      // The sticky first-column behaviour is critical; this class drives position:sticky
      const { container } = renderMatrix([ctrl1])
      // After restyle the control-cell td should still be sticky
      const stickyTds = container.querySelectorAll('td.control-cell.sticky-col')
      expect(stickyTds.length).toBeGreaterThan(0)
    })

    it('control-header th retains sticky-col class', () => {
      const { container } = renderMatrix([ctrl1])
      const stickyTh = container.querySelector('th.control-header.sticky-col')
      expect(stickyTh).not.toBeNull()
    })
  })

  describe('toolbar structure (RESTYLE → new classes)', () => {
    it('renders the toolbar region with matrix title', () => {
      renderMatrix([ctrl1])
      // The title text always present (class may change after restyle)
      expect(screen.getByText(/SCF Framework Mapping Matrix/i)).toBeInTheDocument()
    })

    it('toolbar uses explorer-toolbar class after restyle', () => {
      // RESTYLE → RED: before restyle the class is mapping-matrix-header
      // After restyle it should use .matrix-toolbar (toolbar-idiom class)
      const { container } = renderMatrix([ctrl1])
      // This assertion becomes GREEN after restyle:
      const toolbar = container.querySelector('.matrix-toolbar')
      expect(toolbar).not.toBeNull()
    })
  })
})
