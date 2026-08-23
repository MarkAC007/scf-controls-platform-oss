/**
 * Resolves the guidance shown above the evidence upload panel (#789).
 *
 * The review filed this as "Evidence Guidance frequently renders GENERIC". It is
 * not occasional. `webclient/public/data/evidence_templates.json` ships **30**
 * hand-written templates; the SCF evidence catalogue has **316** active items. So
 * roughly nine items in ten fall to a fallback that says the same seven sentences
 * about "documentation that demonstrates this control is implemented" no matter
 * which artifact you are looking at — and then labels itself Generic, which reads
 * as an admission that the product has nothing to say.
 *
 * It does have something to say. The catalogue already carries, per item, an
 * `artifact_title`, an `artifact_description`, an `area_of_focus` and the
 * `control_mappings` the artifact answers; the organisation's own tracking row
 * carries the cadence, the collecting system and the collection method. None of
 * that reached this panel. The gap was never missing content — it was content
 * the backend had and the dashboard did not surface, which is the thesis this
 * module and the maturity widget share.
 *
 * Three tiers, most specific first:
 *
 *   template — a hand-written entry in evidence_templates.json. Always wins; a
 *              human wrote it about this exact artifact.
 *   derived  — assembled from the catalogue entry and the org's tracking row.
 *   generic  — the seven fixed sentences, when there is no catalogue entry to
 *              derive from either.
 *
 * The tier is returned rather than inferred by the caller, so the badge cannot
 * drift from the text underneath it.
 *
 * Deliberately pure and DOM-free: this is where the wording lives, and wording
 * is the thing worth testing without mounting six services to reach it.
 */
import type {
  ERLEntry,
  ERLFile,
  EvidenceId,
  EvidenceTemplatesFile,
  EvidenceTemplateGuidance,
  EvidenceTracking,
} from '../types'
import { frequencyLabel } from './frequencyVocabulary'
import { AMBER_GRACE_MULTIPLIER } from './freshnessRule'

export type GuidanceTier = 'template' | 'derived' | 'generic'

export interface ResolvedGuidance {
  tier: GuidanceTier
  guidance: EvidenceTemplateGuidance
}

export interface GuidanceInputs {
  templates?: EvidenceTemplatesFile
  erl?: ERLFile
  /** The organisation's own tracking row, when it has one. */
  tracking?: EvidenceTracking
}

/**
 * The formats the upload endpoint accepts. Deliberately the same list in the
 * derived tier as in the generic one: it is a property of the uploader, not of
 * the artifact, and the catalogue holds nothing per-item to narrow it with.
 * Pretending otherwise would be the failure this module exists to end — text
 * that looks specific and is not.
 */
const ACCEPTABLE_FORMATS = ['PDF', 'DOCX', 'XLSX', 'CSV', 'PNG', 'JPG']

const REDACTION_WARNINGS = [
  'Remove any personally identifiable information (PII) not relevant to the control',
]

/** The seven fixed sentences. Unchanged from what shipped — moved, not rewritten. */
export const GENERIC_GUIDANCE: EvidenceTemplateGuidance = {
  summary:
    'Upload documentation that demonstrates this control is implemented and operating effectively.',
  acceptable_formats: ACCEPTABLE_FORMATS,
  good_examples: [
    'Signed, dated policy or procedure document with version control',
    'System-generated report or export with timestamps',
  ],
  bad_examples: [
    'Screenshot without date or context',
    'Draft document without approval signatures',
  ],
  redaction_warnings: REDACTION_WARNINGS,
  freshness: 'Within the current audit period',
  auditor_tip:
    'Auditors look for evidence that is current, complete, and demonstrates consistent operation over the audit period.',
}

/** "AAA-01, BBB-02 and CCC-03", truncating a long tail rather than printing 40 ids. */
function listControls(ids: string[]): string {
  const shown = ids.slice(0, 4)
  const rest = ids.length - shown.length
  const joined =
    shown.length > 1
      ? `${shown.slice(0, -1).join(', ')} and ${shown[shown.length - 1]}`
      : shown[0]
  return rest > 0 ? `${joined} (and ${rest} more)` : joined
}

