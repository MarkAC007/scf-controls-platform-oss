/**
 * MarkdownField — the shared description editor.
 *
 * What these tests defend:
 *   - it is a controlled textarea: typing calls onChange with the new value;
 *   - the toolbar wraps the selection (or inserts a placeholder) with the
 *     right markers, so the buttons produce exactly what RichText renders;
 *   - Preview shows the rendered text, with links clickable, and Write
 *     returns to the textarea with the value intact.
 */
import { useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import MarkdownField from '../MarkdownField'

function Harness({ initial = '' }: { initial?: string }) {
  const [value, setValue] = useState(initial)
  return <MarkdownField value={value} onChange={setValue} aria-label="Description" />
}

describe('MarkdownField', () => {
  it('renders a textarea with the value and forwards edits through onChange', async () => {
    const onChange = vi.fn()
    render(<MarkdownField value="hello" onChange={onChange} aria-label="Description" />)
    const box = screen.getByRole('textbox', { name: 'Description' })
    expect(box).toHaveValue('hello')
    await userEvent.type(box, '!')
    expect(onChange).toHaveBeenLastCalledWith('hello!')
  })

  it('wraps the current selection in bold markers', () => {
    const onChange = vi.fn()
    render(<MarkdownField value="make this bold" onChange={onChange} aria-label="Description" />)
    const box = screen.getByRole('textbox', { name: 'Description' }) as HTMLTextAreaElement
    box.setSelectionRange(5, 9)
    fireEvent.click(screen.getByRole('button', { name: 'Bold' }))
    expect(onChange).toHaveBeenCalledWith('make **this** bold')
  })

  it('inserts a link scaffold when nothing is selected', () => {
    const onChange = vi.fn()
    render(<MarkdownField value="" onChange={onChange} aria-label="Description" />)
    fireEvent.click(screen.getByRole('button', { name: 'Link' }))
    expect(onChange).toHaveBeenCalledWith('[link text](https://)')
  })

  it('starts a bullet on a new line when the caret is mid-text', () => {
    const onChange = vi.fn()
    render(<MarkdownField value="intro" onChange={onChange} aria-label="Description" />)
    const box = screen.getByRole('textbox', { name: 'Description' }) as HTMLTextAreaElement
    box.setSelectionRange(5, 5)
    fireEvent.click(screen.getByRole('button', { name: 'Bullet list' }))
    expect(onChange).toHaveBeenCalledWith('intro\n- item')
  })

  it('preview renders the text with a clickable link, and Write restores the textarea', async () => {
    render(<Harness initial="See https://example.com/doc and `code`" />)
    await userEvent.click(screen.getByRole('tab', { name: 'Preview' }))

    const preview = screen.getByTestId('markdown-field-preview')
    const link = preview.querySelector('a') as HTMLAnchorElement
    expect(link).not.toBeNull()
    expect(link.getAttribute('href')).toBe('https://example.com/doc')
    expect(link.getAttribute('target')).toBe('_blank')
    expect(preview.querySelector('code')?.textContent).toBe('code')
    expect(screen.queryByRole('textbox')).toBeNull()
    // Formatting buttons are meaningless while previewing.
    expect(screen.getByRole('button', { name: 'Bold' })).toBeDisabled()

    await userEvent.click(screen.getByRole('tab', { name: 'Write' }))
    expect(screen.getByRole('textbox', { name: 'Description' })).toHaveValue('See https://example.com/doc and `code`')
  })

  it('preview of an empty value says so rather than rendering nothing', async () => {
    render(<MarkdownField value="   " onChange={vi.fn()} aria-label="Description" />)
    await userEvent.click(screen.getByRole('tab', { name: 'Preview' }))
    expect(screen.getByText('Nothing to preview')).toBeInTheDocument()
  })

  it('respects disabled', () => {
    render(<MarkdownField value="x" onChange={vi.fn()} aria-label="Description" disabled />)
    expect(screen.getByRole('textbox', { name: 'Description' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Bold' })).toBeDisabled()
  })
})
