/**
 * The one place a section decision is sent, so the reader and the editor cannot
 * drift apart on what a decision invalidates.
 *
 * Every cache the decision touches has to go, and it is easy to miss one: the
 * document detail carries the section rows and the counts, the preview carries
 * the rendered HTML with the status wrappers the reader draws its affordances
 * on, the library list carries the conflict badge, history gains a version, and
 * a diff already on screen is now comparing against text that has moved. Miss
 * the preview and the conflict banner disappears while the section in the
 * document below still says "needs your decision".
 */
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'react-hot-toast'
import {
  resolveSection,
  type SectionResolveChoice,
  type SectionResolveResult,
} from '../../data/documentsApi'

const DONE: Record<SectionResolveChoice, string> = {
  keep_mine: 'Kept your text — this section no longer needs a decision',
  take_generated: 'Replaced with the generated text',
  retire: 'Section retired — the text stays in version history',
  keep: 'Kept in the document',
}

export function useResolveSection(organizationId: string, documentId: string) {
  const queryClient = useQueryClient()

  return useMutation<
    SectionResolveResult,
    Error,
    { sectionId: string; choice: SectionResolveChoice }
  >({
    mutationFn: ({ sectionId, choice }) =>
      resolveSection(organizationId, documentId, sectionId, choice),
    onSuccess: (_result, variables) => {
      queryClient.invalidateQueries({ queryKey: ['document', organizationId, documentId] })
      queryClient.invalidateQueries({
        queryKey: ['document-preview', organizationId, documentId],
      })
      queryClient.invalidateQueries({ queryKey: ['documents', organizationId] })
      queryClient.invalidateQueries({
        queryKey: ['document-history', organizationId, documentId],
      })
      queryClient.invalidateQueries({
        queryKey: ['section-generated', organizationId, documentId],
      })
      toast.success(DONE[variables.choice])
    },
    onError: (error) => toast.error(error.message),
  })
}
