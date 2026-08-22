/**
 * The count in an outline header.
 *
 * It exists as a shared component rather than as two copies of the same JSX
 * because the two surfaces have to agree, and the way they stopped agreeing is
 * instructive: the masthead reported the backend's `section_count` while the
 * rail counted the rows it was rendering. Once `section_count` began excluding
 * retired sections, one document said "38 sections" at the top and "71" in the
 * rail — the same lie finding 7 raised against the library card, restated one
 * level down.
 *
 * So the operative count is *the* count, taken from the same field the masthead
 * uses. Retiring sections stay listed below — they must remain reachable, which
 * is the entire point of `pending_retirement` — but they are not part of how
 * long the document is, so they are a separate and visibly secondary tally.
 *
 * The tail renders only when the backend actually reports
 * `pending_retirement_count`. That is deliberate rather than defensive padding:
 * the field and the operative-only `section_count` land in the same backend
 * change, so treating the field's absence as "0" would, against an older
 * backend, print "71 +33 retiring" and claim 104 sections.
 */
interface Props {
  /** Operative sections — the backend's `section_count`. */
  sectionCount: number
  /** The backend's `pending_retirement_count`. Undefined means "not reported". */
  retiringCount: number | undefined
}

export default function OutlineCount({ sectionCount, retiringCount }: Props) {
  const showRetiring = retiringCount !== undefined && retiringCount > 0

  return (
    <span
      title={
        showRetiring
          ? `${sectionCount} section${sectionCount === 1 ? '' : 's'}. ` +
            `${retiringCount} pending retirement, still listed below.`
          : `${sectionCount} section${sectionCount === 1 ? '' : 's'}.`
      }
    >
      {sectionCount}
      {showRetiring && (
        <span className="doc-outline-retiring">+{retiringCount} retiring</span>
      )}
    </span>
  )
}
