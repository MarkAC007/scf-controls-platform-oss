/**
 * A custom risk's control link can carry the document that sets the standard
 * it is an exception against (PIG R7, backend #1138).
 *
 * The control id is an opaque placeholder: the component only passes it through.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ConfirmProvider, answerConfirm } from '../../__tests__/confirmDialog'
import CustomRiskLinkDocument, { LinkDocumentSelect } from '../CustomRiskLinkDocument'
import { setCustomRiskControlDocument } from '../../data/apiClient'
import { listDocuments, type DocumentSummary } from '../../data/documentsApi'

vi.mock('../../data/apiClient', () => ({
  setCustomRiskControlDocument: vi.fn(),
}))
vi.mock('../../data/documentsApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../data/documentsApi')>()),
  listDocuments: vi.fn(),
}))
const editorState = vi.hoisted(() => ({ isEditor: true }))
vi.mock('../../hooks/useHasOrgRole', () => ({
  useIsOrgEditor: () => editorState.isEditor,
}))

const mockSet = vi.mocked(setCustomRiskControlDocument)
const mockList = vi.mocked(listDocuments)

const CONTROL = 'TESTCONTROL'
const RISK = 'R-ORG-1'

const doc = (id: string, title: string, document_type: string, lifecycle_status: string) =>
  ({ id, title, document_type, lifecycle_status }) as unknown as DocumentSummary

const documents = [
  doc('doc-1', 'Access Control Standard', 'standard', 'approved'),
  doc('doc-2', 'Cryptography Standard', 'standard', 'draft'),
]

let client: QueryClient

function renderLink(props: Partial<React.ComponentProps<typeof CustomRiskLinkDocument>> = {}) {
  const onChanged = vi.fn()
  render(
    <QueryClientProvider client={client}>
      <ConfirmProvider>
        <CustomRiskLinkDocument
          organizationId="org-1"
          riskCode={RISK}
          scfId={CONTROL}
          documentId="doc-1"
          onChanged={onChanged}
          {...props}
        />
      </ConfirmProvider>
    </QueryClientProvider>,
  )
  return { onChanged }
}

beforeEach(() => {
  vi.clearAllMocks()
  editorState.isEditor = true
  mockList.mockResolvedValue(documents)
  mockSet.mockResolvedValue(undefined)
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
})

describe('CustomRiskLinkDocument', () => {
  it("shows the link's document by title", async () => {
    renderLink()
    expect(await screen.findByText('Access Control Standard')).toBeInTheDocument()
    expect(mockList).toHaveBeenCalledWith('org-1')
  })

  it('says when the link has no document', () => {
    renderLink({ documentId: null })
    expect(screen.getByText('No supporting document')).toBeInTheDocument()
  })

  it('changes the document with exactly the new id, once the choice differs', async () => {
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    const { onChanged } = renderLink()
    fireEvent.click(screen.getByRole('button', { name: `Change document for ${CONTROL}` }))
    const select = await screen.findByLabelText(`Supporting document for ${CONTROL}`)
    await screen.findByRole('option', { name: /Cryptography Standard/ })
    const save = screen.getByRole('button', { name: 'Save' })
    expect(save).toBeDisabled()
    fireEvent.change(select, { target: { value: 'doc-2' } })
    expect(save).toBeEnabled()
    fireEvent.click(save)
    await waitFor(() => expect(onChanged).toHaveBeenCalled())
    expect(mockSet).toHaveBeenCalledWith(RISK, CONTROL, 'doc-2', 'org-1')
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['custom-risk-controls', 'org-1', RISK] })
  })

  it('clears the document with an explicit null', async () => {
    renderLink()
    fireEvent.click(screen.getByRole('button', { name: `Change document for ${CONTROL}` }))
    fireEvent.change(await screen.findByLabelText(`Supporting document for ${CONTROL}`), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(mockSet).toHaveBeenCalledWith(RISK, CONTROL, null, 'org-1'))
  })

  it('shows the API detail in an alert when the change is refused', async () => {
    mockSet.mockRejectedValue(new Error('No such document in this organisation'))
    const { onChanged } = renderLink({ documentId: null })
    fireEvent.click(screen.getByRole('button', { name: `Add document for ${CONTROL}` }))
    await screen.findByRole('option', { name: /Access Control Standard/ })
    fireEvent.change(screen.getByLabelText(`Supporting document for ${CONTROL}`), { target: { value: 'doc-1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('No such document in this organisation')
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('asks before a change that withdraws an approval, and saves nothing on cancel', async () => {
    const message = "This withdraws the admin approval of R-ORG-1's acceptance. Continue?"
    renderLink({ confirmMessage: message })
    fireEvent.click(screen.getByRole('button', { name: `Change document for ${CONTROL}` }))
    await screen.findByRole('option', { name: /Cryptography Standard/ })
    fireEvent.change(screen.getByLabelText(`Supporting document for ${CONTROL}`), { target: { value: 'doc-2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await answerConfirm(false)).toBe(message)
    expect(mockSet).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await answerConfirm(true)).toBe(message)
    await waitFor(() => expect(mockSet).toHaveBeenCalledWith(RISK, CONTROL, 'doc-2', 'org-1'))
  })

  it('offers no change to a viewer', () => {
    editorState.isEditor = false
    renderLink()
    expect(screen.queryByRole('button', { name: new RegExp(`document for ${CONTROL}`) })).toBeNull()
  })
})

describe('LinkDocumentSelect', () => {
  it("lists the organisation's documents with their type and status, and reports the choice", async () => {
    const onChange = vi.fn()
    render(
      <QueryClientProvider client={client}>
        <LinkDocumentSelect organizationId="org-1" value="" onChange={onChange} />
      </QueryClientProvider>,
    )
    const select = screen.getByLabelText('Supporting document (optional)')
    expect(await screen.findByRole('option', { name: 'Access Control Standard (standard, Approved)' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'No supporting document' })).toBeInTheDocument()
    fireEvent.change(select, { target: { value: 'doc-2' } })
    expect(onChange).toHaveBeenCalledWith('doc-2')
  })

  it('shows the API detail when the documents cannot be loaded', async () => {
    mockList.mockRejectedValue(new Error('Forbidden'))
    render(
      <QueryClientProvider client={client}>
        <LinkDocumentSelect organizationId="org-1" value="" onChange={vi.fn()} />
      </QueryClientProvider>,
    )
    expect(await screen.findByRole('alert')).toHaveTextContent('Forbidden')
  })
})
