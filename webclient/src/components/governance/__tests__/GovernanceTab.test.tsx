import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import GovernanceTab from '../GovernanceTab'

const stub = vi.hoisted(() => (name: string) => ({
  default: ({ scfId }: { scfId: string }) => <div data-testid={name} data-scf-id={scfId} />,
}))

vi.mock('../ObligationBasisPanel', () => stub('basis'))
vi.mock('../TargetMaturityPanel', () => stub('target'))
vi.mock('../MaterialityPanel', () => stub('materiality'))
vi.mock('../ProcedureDocumentPanel', () => stub('procedure'))
vi.mock('../DeficiencyPanel', () => stub('deficiencies'))
vi.mock('../CompensatingControlPanel', () => stub('compensating'))
vi.mock('../ValidationPanel', () => stub('validation'))

describe('GovernanceTab', () => {
  it("mounts the control's deficiency panel (PIG R8) with the other governance panels", () => {
    render(<GovernanceTab organizationId="org-one" scfId="TESTCONTROL-01" />)
    expect(screen.getByTestId('deficiencies')).toHaveAttribute('data-scf-id', 'TESTCONTROL-01')
    expect(screen.getByTestId('procedure')).toBeInTheDocument()
  })

  it("mounts the control's compensating controls panel (PIG R9) after its deficiencies", () => {
    render(<GovernanceTab organizationId="org-one" scfId="TESTCONTROL-01" />)
    const panel = screen.getByTestId('compensating')
    expect(panel).toHaveAttribute('data-scf-id', 'TESTCONTROL-01')
    expect(screen.getByTestId('deficiencies').compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it("mounts the control's validation panel (PIG R10) after its compensating controls", () => {
    render(<GovernanceTab organizationId="org-one" scfId="TESTCONTROL-01" />)
    const panel = screen.getByTestId('validation')
    expect(panel).toHaveAttribute('data-scf-id', 'TESTCONTROL-01')
    expect(screen.getByTestId('compensating').compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})
