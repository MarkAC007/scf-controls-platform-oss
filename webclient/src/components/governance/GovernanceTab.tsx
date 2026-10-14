import type { JSX } from 'react'
import CompensatingControlPanel from './CompensatingControlPanel'
import DeficiencyPanel from './DeficiencyPanel'
import MaterialityPanel from './MaterialityPanel'
import ObligationBasisPanel from './ObligationBasisPanel'
import ProcedureDocumentPanel from './ProcedureDocumentPanel'
import TargetMaturityPanel from './TargetMaturityPanel'
import ValidationPanel from './ValidationPanel'

interface Props {
  organizationId: string
  scfId: string
  /** Called after a panel saves, so the page reloads what it displays (the target). */
  onChange?: () => void
}

/* The control's PIG governance record: why it is an obligation, the level it is
   meant to reach, whether it is material, the procedure that operates it, its
   deficiencies, its compensating controls, and its validation schedule. Each
   panel reads and writes its own API. */
export default function GovernanceTab({ organizationId, scfId, onChange }: Props): JSX.Element {
  return (
    <div className="gov-tab">
      <ObligationBasisPanel key={scfId} organizationId={organizationId} scfId={scfId} />
      <TargetMaturityPanel key={`target-${scfId}`} organizationId={organizationId} scfId={scfId} onChange={onChange} />
      <MaterialityPanel key={`material-${scfId}`} organizationId={organizationId} scfId={scfId} onChange={onChange} />
      <ProcedureDocumentPanel key={`procedure-${scfId}`} organizationId={organizationId} scfId={scfId} onChange={onChange} />
      <DeficiencyPanel key={`deficiency-${scfId}`} organizationId={organizationId} scfId={scfId} />
      <CompensatingControlPanel key={`compensating-${scfId}`} organizationId={organizationId} scfId={scfId} />
      <ValidationPanel key={`validation-${scfId}`} organizationId={organizationId} scfId={scfId} />
    </div>
  )
}
