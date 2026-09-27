/**
 * Add Custom Risk keeps keyboard focus inside the dialog: Title on open,
 * Tab/Shift+Tab wrap within it, and the opener gets focus back on close.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { useState } from 'react'
import CustomRiskModal, { type CustomRiskForm } from '../CustomRiskModal'

function Harness() {
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState<CustomRiskForm>({
    title: '', description: '', category_name: 'Custom', category_color: '#6b7280',
  })
  return (
    <>
      <button onClick={() => setOpen(true)}>Open</button>
      <button>Behind</button>
      {open && (
        <CustomRiskModal
          form={form}
          setForm={setForm}
          creating={false}
          onCreate={vi.fn()}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  )
}

function openDialog() {
  render(<Harness />)
  const opener = screen.getByRole('button', { name: 'Open' })
  opener.focus()
  fireEvent.click(opener)
  return opener
}

describe('CustomRiskModal focus', () => {
  it('names the dialog and focuses the labelled Title field on open', () => {
    openDialog()
    expect(screen.getByRole('dialog', { name: 'Add Custom Risk' })).toBeInTheDocument()
    expect(document.activeElement).toBe(screen.getByLabelText('Title *'))
    expect(screen.getByLabelText('Description *').tagName).toBe('TEXTAREA')
  })

  it('wraps Tab from the last control to the first, and Shift+Tab back', () => {
    openDialog()
    // Create is disabled while the form is empty, so Cancel is last.
    const close = screen.getByRole('button', { name: 'Close' })
    const cancel = screen.getByRole('button', { name: 'Cancel' })

    cancel.focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(document.activeElement).toBe(close)

    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(cancel)
  })

  it('pulls focus back in if it has escaped behind the overlay', () => {
    openDialog()
    screen.getByRole('button', { name: 'Behind' }).focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(screen.getByRole('dialog').contains(document.activeElement)).toBe(true)
  })

  it('returns focus to the opener on close', () => {
    const opener = openDialog()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.activeElement).toBe(opener)
  })
})
