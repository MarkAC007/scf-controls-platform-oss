/**
 * Markdown editor with a CodeMirror 6 upgrade path and a textarea floor.
 *
 * CodeMirror is loaded with a dynamic `import()` rather than a static one for
 * two reasons. It keeps roughly 300KB of editor out of the main bundle for the
 * majority of users who never open a document, and it means a failed chunk
 * load degrades to a plain textarea instead of a blank page. Losing syntax
 * highlighting is an inconvenience; losing the ability to edit an ISMS
 * document is an outage.
 *
 * The textarea is not a stub. It is a working editor with the same value,
 * onChange, and save semantics, so both paths are real.
 */
import { useEffect, useRef, useState } from 'react'

interface MarkdownEditorProps {
  value: string
  onChange: (value: string) => void
  readOnly?: boolean
  placeholder?: string
  minHeight?: number
}

type EditorMode = 'loading' | 'codemirror' | 'textarea'

export default function MarkdownEditor({
  value,
  onChange,
  readOnly = false,
  placeholder = 'Write in Markdown…',
  minHeight = 320,
}: MarkdownEditorProps) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const viewRef = useRef<any>(null)
  const onChangeRef = useRef(onChange)
  const [mode, setMode] = useState<EditorMode>('loading')

  // Keep the latest handler reachable from the CodeMirror listener without
  // rebuilding the editor on every render — a rebuild would drop the cursor.
  useEffect(() => {
    onChangeRef.current = onChange
  }, [onChange])

  useEffect(() => {
    let cancelled = false
    let view: any = null

    async function boot() {
      try {
        const [{ EditorState }, viewMod, { markdown }, { defaultKeymap, history, historyKeymap }] =
          await Promise.all([
            import('@codemirror/state'),
            import('@codemirror/view'),
            import('@codemirror/lang-markdown'),
            import('@codemirror/commands'),
          ])
        if (cancelled || !hostRef.current) return

        const { EditorView, keymap, lineNumbers, highlightActiveLine } = viewMod

        const state = EditorState.create({
          doc: value,
          extensions: [
            lineNumbers(),
            highlightActiveLine(),
            history(),
            keymap.of([...defaultKeymap, ...historyKeymap]),
            markdown(),
            EditorView.lineWrapping,
            EditorState.readOnly.of(readOnly),
            EditorView.updateListener.of((update: any) => {
              if (update.docChanged) {
                onChangeRef.current(update.state.doc.toString())
              }
            }),
          ],
        })

        view = new EditorView({ state, parent: hostRef.current })
        viewRef.current = view
        setMode('codemirror')
      } catch (error) {
        // Chunk failed to load, or the dependency is absent in this build.
        // Fall through to the textarea rather than leaving a dead pane.
        if (!cancelled) {
          console.warn('CodeMirror unavailable, using plain editor', error)
          setMode('textarea')
        }
      }
    }

    boot()
    return () => {
      cancelled = true
      if (view) view.destroy()
      viewRef.current = null
    }
    // Mount once. Value changes from outside are handled by the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Reflect an externally-changed value (switching sections, discarding an
  // edit) without clobbering what the user is currently typing.
  useEffect(() => {
    const view = viewRef.current
    if (!view) return
    const current = view.state.doc.toString()
    if (current === value) return
    view.dispatch({
      changes: { from: 0, to: current.length, insert: value },
    })
  }, [value])

  if (mode === 'textarea') {
    return (
      <textarea
        className="doc-editor-textarea"
        value={value}
        placeholder={placeholder}
        readOnly={readOnly}
        spellCheck
        style={{ minHeight }}
        onChange={(e) => onChange(e.target.value)}
      />
    )
  }

  return (
    <div className="doc-editor-host-wrap">
      <div ref={hostRef} className="doc-editor-host" style={{ minHeight }} />
      {mode === 'loading' && (
        <div className="doc-editor-loading">Loading editor…</div>
      )}
    </div>
  )
}
