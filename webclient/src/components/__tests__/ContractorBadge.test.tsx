/**
 * ContractorBadge: the badge appears when it should, and — the harder half —
 * does not appear when it should not (#822 phase 2, ISC-42).
 *
 * The absence cases are what these tests exist for. ``member_type`` is
 * resolved per organisation by a lookup that starts empty and fills in, so
 * every badge in the app renders at least once with ``undefined``. If that
 * rendered a badge, every row in a members table would flash "Contractor" and
 * retract it, which is worse than never showing one: a label that appears by
 * accident is a false claim about a real person, and the reader has no way to
 * tell it from a true one.
 *
 * Asserted on accessible text and the accessible name, never on class names or
 * colour. The badge is amber, but a badge distinguished ONLY by colour would
 * be an accessibility defect, so a test that asserted the colour would be
 * locking in the defect rather than the behaviour.
 *
 * ``withContractorSuffix`` is covered here rather than in its own file because
 * it is the same statement in the one place an element cannot go: HTML allows
 * only text inside ``<option>``, so four pickers carry the suffix instead of
 * the badge. A suite that tested only the component would leave those four
 * surfaces with no coverage at all.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ContractorBadge, withContractorSuffix } from '../ContractorBadge'

describe('ContractorBadge', () => {
  it('renders a readable label for an external contractor', () => {
    render(<ContractorBadge memberType="external_contractor" />)
    expect(screen.getByText('Contractor')).toBeInTheDocument()
  })

  it('is reachable by its accessible name, not only by colour', () => {
    render(<ContractorBadge memberType="external_contractor" />)
    expect(screen.getByLabelText('External contractor')).toBeInTheDocument()
  })

  it('names the person it is about', () => {
    // A column of badges that all read "Contractor" gives a screen-reader user
    // no way to tell which row each belongs to.
    render(<ContractorBadge memberType="external_contractor" personName="Ada Lovelace" />)
    expect(
      screen.getByLabelText('Ada Lovelace is an external contractor')
    ).toBeInTheDocument()
  })

  it('renders nothing for an internal member', () => {
    const { container } = render(<ContractorBadge memberType="internal" />)
    expect(container).toBeEmptyDOMElement()
    expect(screen.queryByText('Contractor')).not.toBeInTheDocument()
  })

  it('renders nothing when the member type is undefined', () => {
    // The in-flight case. useOrgMemberTypes starts with an empty map, so this
    // is the FIRST render of every badge in the application.
    const { container } = render(<ContractorBadge />)
    expect(container).toBeEmptyDOMElement()
    expect(screen.queryByText('Contractor')).not.toBeInTheDocument()
  })

  it('renders nothing when the member type is null', () => {
    const { container } = render(<ContractorBadge memberType={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing for a value outside the vocabulary', () => {
    // A server that grew a third employment type must not make this badge
    // start claiming everyone is a contractor. Only the one value does.
    const { container } = render(
      <ContractorBadge memberType={'secondee' as never} />
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('does not announce the absence of a badge', () => {
    // "Internal" on every row would be noise that makes the one row that
    // matters harder to find. Absence is the ordinary case, said silently.
    render(<ContractorBadge memberType="internal" personName="Ada Lovelace" />)
    expect(screen.queryByText(/internal/i)).not.toBeInTheDocument()
  })

  it('says in its hover text that it grants nothing', () => {
    // ISC-21 at the surface a user actually reads. If somebody later wires
    // member_type into a permission check, this wording becomes a lie, and a
    // test on it is a cheap place for that to surface.
    render(<ContractorBadge memberType="external_contractor" />)
    const badge = screen.getByLabelText('External contractor')
    expect(badge).toHaveAttribute('title', expect.stringContaining('grants'))
    expect(badge.getAttribute('title')).toMatch(/organisation role/i)
  })
})

describe('withContractorSuffix', () => {
  it('appends the suffix for a contractor', () => {
    expect(withContractorSuffix('Ada Lovelace', 'external_contractor')).toBe(
      'Ada Lovelace (Contractor)'
    )
  })

  it('leaves an internal name untouched', () => {
    expect(withContractorSuffix('Ada Lovelace', 'internal')).toBe('Ada Lovelace')
  })

  it('leaves an unknown name untouched', () => {
    expect(withContractorSuffix('Ada Lovelace', undefined)).toBe('Ada Lovelace')
    expect(withContractorSuffix('Ada Lovelace', null)).toBe('Ada Lovelace')
  })

  it('uses the same word as the badge', () => {
    // Two surfaces, one claim. If the badge said "Contractor" and a picker
    // said "External", a reader would reasonably wonder whether they meant
    // different things.
    render(<ContractorBadge memberType="external_contractor" />)
    const badgeWord = screen.getByText('Contractor').textContent
    expect(withContractorSuffix('X', 'external_contractor')).toBe(`X (${badgeWord})`)
  })
})
