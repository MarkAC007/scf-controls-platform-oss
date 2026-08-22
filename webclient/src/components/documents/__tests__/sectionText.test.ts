/**
 * Section identity: the mapping between a stored section row and the text in
 * the document that belongs to it.
 *
 * This is worth pinning down because getting it wrong is not cosmetic. The
 * previous implementation paired the Nth row with the Nth heading line, and on
 * the real Statement of Applicability that handed 65 of 71 sections their
 * neighbour's body — so opening a section in the editor showed the wrong text
 * and saving it would have written that text to the wrong section. Every case
 * below is one of the ways the two sequences diverge in practice.
 */
import { describe, expect, it } from 'vitest'

import {
  normaliseSectionIdComponent,
  sliceSections,
  stripMergeMarkers,
} from '../sectionText'
import type { DocumentSection } from '../../../data/documentsApi'

function section(
  sectionId: string,
  headingText: string,
  headingLevel: number,
  ordinal: number
): DocumentSection {
  return {
    section_id: sectionId,
    heading_text: headingText,
    heading_level: headingLevel,
    ordinal,
    status: 'unchanged',
    human_edited: false,
    control_ids: [],
    edited_at: null,
  }
}

describe('sliceSections', () => {
  it('gives each section the body under its own heading', () => {
    const markdown = [
      '# Access Control Policy',
      '',
      '## 1. Purpose',
      '',
      'Why this policy exists.',
      '',
      '## 2. Scope',
      '',
      'What it covers.',
    ].join('\n')

    const { bodies, unmatched } = sliceSections(markdown, [
      section('purpose', '1. Purpose', 2, 0),
      section('scope', '2. Scope', 2, 1),
    ])

    expect(unmatched).toEqual([])
    expect(bodies.purpose).toBe('Why this policy exists.')
    expect(bodies.scope).toBe('What it covers.')
  })

  it('builds hierarchical ids so a subsection matches its stored parent.child id', () => {
    const markdown = [
      '## Policy Statements',
      '',
      'Intro line.',
      '',
      '### Access Management',
      '',
      'Access rules.',
    ].join('\n')

    const { bodies, unmatched } = sliceSections(markdown, [
      section('policy-statements', 'Policy Statements', 2, 0),
      section('policy-statements.access-management', 'Access Management', 3, 1),
    ])

    expect(unmatched).toEqual([])
    expect(bodies['policy-statements']).toBe('Intro line.')
    expect(bodies['policy-statements.access-management']).toBe('Access rules.')
  })

  it('is not fooled by a retired section appended after the sections that follow it', () => {
    // The row ordinals say intro, retired, outro. The document says intro,
    // outro, retired — a retirement moves the text to the end without moving
    // the row. Pairing by position gives `retired` the outro's body and vice
    // versa; both saves would then land on the wrong section.
    const markdown = [
      '## Intro',
      '',
      'Intro body.',
      '',
      '## Outro',
      '',
      'Outro body.',
      '',
      '## Retired',
      '',
      'Retired body.',
    ].join('\n')

    const { bodies, unmatched } = sliceSections(markdown, [
      section('intro', 'Intro', 2, 0),
      section('retired', 'Retired', 2, 1),
      section('outro', 'Outro', 2, 2),
    ])

    expect(unmatched).toEqual([])
    expect(bodies.intro).toBe('Intro body.')
    expect(bodies.retired).toBe('Retired body.')
    expect(bodies.outro).toBe('Outro body.')
  })

  it('is not shifted by a heading the section rows do not know about', () => {
    // A human edit that introduces its own `#` line. Positionally this pushes
    // every later section onto its neighbour's body.
    const markdown = [
      '## Intro',
      '',
      'Intro body.',
      '',
      '## Notes from review',
      '',
      'Added by hand, no row exists for this.',
      '',
      '## Outro',
      '',
      'Outro body.',
    ].join('\n')

    const { bodies, unmatched } = sliceSections(markdown, [
      section('intro', 'Intro', 2, 0),
      section('outro', 'Outro', 2, 1),
    ])

    expect(unmatched).toEqual([])
    expect(bodies.intro).toBe('Intro body.')
    expect(bodies.outro).toBe('Outro body.')
    expect(Object.keys(bodies).sort()).toEqual(['intro', 'outro'])
  })

  it('reports a section with no heading in the document instead of inventing a body', () => {
    // A blank box invites the user to "fix" it by typing, which would then
    // overwrite whatever the section really holds. Absence has to be visible.
    const markdown = ['## Intro', '', 'Intro body.'].join('\n')

    const { bodies, unmatched } = sliceSections(markdown, [
      section('intro', 'Intro', 2, 0),
      section('ghost', 'Removed Section', 2, 1),
    ])

    expect(unmatched).toEqual(['ghost'])
    expect(bodies).not.toHaveProperty('ghost')
    expect(bodies.intro).toBe('Intro body.')
  })

  it('never lets two sections claim the same heading', () => {
    const markdown = [
      '## Evidence',
      '',
      'First evidence body.',
      '',
      '## Evidence',
      '',
      'Second evidence body.',
    ].join('\n')

    const { bodies, unmatched } = sliceSections(markdown, [
      section('evidence', 'Evidence', 2, 0),
      section('evidence-2', 'Evidence', 2, 1),
    ])

    expect(unmatched).toEqual([])
    expect(bodies.evidence).toBe('First evidence body.')
    // The second row's stored id does not match any derived id, so it falls to
    // the text pass — and must get the heading the first row did not take.
    expect(bodies['evidence-2']).toBe('Second evidence body.')
  })

  it('does not treat a comment inside a fenced code block as a heading', () => {
    const markdown = [
      '## Intro',
      '',
      '```bash',
      '# not a heading',
      'echo hello',
      '```',
      '',
      '## Outro',
      '',
      'Outro body.',
    ].join('\n')

    const { bodies, unmatched } = sliceSections(markdown, [
      section('intro', 'Intro', 2, 0),
      section('outro', 'Outro', 2, 1),
    ])

    expect(unmatched).toEqual([])
    expect(bodies.intro).toContain('# not a heading')
    expect(bodies.outro).toBe('Outro body.')
  })

  it('strips merge markers out of the bodies it returns', () => {
    const markdown = [
      '## Intro',
      '',
      '<!-- CONFLICT: both changed this -->',
      'Intro body.',
    ].join('\n')

    const { bodies } = sliceSections(markdown, [section('intro', 'Intro', 2, 0)])

    expect(bodies.intro).toBe('Intro body.')
  })
})

