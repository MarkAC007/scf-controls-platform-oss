/**
 * useCdmEnabled — is the Control Documents Mapper available to this org?
 *
 * The module is being retired (docs/plans/cdm-retirement.md). The backend has
 * gated its routes on a flag since slice 7; the UI never was, so a deployment
 * with CDM off still showed "Control Documents", "Document Map" and the
 * per-control "Knowledge Base" tab, every call from which 404s. The answer is
 * the backend's: `cdm_enabled` on the organisation settings response,
 * resolved there from the tenant override, else the deployment's `ENABLE_CDM`.
 *
 * Fails closed. Loading, an errored call, a backend too old to send the field
 * and a missing org id all read as `false` — a hidden entry is recoverable,
 * while showing a surface whose every call 404s is the failure this exists to
 * stop.
 *
 * Shares the `['organization-settings', orgId]` cache entry with
 * `useOrganizationSettings`, so the sidebar, the control detail page and the
 * scoping detail page asking at once cost one request between them. The
 * retry policy below applies to that shared entry too: one retry, so a
 * transient blip does not hide the module for the five-minute stale window,
 * while a real outage still fails closed within a second or two.
 */
import { useQuery } from '@tanstack/react-query'

import {
  fetchOrganizationSettings,
  type OrganizationSettingsResponse,
} from '../data/apiClient'

export interface CdmGate {
  /** Whether to offer CDM. False unless the backend has said otherwise. */
  enabled: boolean
  /**
   * Whether `enabled` is the backend's answer rather than the safe default.
   *
   * Only a caller that *navigates* on the answer needs this. Redirecting off
   * `?tab=cdm` while the settings call is still in flight would bounce every
   * deep link on an install where CDM is on, so App waits for this; a caller
   * that merely hides an entry can use the boolean and let it appear late.
   */
  resolved: boolean
}

export function useCdmGate(orgId: string | null | undefined): CdmGate {
  const { data, isSuccess, isError } = useQuery<OrganizationSettingsResponse>({
    queryKey: ['organization-settings', orgId],
    // `orgId` is non-null here: the query does not run without one.
    queryFn: () => fetchOrganizationSettings(orgId!),
    enabled: !!orgId,
    staleTime: 5 * 60 * 1000,
    refetchOnWindowFocus: false,
    retry: 1,
  })

  return {
    enabled: data?.cdm_enabled === true,
    // No org id yet is not an answer — it is the org context still loading.
    resolved: !!orgId && (isSuccess || isError),
  }
}

export function useCdmEnabled(orgId: string | null | undefined): boolean {
  return useCdmGate(orgId).enabled
}

export default useCdmEnabled
