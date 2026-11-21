// SPDX-License-Identifier: AGPL-3.0-only
/**
 * useEvidenceFileTypes — the server's list of evidence file types (#1309).
 *
 * One request per page load, shared by every upload control and viewer on the
 * screen. The list changes only when the backend is deployed, so once it has
 * arrived it is kept for the life of the page.
 *
 * Deliberately not react-query: the upload control and the viewer are mounted
 * from several places, and most of their tests, without a QueryClientProvider.
 * A module-level promise gives the same "fetch once, share the answer" with no
 * provider to forget.
 *
 * Fails quiet. A failed request yields `fileTypes: null`, and both consumers
 * treat null as "no opinion": the upload control lets the server decide and
 * the viewer falls back to what it can tell from the content type. A failed
 * request is forgotten, so the next mount asks again.
 */
import { useEffect, useState } from 'react'

import { getEvidenceFileTypes, type EvidenceFileTypesResponse } from '../data/apiClient'

let pending: Promise<EvidenceFileTypesResponse> | null = null
let loaded: EvidenceFileTypesResponse | null = null

export function loadEvidenceFileTypes(): Promise<EvidenceFileTypesResponse> {
  if (!pending) {
    // Wrapped so a synchronous throw becomes a rejection like any other failure.
    const request = Promise.resolve().then(() => getEvidenceFileTypes())
    pending = request
    request.then(
      data => { loaded = data },
      () => { if (pending === request) pending = null },
    )
  }
  return pending
}

/** Tests only: forget the shared answer so each test sees its own list. */
export function resetEvidenceFileTypesCache(): void {
  pending = null
  loaded = null
}

export interface EvidenceFileTypesState {
  /** The list, or null while loading or after a failure. */
  fileTypes: EvidenceFileTypesResponse | null
  /** True only while the request is in flight. */
  loading: boolean
}

export function useEvidenceFileTypes(): EvidenceFileTypesState {
  const [fileTypes, setFileTypes] = useState<EvidenceFileTypesResponse | null>(loaded)
  const [loading, setLoading] = useState(loaded === null)

  useEffect(() => {
    if (loaded) {
      setFileTypes(loaded)
      setLoading(false)
      return
    }
    let cancelled = false
    loadEvidenceFileTypes().then(
      data => {
        if (cancelled) return
        setFileTypes(data)
        setLoading(false)
      },
      () => {
        if (cancelled) return
        setFileTypes(null)
        setLoading(false)
      },
    )
    return () => { cancelled = true }
  }, [])

  return { fileTypes, loading }
}