/**
 * How this item's freshness will actually be judged.
 *
 * Names the item's own cadence rather than "within the current audit period",
 * which is true of nothing the freshness engine computes. The 1.5x grace band is
 * imported from `freshnessRule` so this sentence cannot drift from the dashboard
 * legend or from `_calculate_status`.
 */
function derivedFreshness(tracking?: EvidenceTracking): string {
  if (!tracking?.frequency) {
    return (
      'No collection frequency is set for this item, so freshness cannot be ' +
      'judged and it will show as No Data. Set a frequency to start the clock.'
    )
  }
  const label = frequencyLabel(tracking.frequency).toLowerCase()
  return (
    `Collected ${label}. Fresh within that interval, Stale up to ` +
    `${AMBER_GRACE_MULTIPLIER}x it, Critical beyond that.`
  )
}

function derivedGoodExamples(entry: ERLEntry, tracking?: EvidenceTracking): string[] {
  const examples: string[] = []
  const title = entry.artifact_title

  examples.push(
    title
      ? `A dated, attributable ${title.toLowerCase()} — signed off or system-generated, not a working draft`
      : 'A dated, attributable artifact — signed off or system-generated, not a working draft',
  )

  const controls = entry.control_mappings ?? []
  if (controls.length > 0) {
    examples.push(
      `Content that visibly covers ${listControls(controls)} — the ` +
        `control${controls.length === 1 ? '' : 's'} this artifact is mapped to`,
    )
  }

  if (tracking?.collecting_system) {
    examples.push(
      `An export from ${tracking.collecting_system}, the system this ` +
        'organisation has recorded as the source for this item',
    )
  }

  return examples
}

function derivedBadExamples(entry: ERLEntry): string[] {
  const examples = [
    'Screenshot without date or context',
    'Draft document without approval signatures',
  ]
  if (entry.area_of_focus) {
    examples.push(
      `General material about ${entry.area_of_focus} that never evidences this ` +
        'specific artifact',
    )
  }
  return examples
}

function derivedAuditorTip(entry: ERLEntry): string {
  const controls = entry.control_mappings ?? []
  const scope = entry.area_of_focus
    ? `Auditors assess this under ${entry.area_of_focus}. `
    : ''
  const coverage =
    controls.length > 0
      ? `Be ready to show one artifact satisfies all ${controls.length} mapped ` +
        `control${controls.length === 1 ? '' : 's'}, or to supply a second for the gap. `
      : ''
  return (
    scope +
    coverage +
    'Evidence must be current, complete, and demonstrate consistent operation ' +
    'across the whole audit period rather than on the day it was collected.'
  )
}

/**
 * Guidance for one evidence item, plus which tier it came from.
 *
 * A catalogue entry with no `artifact_description` and no `artifact_title` is
 * treated as absent: derived guidance whose summary is empty would be worse than
 * the generic sentence, since the panel's first line would simply be blank.
 */
export function resolveEvidenceGuidance(
  evidenceId: EvidenceId,
  { templates, erl, tracking }: GuidanceInputs = {},
): ResolvedGuidance {
  const template = templates?.[evidenceId]
  if (template?.guidance) {
    return { tier: 'template', guidance: template.guidance }
  }

  const entry = erl?.[evidenceId]
  const summary = entry?.artifact_description || entry?.artifact_title
  if (!entry || !summary) {
    return { tier: 'generic', guidance: GENERIC_GUIDANCE }
  }

  return {
    tier: 'derived',
    guidance: {
      summary,
      acceptable_formats: ACCEPTABLE_FORMATS,
      good_examples: derivedGoodExamples(entry, tracking),
      bad_examples: derivedBadExamples(entry),
      redaction_warnings: REDACTION_WARNINGS,
      freshness: derivedFreshness(tracking),
      auditor_tip: derivedAuditorTip(entry),
    },
  }
}

/** Badge text per tier. `null` means "say nothing" — a template needs no label. */
export const GUIDANCE_TIER_BADGE: Record<GuidanceTier, string | null> = {
  template: null,
  derived: 'From SCF catalogue',
  generic: 'Generic',
}
