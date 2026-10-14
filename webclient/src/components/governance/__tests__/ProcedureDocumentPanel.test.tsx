import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ProcedureDocumentPanel from '../ProcedureDocumentPanel'

type Doc = { id: string; title: string; document_type: string; lifecycle_status: string }

const state = vi.hoisted(() => ({
  editor: false,
  row: null as Record<string, unknown> | null,
  error: null as Error | null,
  docs: [] as Doc[],
  docsError: null as Error | null,
}))
const api = vi.hoisted(() => ({ setProcedure: vi.fn() }))

vi.mock('../../../hooks/useHasOrgRole', () => ({ useIsOrgEditor: () => state.editor }))
vi.mock('../../../hooks/useScopedControlsQuery', () => ({
  useScopedControlsQuery: () => ({ isLoading: false, isError: !!state.error, error: state.error, data: { pages: [] } }),
  flattenScopedControlPages: () => ({ controls: state.row ? [state.row] : [] }),
}))
vi.mock('../../../hooks/useProcedureDocuments', () => ({
  useProcedureDocuments: () => ({
    isLoading: false,
    isError: !!state.docsError,
    error: state.docsError,
    data: state.docsError ? undefined : state.docs,
  }),
}))
vi.mock('../../../data/apiClient', () => ({ setControlProcedureDocument: api.setProcedure }))
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function renderPanel(onChange?: () => void) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  render(
    <QueryClientProvider client={client}>
      <ProcedureDocumentPanel organizationId="org-one" scfId="GOV-01" onChange={onChange} />
    </QueryClientProvider>,
  )
  return { invalidate }
}

const DOCS: Doc[] = [
  { id: 'doc-approved', title: 'GOV Procedure', document_type: 'procedure', lifecycle_status: 'approved' },
  { id: 'doc-published', title: 'IAC Procedure', document_type: 'procedure', lifecycle_status: 'published' },
  { id: 'doc-draft', title: 'AST Procedure', document_type: 'procedure', lifecycle_status: 'draft' },
  { id: 'doc-review', title: 'NET Procedure', document_type: 'procedure', lifecycle_status: 'in_review' },
]

describe('ProcedureDocumentPanel (PIG R6, step 2d)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    state.editor = false
    state.error = null
    state.docsError = null
    state.docs = DOCS
    state.row = { scf_id: 'GOV-01', is_scoped: true, procedure_document_id: null, procedure_document_status: null }
  })

  it('says when no procedure is linked, read-only to a viewer', () => {
    renderPanel()
    expect(screen.getByText(/No procedure document is linked/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /procedure/i })).not.toBeInTheDocument()
  })

  it('shows the linked procedure by title with its current status', () => {
    state.row = { scf_id: 'GOV-01', is_scoped: true, procedure_document_id: 'doc-approved', procedure_document_status: 'approved' }
    renderPanel()
    expect(screen.getByText('GOV Procedure')).toBeInTheDocument()
    expect(screen.getByText('Approved')).toBeInTheDocument()
    expect(screen.queryByText(/needs signing off again/)).not.toBeInTheDocument()
  })

  it('flags a linked procedure edited since it was signed off', () => {
    state.row = { scf_id: 'GOV-01', is_scoped: true, procedure_document_id: 'doc-review', procedure_document_status: 'in_review' }
    renderPanel()
    expect(screen.getByText('NET Procedure')).toBeInTheDocument()
    expect(screen.getByText('In Review')).toBeInTheDocument()
    expect(screen.getByText(/needs signing off again/)).toBeInTheDocument()
  })

  it('offers no link on a control without a scope record, which the API would refuse', () => {
    state.editor = true
    state.row = { scf_id: 'GOV-01', is_scoped: false, procedure_document_id: null, procedure_document_status: null }
    renderPanel()
    expect(screen.getByText(/no scope record yet/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Link procedure' })).not.toBeInTheDocument()
  })

  it('offers only signed-off procedures and sends the chosen id', async () => {
    state.editor = true
    api.setProcedure.mockResolvedValue({})
    const onChange = vi.fn()
    const { invalidate } = renderPanel(onChange)
    fireEvent.click(screen.getByRole('button', { name: 'Link procedure' }))
    const picker = screen.getByLabelText('Document')
    const offered = within(picker).getAllByRole('option').map((option) => option.textContent)
    expect(offered).toEqual(['No procedure linked', 'GOV Procedure (Approved)', 'IAC Procedure (Published)'])
    fireEvent.change(picker, { target: { value: 'doc-published' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.setProcedure).toHaveBeenCalledWith('org-one', 'GOV-01', 'doc-published'))
    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1))
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['scoped-controls', 'org-one'] })
  })

  it('unlinks with null', async () => {
    state.editor = true
    state.row = { scf_id: 'GOV-01', is_scoped: true, procedure_document_id: 'doc-approved', procedure_document_status: 'approved' }
    api.setProcedure.mockResolvedValue({})
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Change procedure' }))
    expect(screen.getByLabelText('Document')).toHaveValue('doc-approved')
    fireEvent.change(screen.getByLabelText('Document'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.setProcedure).toHaveBeenCalledWith('org-one', 'GOV-01', null))
  })

  it('says how to get a procedure when none is signed off', () => {
    state.editor = true
    state.docs = DOCS.filter((doc) => doc.lifecycle_status === 'draft')
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Link procedure' }))
    expect(screen.getByText(/No approved or published procedure documents yet/)).toBeInTheDocument()
  })

  it('shows the API refusal message and keeps the form open', async () => {
    state.editor = true
    api.setProcedure.mockRejectedValue(new Error('Only an approved or published procedure can be linked; this one is in_review'))
    const onChange = vi.fn()
    renderPanel(onChange)
    fireEvent.click(screen.getByRole('button', { name: 'Link procedure' }))
    fireEvent.change(screen.getByLabelText('Document'), { target: { value: 'doc-approved' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Only an approved or published procedure can be linked; this one is in_review')
    expect(screen.getByLabelText('Document')).toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
  })

  it('shows a failed documents read as an error in the form', () => {
    state.editor = true
    state.docsError = new Error('Could not list documents')
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Link procedure' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Could not list documents')
  })

  it('shows a failed lookup as an error, never as unlinked', () => {
    state.editor = true
    state.error = new Error('Internal server error')
    state.row = null
    renderPanel()
    expect(screen.getByRole('alert')).toHaveTextContent('Internal server error')
    expect(screen.queryByText(/No procedure document is linked/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Link procedure' })).not.toBeInTheDocument()
  })

  it("shows the document's own current status over the row's stale one", () => {
    state.row = { scf_id: 'GOV-01', is_scoped: true, procedure_document_id: 'doc-draft', procedure_document_status: 'approved' }
    renderPanel()
    expect(screen.getByText('AST Procedure')).toBeInTheDocument()
    expect(screen.getByText(/needs signing off again/)).toBeInTheDocument()
  })

  it('keeps a drifted link as the current, unselectable option and Save inert until it changes', () => {
    state.editor = true
    state.row = { scf_id: 'GOV-01', is_scoped: true, procedure_document_id: 'doc-draft', procedure_document_status: 'draft' }
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Change procedure' }))
    const select = screen.getByLabelText('Document') as HTMLSelectElement
    expect(select.value).toBe('doc-draft')
    const current = within(select).getByRole('option', { name: /AST Procedure .*current/ }) as HTMLOptionElement
    expect(current.disabled).toBe(true)
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    fireEvent.change(select, { target: { value: '' } })
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
  })
})
