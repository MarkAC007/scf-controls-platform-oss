/**
 * Assessment engine API client — which engine judges an organisation's
 * evidence, and what the Jev shadow has said alongside Claude.
 *
 * Three engines, chosen per organisation (the choice itself round-trips
 * through the org settings PATCH, not through this module):
 *   - `llm`        Claude decides. Today's behaviour and the default.
 *   - `jev_shadow` Claude decides; Jev assesses the same objectives in the
 *                  background and the two verdicts are compared.
 *   - `jev`        Jev decides.
 *
 * Conventions are evidenceStorageApi.ts's: the shared `fetchWithAuthRetry`
 * (one OIDC refresh-and-retry on 401) and the shared error unwrapping, and the
 * thrown Error keeps its `.status` so a caller can branch without parsing text.
 */

import { errorMessageFromBody, fetchWithAuthRetry } from './integrationsApi'

const API_BASE_URL = '/api'

export type AssessmentEngine = 'llm' | 'jev_shadow' | 'jev'

/**
 * Running totals of the shadow comparison for one organisation. Every rate is
 * a 0..1 float, or null while nothing has been compared — a null is "no data",
 * never "0% agreement".
 */
export interface ShadowStats {
  compared_verdicts: number
  failed_verdicts: number
  objectives_compared: number
  objectives_agreed: number
  agreement_rate: number | null
  /** Objectives where Jev's confidence cleared the cutoff. */
  confident_objectives: number
  confident_agreed: number
  confident_agreement_rate: number | null
  status_agreement_rate: number | null
  mean_latency_ms: number | null
  total_cost_cents: number | null
  last_compared_at: string | null
}

export interface AssessmentEngineStatus {
  engine: AssessmentEngine
  /** Whether a TypeSafe key is available to the backend at all. */
  typesafe_key_configured: boolean
  jev_model_id: string
  confidence_cutoff: number
  shadow_stats: ShadowStats
}

/**
 * One objective as Jev judged it. `confidence` is null when the model did not
 * report a usable figure; the panel treats that as below the cutoff, since an
 * unknown confidence is not a confident one.
 */
export interface ShadowAOFinding {
  ao_id: string
  suggested_designation: string
  confidence: number | null
  probabilities: Record<string, number>
}

/** Where Claude and Jev differ on one objective. */
export interface ShadowDisagreement {
  ao_id: string
  llm: string
  jev: string
  confidence: number | null
}

export interface ShadowComparison {
  compared: number
  agreed: number
  agreement_rate: number | null
  confident_total: number
  confident_agreed: number
  confident_agreement_rate: number | null
  llm_status: string | null
  jev_status: string | null
  status_agrees: boolean
  disagreements: ShadowDisagreement[]
}

/**
 * Jev's verdict on one evidence file, written only in `jev_shadow` mode (in
 * `jev` mode Jev's verdict IS the assessment, and no shadow row exists).
 * `comparison` is null when the call failed; `error` is then set, and the row
 * is still written so a miss is visible. `version_id` names the Claude
 * assessment version this shadow compared against.
 */
export interface ShadowVerdict {
  id: string
  evidence_file_id: string
  assessment_id: string
  version_id: string | null
  engine: string
  model_id: string | null
  status: string | null
  relevance_score: number | null
  ao_findings: ShadowAOFinding[]
  gap_count: number
  cannot_assess_count: number
  low_confidence_count: number
  confidence_cutoff: number | null
  comparison: ShadowComparison | null
  state_truncated: boolean
  input_token_count: number | null
  output_token_count: number | null
  cost_cents: number | null
  processing_time_ms: number | null
  error: string | null
  created_at: string
}

/** An API failure that kept its HTTP status for the caller to branch on. */
export type AssessmentEngineApiError = Error & { status?: number }

async function engineFetch<T>(endpoint: string): Promise<T> {
  const response = await fetchWithAuthRetry((bearer) =>
    fetch(`${API_BASE_URL}${endpoint}`, {
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${bearer}`,
      },
    })
  )

  if (!response.ok) {
    const fallback = `API Error: ${response.status} ${response.statusText}`
    let body: unknown = null
    try {
      body = await response.json()
    } catch {
      // non-JSON error body — the status text is all there is
    }
    const error: AssessmentEngineApiError = new Error(errorMessageFromBody(body, fallback))
    error.status = response.status
    throw error
  }

  return response.json()
}

export async function getAssessmentEngineStatus(orgId: string): Promise<AssessmentEngineStatus> {
  return engineFetch<AssessmentEngineStatus>(`/organizations/${orgId}/evidence-assessment/engine`)
}

/**
 * Jev's shadow verdict for one file, or null when there is none.
 *
 * Only a 404 becomes null, as in `getAssessment`: "no shadow ran" and "we could
 * not find out" are different answers.
 */
export async function getShadowVerdict(
  orgId: string,
  evidenceId: string,
  fileId: string,
): Promise<ShadowVerdict | null> {
  try {
    return await engineFetch<ShadowVerdict>(
      `/organizations/${orgId}/evidence/${evidenceId}/files/${fileId}/assessment/shadow`,
    )
  } catch (err: unknown) {
    if ((err as AssessmentEngineApiError | null)?.status === 404) return null
    throw err
  }
}
