/**
 * MarkdownField — the editor half of RichText. A textarea with a small
 * toolbar (bold, italic, code, link, list) and a Write / Preview switch, so
 * the person typing a description sees exactly what readers will see.
 *
 * It deliberately stays a textarea: the value is plain Markdown-lite text,
 * the same string the API stores and RichText renders. There is no hidden
 * HTML model to get out of step with the backend, no contentEditable to
 * fight, and paste behaves like paste.
 *
 * Drop-in for any <textarea value onChange>: the props mirror the ones the
 * existing task and system modals already pass.
 */
import { useCallback, useId, useRef, useState } from 'react'
import RichText from './RichText'

interface MarkdownFieldProps {
  value: string
  onChange: (value: string) => void
  id?: string
  placeholder?: string
  rows?: number
  disabled?: boolean
  /** Forwarded to the textarea, so callers style the input as they would a plain one. */
  className?: string
  'aria-label'?: string
}

type Wrap = { before: string; after: string; placeholder: string }

const ACTIONS: Array<{ key: string; label: string; title: string; wrap: Wrap }> = [
  { key: 'bold', label: 'B', title: 'Bold', wrap: { before: '**', after: '**', placeholder: 'bold text' } },
  { key: 'italic', label: 'I', title: 'Italic', wrap: { before: '*', after: '*', placeholder: 'italic text' } },
  { key: 'code', label: '</>', title: 'Code', wrap: { before: '`', after: '`', placeholder: 'code' } },
  { key: 'link', label: '🔗', title: 'Link', wrap: { before: '[', after: '](https://)', placeholder: 'link text' } },
  { key: 'list', label: '•', title: 'Bullet list', wrap: { before: '- ', after: '', placeholder: 'item' } },
]

export default function MarkdownField({
  value,
  onChange,
  id,
  placeholder,
  rows = 4,
  disabled = false,
  className,
  'aria-label': ariaLabel,
}: MarkdownFieldProps) {
  const [mode, setMode] = useState<'write' | 'preview'>('write')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const generatedId = useId()
  const fieldId = id ?? `markdown-field-${generatedId}`

  const applyWrap = useCallback((wrap: Wrap) => {
    const el = textareaRef.current
    const start = el?.selectionStart ?? value.length
    const end = el?.selectionEnd ?? value.length
    const selected = value.slice(start, end) || wrap.placeholder
    // A list marker belongs at the start of a line.
    const before = wrap.before === '- ' && start > 0 && value[start - 1] !== '\n' ? '\n- ' : wrap.before
    const next = value.slice(0, start) + before + selected + wrap.after + value.slice(end)
    onChange(next)
    // Put the caret back around what was just inserted so a second click or
    // typing continues where the person was.
    requestAnimationFrame(() => {
      if (!el) return
      el.focus()
      const caretStart = start + before.length
      el.setSelectionRange(caretStart, caretStart + selected.length)
    })
  }, [value, onChange])

  return (
    <div className="markdown-field" data-mode={mode}>
      <div className="markdown-field-toolbar" role="toolbar" aria-label="Formatting">
        <div className="markdown-field-actions">
          {ACTIONS.map(action => (
            <button
              key={action.key}
              type="button"
              className="markdown-field-action"
              title={action.title}
              aria-label={action.title}
              disabled={disabled || mode === 'preview'}
              onMouseDown={e => e.preventDefault() /* keep the textarea selection */}
              onClick={() => applyWrap(action.wrap)}
            >
              {action.label}
            </button>
          ))}
        </div>
        <div className="markdown-field-modes" role="tablist" aria-label="Editor mode">
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'write'}
            className={`markdown-field-mode${mode === 'write' ? ' markdown-field-mode--active' : ''}`}
            onClick={() => setMode('write')}
          >
            Write
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'preview'}
            className={`markdown-field-mode${mode === 'preview' ? ' markdown-field-mode--active' : ''}`}
            onClick={() => setMode('preview')}
          >
            Preview
          </button>
        </div>
      </div>

      {mode === 'write' ? (
        <textarea
          ref={textareaRef}
          id={fieldId}
          className={`markdown-field-textarea${className ? ` ${className}` : ''}`}
          value={value}
          onChange={e => onChange(e.target.value)}
          placeholder={placeholder}
          rows={rows}
          disabled={disabled}
          aria-label={ariaLabel}
        />
      ) : (
        <div className="markdown-field-preview" data-testid="markdown-field-preview">
          {value.trim() ? (
            <RichText text={value} />
          ) : (
            <span className="markdown-field-preview-empty">Nothing to preview</span>
          )}
        </div>
      )}
      <p className="markdown-field-hint">
        Links are clickable. <code>**bold**</code>, <code>*italic*</code>, <code>`code`</code>, <code>- list</code>.
      </p>
    </div>
  )
}
