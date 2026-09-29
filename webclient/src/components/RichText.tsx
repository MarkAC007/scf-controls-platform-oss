/**
 * RichText — renders a person-written description (task, system, …) with
 * clickable links, code and simple Markdown. See data/richText.ts for the
 * exact subset and the sanitisation order.
 *
 * Use this wherever a description used to be dropped into a <p> or <span>
 * verbatim. `as` picks the wrapper element so it can sit inline (a table
 * cell) or as a block (a detail page). Empty text renders nothing.
 */
import { useMemo } from 'react'
import { renderRichText } from '../data/richText'

interface RichTextProps {
  text: string | null | undefined
  className?: string
  as?: 'div' | 'span' | 'p'
}

export default function RichText({ text, className, as: Tag = 'div' }: RichTextProps) {
  const html = useMemo(() => renderRichText(text), [text])
  if (!html) return null
  return (
    <Tag
      className={`rich-text${className ? ` ${className}` : ''}`}
      // Output of renderRichText: source is HTML-escaped before markup is
      // generated, hrefs are protocol-checked, and the result is run through
      // DOMPurify with an explicit tag/attribute allow-list.
      // nosemgrep: typescript.react.security.audit.react-dangerouslysetinnerhtml.react-dangerouslysetinnerhtml
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
