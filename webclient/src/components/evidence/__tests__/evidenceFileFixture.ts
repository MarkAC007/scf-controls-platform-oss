import type { EvidenceFileResponse, PreparerAssertions } from '../../../data/apiClient'

/**
 * One evidence-file fixture, shared by every test in this folder.
 *
 * `EvidenceFileResponse` has grown twice in quick succession — integrity
 * columns, then preparer assertions — and each time every inline fixture in
 * every test broke at once, for a reason unrelated to what those tests were
 * about. Adding a column should touch this file and nothing else.
 */

/**
 * The state most evidence is actually in: a person asserted nothing about it.
 * Explicitly null rather than absent, because "not asserted" is a fact the
 * product displays, not a gap in the fixture.
 */
export const UNASSERTED: PreparerAssertions = {
  effective_period_start: null,
  effective_period_end: null,
  population_size: null,
  population_source: null,
  sample_size: null,
  sample_method: null,
  sample_basis: null,
  ipe_source_system: null,
  ipe_query_or_filter: null,
  ipe_extracted_by_user_id: null,
  ipe_extracted_at: null,
  ipe_completeness_check: null,
}

export function makeEvidenceFile(
  overrides: Partial<EvidenceFileResponse> = {},
): EvidenceFileResponse {
  return {
    id: 'file-1',
    organization_id: 'org-1',
    evidence_id: 'ERL-001',
    filename: 'report.pdf',
    s3_key: 'evidence/org-1/2026/02/abc_report.pdf',
    content_type: 'application/pdf',
    file_size_bytes: 1024,
    sha256_hash: null,
    classification: 'internal',
    scan_status: 'clean',
    computed_sha256: null,
    hash_verification_status: 'verified',
    hash_verified_at: '2026-08-23T12:00:00Z',
    integrity_badge: null,
    uploaded_by_user_id: null,
    uploaded_at: '2026-08-23T10:00:00Z',
    expires_at: null,
    is_deleted: false,
    download_url: '/download',
    uploaded_by: null,
    review_status: 'not_reviewed',
    reviewed_by_user_id: null,
    reviewed_at: null,
    review_notes: null,
    reviewed_by: null,
    ...UNASSERTED,
    ...overrides,
  }
}