describe('stripMergeMarkers', () => {
  it('removes each of the three marker keywords', () => {
    expect(stripMergeMarkers('<!-- CONFLICT: both changed this -->\nBody.')).toBe('Body.')
    expect(stripMergeMarkers('<!-- NEW: just generated -->\nBody.')).toBe('Body.')
    expect(
      stripMergeMarkers('<!-- PENDING RETIREMENT: controls left scope -->\nBody.')
    ).toBe('Body.')
  })

  it('removes a marker a human has reflowed across lines', () => {
    // Matched by leading keyword, not exact text: the wording changes between
    // releases and an editor will rewrap it. Mirrors `_MARKER_RE`'s DOTALL.
    const content = [
      '<!-- CONFLICT:',
      '     both you and the generator changed this section,',
      '     so it needs a decision -->',
      'Body.',
    ].join('\n')

    expect(stripMergeMarkers(content)).toBe('Body.')
  })

  it('is case-insensitive, matching the backend flag', () => {
    expect(stripMergeMarkers('<!-- conflict: lowercase -->\nBody.')).toBe('Body.')
  })

  it('leaves a body with no marker exactly as it was', () => {
    const content = 'The organisation shall review access rights quarterly.'
    expect(stripMergeMarkers(content)).toBe(content)
  })

  it('leaves an unrelated HTML comment alone', () => {
    const content = '<!-- editor note: check with legal -->\nBody.'
    expect(stripMergeMarkers(content)).toBe(content)
  })
})

describe('normaliseSectionIdComponent', () => {
  // The three examples from the Python docstring, pinning the port to its
  // original. If `section_parser.normalise_section_id` changes, these should
  // fail rather than the mapping quietly drifting.
  it('matches the backend examples', () => {
    expect(normaliseSectionIdComponent('1. Document Control')).toBe('document-control')
    expect(normaliseSectionIdComponent('4.1 Access Management')).toBe('access-management')
    expect(normaliseSectionIdComponent('**Evidence Produced:**')).toBe('evidence-produced')
  })

  it('drops a trailing count parenthetical, which is scope and not identity', () => {
    expect(normaliseSectionIdComponent('3. GOV — Governance (12 controls)')).toBe(
      'gov-governance'
    )
    expect(normaliseSectionIdComponent('Controls (7)')).toBe('controls')
    expect(normaliseSectionIdComponent('Evidence (3 items)')).toBe('evidence')
  })

  it('keeps a qualifier parenthetical, which is part of what the section is', () => {
    expect(normaliseSectionIdComponent('Acceptable Use (Policy)')).toBe(
      'acceptable-use-policy'
    )
    expect(normaliseSectionIdComponent('Controls (Annex A)')).toBe('controls-annex-a')
  })

  it('strips bracketed SCF ids', () => {
    expect(normaliseSectionIdComponent('Asset Inventory [AST-02]')).toBe('asset-inventory')
  })
})
