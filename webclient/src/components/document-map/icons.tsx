/**
 * Inline glyphs for the document map.
 *
 * The check and the ring are one of the four redundant channels separating a
 * confirmed placement from a suggested one, so they must stay visually
 * distinct in shape — solid stroke vs hollow dashed outline — and not merely
 * in colour.
 */

interface GlyphProps {
  size?: number
  className?: string
}

function frame(size: number, className: string | undefined, children: JSX.Element) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      {children}
    </svg>
  )
}

/** Solid check — confirmed by a person. */
export function CheckGlyph({ size = 13, className }: GlyphProps) {
  return frame(size, className, <path d="M20 6 9 17l-5-5" />)
}

/** Hollow dashed ring — suggested, nobody has accepted it yet. */
export function RingGlyph({ size = 13, className }: GlyphProps) {
  return frame(size, className, <circle cx="12" cy="12" r="8" strokeDasharray="3 3" />)
}

/** Gap — controls scoped here, nothing placed. */
export function AlertGlyph({ size = 13, className }: GlyphProps) {
  return frame(
    size,
    className,
    <>
      <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
      <path d="M12 9v4M12 17h.01" />
    </>
  )
}

/** Out of scope — a decision, not an absence. */
export function MinusGlyph({ size = 13, className }: GlyphProps) {
  return frame(size, className, <path d="M5 12h14" />)
}

export function DocGlyph({ size = 16, className }: GlyphProps) {
  return frame(
    size,
    className,
    <>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
    </>
  )
}

export function ClockGlyph({ size = 13, className }: GlyphProps) {
  return frame(
    size,
    className,
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  )
}

export function CloseGlyph({ size = 16, className }: GlyphProps) {
  return frame(size, className, <path d="M18 6 6 18M6 6l12 12" />)
}
