/**
 * SystemsManagement — the open detail page reloads after a successful edit.
 *
 * Without this the detail page kept the values it fetched on open, so the
 * boundary & recovery fields (and every other field) looked unsaved until the
 * user navigated away and back.
 */
import { useEffect } from 'react'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import SystemsManagement from '../SystemsManagement'

const detailMounts = vi.fn()

vi.mock('../SystemsRegistry', () => ({
  default: () => <div data-testid="systems-registry" />,
}))

vi.mock('../SystemDetailPage', () => ({
  default: ({ systemId, onEdit }: { systemId: string; onEdit: (s: { id: string }) => void }) => {
    useEffect(() => { detailMounts(systemId) }, [systemId])
    return <button onClick={() => onEdit({ id: systemId })}>Edit</button>
  },
}))

vi.mock('../AddSystemModal', () => ({
  default: ({ onSuccess, onClose }: { onSuccess: () => void; onClose: () => void }) => (
    <div>
      <button onClick={onSuccess}>Save Changes</button>
      <button onClick={onClose}>Cancel</button>
    </div>
  ),
}))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('SystemsManagement — detail refresh after edit', () => {
  it('remounts the detail page (so it refetches) after a successful save', async () => {
    await act(async () => {
      render(<SystemsManagement organizationId="org-1" systemItem="sys-001" onSystemItemChange={vi.fn()} />)
    })
    expect(detailMounts).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }))
    })

    expect(detailMounts).toHaveBeenCalledTimes(2)
    expect(detailMounts).toHaveBeenLastCalledWith('sys-001')
  })

  it('does not reload the detail page when the edit is cancelled', async () => {
    await act(async () => {
      render(<SystemsManagement organizationId="org-1" systemItem="sys-001" onSystemItemChange={vi.fn()} />)
    })
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    })

    expect(detailMounts).toHaveBeenCalledTimes(1)
  })
})
