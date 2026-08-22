/**
 * Turning the stored merged markdown back into per-section bodies.
 *
 * The editor edits one section at a time, but the backend stores the document
 * as one markdown blob plus a row per section. Recovering "the text under this
 * heading" is therefore a client-side slice, and getting the slice wrong is not
 * a cosmetic bug: it hands a section its neighbour's body and a save then
 * writes that body to the wrong section.
 *
 * The previous implementation paired the Nth stored section with the Nth
 * heading line. That holds only while the two sequences agree exactly, and they
 * routinely do not — a retired section is appended after the sections that
 * follow it in the outline, a human edit can introduce a `#` line of its own,
 * and the document H1 is a heading with no section row. One divergence and
 * every subsequent section is off by one.
 *
 * So the mapping here is by identity: each heading in the document is given the
 * same hierarchical id the backend derives (`parent.child`, see
 * `section_parser.parse_markdown_sections`), and a stored section claims the
 * heading whose id — or, failing that, whose heading text and level — matches
 * it, walking forwards so repeated headings resolve in document order.
 */
import type { DocumentSection } from '../../data/documentsApi'

/**
 * Merge marker comments, matched by leading keyword rather than exact text.
 *
 * Mirrors `three_layer._MARKER_RE` (`IGNORECASE | DOTALL`) deliberately: a
 * human can reflow the comment and the wording changes between releases, so
 * exact-string matching would leave stale markers in the textarea. `[\s\S]`
 * stands in for Python's DOTALL, which JavaScript spells `s` — kept as a class
 * so the pattern works without relying on the flag.
 */
const MARKER_RE = /<!--\s*(?:CONFLICT|NEW|PENDING\s+RETIREMENT)\b[\s\S]*?-->/gi

/**
 * Strip merge markers out of a body before it reaches the editor.
 *
 * The markers are machine annotations that live in `merged_content`, so they
 * loaded straight into the user's textarea — a comment addressed to the tool
 * sitting inside the text the user is asked to write. The status they carry is
 * already on the outline row and in the section header, so nothing is lost by
 * removing them here, and a subsequent save correctly writes back a body with
 * no marker in it.
 */
export function stripMergeMarkers(content: string): string {
  return (content || '').replace(MARKER_RE, '').trim()
}

/** Mirrors `section_parser._LEADING_NUMBER_RE`. */
const LEADING_NUMBER_RE = /^\d+(?:\.\d+)*\.?\s*/
/** Mirrors `section_parser._CONTROL_ID_RE`. */
const CONTROL_ID_RE = /\[([A-Z]{2,5}-\d+(?:\.\d+)?)\]/g
/**
 * Mirrors `section_parser._TRAILING_COUNT_RE`.
 *
 * A trailing parenthetical whose content starts with a digit is a tally of
 * today's scope — "(12 controls)" — not part of the section's identity, and the
 * backend drops it from the slug so that scoping one more control does not
 * rename every domain section of a Statement of Applicability. The rule is
 * narrow on both sides of the wire: "(Policy)" and "(Annex A)" are identity and
 * stay.
 */
const TRAILING_COUNT_RE = /\(\s*\d+[^)]*\)\s*$/

/**
 * The id component the backend derives from a heading.
 *
 * A faithful port of `section_parser.normalise_section_id`. It has to stay
 * faithful: this is what lets a heading in the markdown be recognised as the
 * heading a stored section row belongs to.
 */
export function normaliseSectionIdComponent(headingText: string): string {
  return (headingText || '')
    .replace(LEADING_NUMBER_RE, '')
    .replace(/\*\*/g, '')
    .replace(/:+$/, '')
    .replace(CONTROL_ID_RE, '')
    .replace(TRAILING_COUNT_RE, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-+|-+$/g, '')
}

interface ParsedHeading {
  /** The hierarchical id the backend would derive for this heading. */
  sectionId: string
  text: string
  level: number
  /** Index of the heading line itself. */
  line: number
  /** Index of the first line after this section's body. */
  end: number
}

