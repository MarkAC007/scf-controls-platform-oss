// SPDX-License-Identifier: AGPL-3.0-only
/** Ids from outside are encoded before they go into an API path (review M1). */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const client = vi.hoisted(() => ({ apiFetch: vi.fn() }))
vi.mock('../apiClient', () => client)

import {
  confirmSecurityObjective,
  getMetricDetail,
  logMetricResult,
  recordObjectiveEvaluation,
  setObjectiveMetric,
  updateSecurityObjective,
} from '../securityObjectivesApi'

const HOSTILE = '../../admin?x=1#y'
const ENCODED = '..%2F..%2Fadmin%3Fx%3D1%23y'

describe('securityObjectivesApi paths', () => {
  beforeEach(() => {
    client.apiFetch.mockReset()
    client.apiFetch.mockResolvedValue({})
  })

  const path = () => client.apiFetch.mock.calls[0][0] as string

  it('encodes the objective id in the detail call', async () => {
    await getMetricDetail('org-1', HOSTILE)
    expect(path()).toBe(`/organizations/org-1/isms-metrics/${ENCODED}?breakdown_limit=10`)
  })

  it('encodes the objective id when logging a result', async () => {
    await logMetricResult('org-1', HOSTILE, {} as never)
    expect(path()).toBe(`/organizations/org-1/isms-metrics/${ENCODED}/results`)
  })

  it.each([
    ['updateSecurityObjective', () => updateSecurityObjective('org-1', HOSTILE, {} as never), ''],
    ['setObjectiveMetric', () => setObjectiveMetric('org-1', HOSTILE, {} as never), '/metric'],
    ['confirmSecurityObjective', () => confirmSecurityObjective('org-1', HOSTILE, {} as never), '/confirm'],
    ['recordObjectiveEvaluation', () => recordObjectiveEvaluation('org-1', HOSTILE, 'note'), '/evaluation'],
  ])('encodes the objective id in %s', async (_name, call, tail) => {
    await call()
    expect(path()).toBe(`/organizations/org-1/security-objectives/${ENCODED}${tail}`)
  })

  it('encodes the organisation id too', async () => {
    await getMetricDetail('org/../2', 'id')
    expect(path()).toBe('/organizations/org%2F..%2F2/isms-metrics/id?breakdown_limit=10')
  })

  it('leaves a UUID as it is', async () => {
    await getMetricDetail('org-1', '9b2f0c1e-4a7d-4e2b-8c3f-1d2e3f4a5b6c')
    expect(path()).toBe('/organizations/org-1/isms-metrics/9b2f0c1e-4a7d-4e2b-8c3f-1d2e3f4a5b6c?breakdown_limit=10')
  })
})
