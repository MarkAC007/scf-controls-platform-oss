import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// Mocked before the module under test is imported, so loadScopedControls sees the stubs.
vi.mock('../apiClient', () => ({
  getCurrentOrganization: vi.fn(),
  getScopedControls: vi.fn(),
  getEvidenceTracking: vi.fn(),
  createOrUpdateEvidenceTracking: vi.fn(),
  updateScopedControl: vi.fn(),
}))

import * as api from '../apiClient'
import { loadScopedControls, exportScopedControls } from '../scopingService'
import type { ScopedControlsFile } from '../../types'

const ORG = {
  id: 'org-1',
  name: 'Example Organization',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
}

const EVIDENCE_KEY = 'evidence-fixture-one'

/**
 * Every field the wire type `apiClient.EvidenceTracking` declares. The
 * completeness test below walks this object, so adding a field to the API type
 * and forgetting it here is caught in review, while adding it here and
 * forgetting the mapper is caught by the test.
 */
const API_ROW = {
  id: 'et-1',
  organization_id: ORG.id,
  evidence_id: EVIDENCE_KEY,
  is_tracked: true,
  method_of_collection: 'automated',
  collecting_system: 'Example IdP',
  assigned_user_id: 'u-1',
  owner_user_id: 'u-2',
  assigned_user: { id: 'u-1', email: 'a@example.com', display_name: 'Ada L' },
  owner_user: { id: 'u-2', email: 'o@example.com', display_name: 'Owen R' },
  frequency: 'quarterly',
  comments: 'collected from the directory export',
  maturity_level: 'L2',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-09-15T12:36:13Z',
}

/**
 * Wire-only keys the UI shape deliberately does not carry.
 *
 * - `organization_id` — the file already has `organizationId` at the top level.
 * - `evidence_id`     — it becomes the record's key, so keeping it on the value
 *                       too would be a second source of truth for one fact.
 * - `created_at` / `updated_at` — server bookkeeping; nothing in the UI reads
 *                       them off a tracking row.
 *
 * Anything NOT listed here must survive the mapper. That is the point of the
 * test: `maturity_level` was silently absent from the mapper while present on
 * both the wire type and the UI type, and because every field on
 * `EvidenceTracking` is optional the compiler had nothing to say about it.
 */
const WIRE_ONLY_KEYS = ['organization_id', 'evidence_id', 'created_at', 'updated_at']

const SCOPED_ROW = {
  id: 'sc-1',
  scf_id: 'control-fixture-one',
  selected: true,
  implementation_status: 'implemented',
  maturity_level: 'L3',
}

beforeEach(() => {
  vi.mocked(api.getCurrentOrganization).mockResolvedValue(ORG as never)
  vi.mocked(api.getScopedControls).mockResolvedValue([SCOPED_ROW] as never)
  vi.mocked(api.getEvidenceTracking).mockResolvedValue([API_ROW] as never)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('loadScopedControls — evidence tracking mapper', () => {
  it('carries maturity_level through from the API', async () => {
    const data = await loadScopedControls()
    expect(data?.evidence_tracking[EVIDENCE_KEY].maturity_level).toBe('L2')
  })

  it('leaves maturity_level undefined when the API sends null', async () => {
    vi.mocked(api.getEvidenceTracking).mockResolvedValue([
      { ...API_ROW, maturity_level: null },
    ] as never)
    const data = await loadScopedControls()
    expect(data?.evidence_tracking[EVIDENCE_KEY].maturity_level).toBeUndefined()
  })

  // The regression guard for the defect CLASS, not just this instance: a
  // hand-written object literal silently dropping a field the API supplies.
  it('drops no API field except the wire-only bookkeeping keys', async () => {
    const data = await loadScopedControls()
    const mapped = data?.evidence_tracking[EVIDENCE_KEY] as Record<string, unknown>

    const missing = Object.keys(API_ROW).filter(
      (key) => !WIRE_ONLY_KEYS.includes(key) && mapped?.[key] === undefined,
    )

    expect(missing).toEqual([])
  })
})

describe('exportScopedControls — normalizeEvidenceTracking', () => {
  it('includes maturity_level in the exported JSON', async () => {
    const data = (await loadScopedControls()) as ScopedControlsFile

    // jsdom Blobs expose their contents only through an async reader, and
    // exportScopedControls is synchronous — so record the constructor args.
    const captured: string[] = []
    const RealBlob = globalThis.Blob
    class RecordingBlob extends RealBlob {
      constructor(parts: BlobPart[], options?: BlobPropertyBag) {
        super(parts, options)
        captured.push(...parts.map(String))
      }
    }

    vi.stubGlobal('Blob', RecordingBlob)
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: () => 'blob:stub',
      revokeObjectURL: () => {},
    })

    try {
      exportScopedControls(data)
    } finally {
      vi.unstubAllGlobals()
    }

    const parsed = JSON.parse(captured.join(''))
    expect(parsed.evidence_tracking[EVIDENCE_KEY].maturity_level).toBe('L2')
  })
})