/**
 * Every heading in the document, with the id the backend would give it.
 *
 * Fenced code is skipped for the same reason the backend skips it: a shell
 * comment inside a fence is not a heading, and treating it as one fabricates a
 * section that disappears the moment the fence content changes.
 */
function parseHeadings(markdown: string): ParsedHeading[] {
  const lines = markdown.split('\n')
  const headings: ParsedHeading[] = []
  const stack: ParsedHeading[] = []
  let inFence = false

  lines.forEach((line, index) => {
    if (/^(`{3,}|~{3,})/.test(line.trimStart())) {
      inFence = !inFence
      return
    }
    if (inFence) return
    const match = /^(#{1,6})\s+(.+)$/.exec(line)
    if (!match) return

    const level = match[1].length
    const text = match[2].trim()
    while (stack.length && stack[stack.length - 1].level >= level) stack.pop()
    const parent = stack.length ? stack[stack.length - 1].sectionId : ''
    const normalised = normaliseSectionIdComponent(text)
    const sectionId = parent ? `${parent}.${normalised}` : normalised

    const heading: ParsedHeading = {
      sectionId,
      text,
      level,
      line: index,
      end: lines.length,
    }
    if (headings.length) headings[headings.length - 1].end = index
    headings.push(heading)
    stack.push(heading)
  })

  return headings
}

/** What `sliceSections` could not account for, so the caller can say so. */
export interface SectionSlices {
  /** Section body by `section_id`. A section with no matched heading is absent. */
  bodies: Record<string, string>
  /** Section ids that no heading in the document could be matched to. */
  unmatched: string[]
}

/**
 * Section bodies keyed by `section_id`, sliced out of the merged markdown.
 *
 * Matching runs in three passes, each stricter than the fallback below it, and
 * a heading is consumed the moment it is claimed so two sections can never end
 * up sharing one body:
 *
 *   1. exact `section_id`, which is what the backend derived in the first place;
 *   2. heading text and level, for a section whose parent heading was renamed
 *      (its stored id still carries the old parent path);
 *   3. heading text alone, for a section whose level was changed by an editor.
 *
 * Anything still unmatched is reported rather than silently given a body. A
 * section that genuinely has no heading in the document — a retired ghost that
 * has already been removed from the text — belongs in `unmatched`, not in
 * `bodies` with somebody else's paragraphs in it.
 */
export function sliceSections(
  markdown: string,
  sections: DocumentSection[]
): SectionSlices {
  const lines = (markdown || '').split('\n')
  const headings = parseHeadings(markdown || '')
  const taken = new Set<number>()
  const bodies: Record<string, string> = {}
  const unmatched: string[] = []

  const body = (h: ParsedHeading) =>
    stripMergeMarkers(lines.slice(h.line + 1, h.end).join('\n'))

  const claim = (predicate: (h: ParsedHeading) => boolean): ParsedHeading | null => {
    for (let i = 0; i < headings.length; i++) {
      if (taken.has(i)) continue
      if (predicate(headings[i])) {
        taken.add(i)
        return headings[i]
      }
    }
    return null
  }

  const ordered = [...sections].sort((a, b) => a.ordinal - b.ordinal)
  const pending: DocumentSection[] = []

  // Pass 1 first for every section, so an exact id match is never stolen by a
  // looser text match made on behalf of an earlier section.
  for (const section of ordered) {
    const exact = claim((h) => h.sectionId === section.section_id)
    if (exact) bodies[section.section_id] = body(exact)
    else pending.push(section)
  }

  for (const section of pending) {
    const byText = claim(
      (h) => h.text === section.heading_text && h.level === section.heading_level
    )
    const loose = byText ?? claim((h) => h.text === section.heading_text)
    if (loose) bodies[section.section_id] = body(loose)
    else unmatched.push(section.section_id)
  }

  return { bodies, unmatched }
}
