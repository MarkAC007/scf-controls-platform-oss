import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ValidationFormModal from '../ValidationFormModal'

const api = vi.hoisted(() => ({ recordControlValidation: vi.fn() }))

vi.mock('../../../data/apiClient', () => api)
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

/** Sets the browser's zone and clock. Node re-reads TZ when it is assigned; the check proves Date follows it. */
function clockAt(zone: string, utc: string) {
  vi.stubEnv('TZ', zone)
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(utc))
  const hour = new Intl.DateTimeFormat('en-US', { timeZone: zone, hour: 'numeric', hourCycle: 'h23' }).format(new Date())
  expect(new Date().getHours()).toBe(Number(hour))
}

function restoreClock() {
  vi.useRealTimers()
  vi.unstubAllEnvs()
}

function renderModal(props: Partial<Parameters<typeof ValidationFormModal>[0]> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const onClose = vi.fn()
  render(
    <QueryClientProvider client={client}>
      <ValidationFormModal organizationId="org-one" scfId="TESTCONTROL-01" onClose={onClose} {...props} />
    </QueryClientProvider>,
  )
  return { invalidate, onClose }
}

const note = () => screen.getByLabelText('What was checked and what was found')

describe('ValidationFormModal (PIG R10)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.recordControlValidation.mockResolvedValue({})
  })
  afterEach(restoreClock)

  it('records the note against this control, then refreshes the schedule and the journey', async () => {
    const { invalidate, onClose } = renderModal()
    expect(screen.getByRole('dialog', { name: 'Record validation' })).toHaveTextContent('TESTCONTROL-01')
    fireEvent.change(note(), { target: { value: '  Sampled ten joiners; all matched.  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Record' }))

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
    expect(api.recordControlValidation).toHaveBeenCalledWith('org-one', 'TESTCONTROL-01', 'Sampled ten joiners; all matched.')
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['control-validations', 'org-one'] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['journey', 'org-one'] })
  })

  it('needs a note that is more than blanks', () => {
    renderModal()
    const record = screen.getByRole('button', { name: 'Record' })
    expect(record).toBeDisabled()
    fireEvent.change(note(), { target: { value: '   ' } })
    expect(record).toBeDisabled()
    fireEvent.submit(record.closest('form')!)
    expect(api.recordControlValidation).not.toHaveBeenCalled()
  })

  it('says that recording replaces the last validation shown', () => {
    renderModal({ lastValidatedAt: '2026-09-20T10:15:00+00:00' })
    expect(screen.getByText(/replaces the last validation \(Sep 20, 2026\)/)).toBeInTheDocument()
  })

  it('names the last validation by the day the server dated it, as the panel does', () => {
    clockAt('Europe/London', '2026-10-14T12:00:00Z')
    renderModal({ lastValidatedAt: '2026-10-13T23:30:00+00:00' })
    expect(screen.getByText(/replaces the last validation \(Oct 13, 2026\)/)).toBeInTheDocument()
  })

  it("shows the server's refusal and keeps the form", async () => {
    api.recordControlValidation.mockRejectedValue(new Error('Requires editor role or higher'))
    const { onClose } = renderModal()
    fireEvent.change(note(), { target: { value: 'Checked' } })
    fireEvent.click(screen.getByRole('button', { name: 'Record' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Requires editor role or higher')
    expect(onClose).not.toHaveBeenCalled()
    expect(note()).toHaveValue('Checked')
  })

  it('stays open while a save is in flight', async () => {
    let refuse: (err: Error) => void = () => {}
    api.recordControlValidation.mockReturnValue(new Promise((_, reject) => { refuse = reject }))
    const { onClose } = renderModal()
    fireEvent.change(note(), { target: { value: 'Checked' } })
    fireEvent.click(screen.getByRole('button', { name: 'Record' }))
    await screen.findByRole('button', { name: 'Saving…' })
    expect(note()).toHaveAttribute('readonly')

    fireEvent.keyDown(document, { key: 'Escape' })
    fireEvent.click(document.querySelector('.modal-overlay')!)
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    const close = screen.getByRole('button', { name: 'Close' })
    expect(close).toBeDisabled()
    fireEvent.click(close)
    expect(onClose).not.toHaveBeenCalled()

    refuse(new Error('API key role viewer insufficient'))
    expect(await screen.findByRole('alert')).toHaveTextContent('API key role viewer insufficient')
    expect(onClose).not.toHaveBeenCalled()
    expect(note()).not.toHaveAttribute('readonly')
  })

  it('ignores Escape and a backdrop click once something is typed', () => {
    const { onClose } = renderModal()
    fireEvent.change(note(), { target: { value: 'Half a note' } })
    fireEvent.keyDown(document, { key: 'Escape' })
    fireEvent.click(document.querySelector('.modal-overlay')!)
    expect(onClose).not.toHaveBeenCalled()
    expect(note()).toHaveValue('Half a note')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('closes on Escape or a backdrop click while untouched, not on a click inside', () => {
    const first = renderModal()
    fireEvent.click(screen.getByRole('dialog'))
    expect(first.onClose).not.toHaveBeenCalled()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(first.onClose).toHaveBeenCalledTimes(1)
  })

  it('closes on a backdrop click while untouched', () => {
    const { onClose } = renderModal()
    fireEvent.click(document.querySelector('.modal-overlay')!)
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
