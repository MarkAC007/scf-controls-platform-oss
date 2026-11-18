// SPDX-License-Identifier: AGPL-3.0-only
/**
 * The dashboard's control maturity, measured against target: reads the
 * organisation's domain targets and hands the standing to its block.
 */
import { useMemo } from 'react'
import type { EnrichedControl, ScopedControlsFile } from '../../types'
import { useOrganizationSettings } from '../../hooks/useOrganizationSettings'
import { targetStanding } from '../../data/targetMaturity'
import TargetStandingBlock from './TargetStandingBlock'

interface Props {
  controls: EnrichedControl[]
  scopingData: ScopedControlsFile
}

export default function ControlTargetStanding({ controls, scopingData }: Props): JSX.Element | null {
  const settings = useOrganizationSettings(scopingData.organizationId)
  const domainTargets = settings.data?.maturity_targets
  const standing = useMemo(() => {
    const scoped = new Map(scopingData.scoped_controls.map(entry => [entry.scf_id, entry]))
    return targetStanding(
      controls.flatMap(control => {
        const entry = scoped.get(control.scf_id)
        return entry?.selected
          ? [{
              scf_id: control.scf_id,
              domainName: control.scf_domain,
              maturity: entry.maturity_level,
              targetOverride: entry.target_maturity_override,
            }]
          : []
      }),
      domainTargets,
    )
  }, [controls, scopingData.scoped_controls, domainTargets])

  // Until the settings are in, the domain targets are unknown: saying "no
  // control has a target" then would be a guess.
  if (!settings.data) return null
  return <TargetStandingBlock standing={standing} />
}
