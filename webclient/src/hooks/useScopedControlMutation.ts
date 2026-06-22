import { useMutation, useQueryClient } from '@tanstack/react-query'
import { createOrUpdateScopedControl } from '../data/apiClient'
import type { ScopedControl } from '../types'

interface ScopedControlInput {
  scf_id: string
  selected?: boolean
  selection_reason?: string
  implementation_status?: string
  priority?: string
  owner?: string
  assigned_to?: string
  maturity_level?: string
  target_date?: string
  completion_date?: string
  implementation_notes?: string
  related_documentation?: any
  custom_fields?: any
  control_weighting?: number
  validation_cadence?: string
  nist_csf_function?: string
  control_question?: string
}

/**
 * Mutation hook for creating/updating a single scoped control.
 * Automatically invalidates React Query caches on success.
 */
export function useScopedControlMutation(orgId?: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (control: ScopedControlInput) =>
      createOrUpdateScopedControl(control, orgId),
    onSuccess: () => {
      // Invalidate both the paginated controls list and stats
      queryClient.invalidateQueries({ queryKey: ['scoped-controls'] })
      queryClient.invalidateQueries({ queryKey: ['scoped-controls-stats'] })
    },
  })
}
