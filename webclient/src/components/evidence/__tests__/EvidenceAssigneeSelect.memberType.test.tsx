/**
 * The contractor label on the one path a badge cannot take
 * (#822 phase 2, ISC-42).
 *
 * HTML allows only text inside an ``<option>``. Five pickers therefore carry
 * ``withContractorSuffix`` instead of ``<ContractorBadge>``, and a suffix is a
 * plainer thing than a component: nothing type-checks that a call site
 * remembered it, and a picker that quietly stopped appending it looks exactly
 * like a picker in an organisation with no contractors.
 *
 * ``ContractorBadge.test.tsx`` covers the function. This file covers the
 * SEAM — one real picker, rendered — because the risk here is not that the
 * function returns the wrong string, it is that a call site drifts: renders a
 * badge into an <option> where it disappears, drops the argument, or loses the
 * suffix behind another one. The invariant that forced the design in the first
 * place is asserted directly, so a future author who tries the obvious thing
 * finds out from a test rather than from an empty dropdown.
 *
 * ``EvidenceAssigneeSelect`` takes plain props and needs nothing mocked, which
 * is why it is the one picked to stand for the pattern.
 */
import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { EvidenceAssigneeSelect } from '../EvidenceAssigneeSelect'
import type { MemberType, UserSimple } from '../../../types'

const ADA: UserSimple = { id: 'u1', email: 'ada@example.com', display_name: 'Ada Lovelace' }
const GRACE: UserSimple = { id: 'u2', email: 'grace@example.com', display_name: 'Grace Hopper' }

const TYPES: Record<string, MemberType> = {
  u1: 'external_contractor',
  u2: 'internal',
}

/** The lookup the screen supplies; undefined for anyone not yet resolved. */
const typeOf = (userId: string | null | undefined) =>
  userId ? TYPES[userId] : undefined

function renderPicker(props: Partial<Parameters<typeof EvidenceAssigneeSelect>[0]> = {}) {
  return render(
    <EvidenceAssigneeSelect
      value={props.value ?? ''}
      members={props.members ?? [ADA, GRACE]}
      onChange={props.onChange ?? (() => {})}
      memberTypeOf={'memberTypeOf' in props ? props.memberTypeOf : typeOf}
      resolved={props.resolved}
      id="assignee"
    />
  )
}

const optionTexts = () =>
  screen.getAllByRole('option').map(option => option.textContent)

describe('the option labels', () => {
  it('suffixes a contractor', () => {
    renderPicker()
    expect(optionTexts()).toContain('Ada Lovelace (Contractor)')
  })

  it('leaves a permanent member\u2019s name alone', () => {
    renderPicker()
    expect(optionTexts()).toContain('Grace Hopper')
    expect(optionTexts()).not.toContain('Grace Hopper (Contractor)')
  })

  it('says nothing about somebody whose type has not arrived', () => {
    // The lookup starts empty on every screen, so this is the first render of
    // every picker in the application. Guessing here would label the whole
    // list wrong for as long as the fetch takes.
    renderPicker({ memberTypeOf: () => undefined })
    expect(optionTexts()).toEqual(['Unassigned', 'Ada Lovelace', 'Grace Hopper'])
  })

  it('renders exactly as before when the caller never wired the lookup', () => {
    // The prop is optional on purpose: a screen that has not adopted it must
    // not break, and must not half-adopt it either.
    renderPicker({ memberTypeOf: undefined })
    expect(optionTexts()).toEqual(['Unassigned', 'Ada Lovelace', 'Grace Hopper'])
  })
})

describe('why it is a suffix and not a badge', () => {
  it('puts no element inside any option', () => {
    // The invariant the whole `withContractorSuffix` design exists to satisfy.
    // A <ContractorBadge> rendered here would produce a <span> that browsers
    // drop, so the label would simply go missing — visibly fine in jsdom,
    // invisible in Chrome. Assert the shape, not the appearance.
    renderPicker({ value: 'u1' })
    for (const option of screen.getAllByRole('option')) {
      expect(option.children).toHaveLength(0)
      expect(option.textContent?.trim()).not.toBe('')
    }
  })

  it('badges the current assignee beside the field label instead', () => {
    // The badge does not vanish, it moves: an <option> cannot hold it, so it
    // describes whoever is currently selected, next to the label.
    renderPicker({ value: 'u1' })
    expect(
      screen.getByLabelText('Ada Lovelace is an external contractor')
    ).toBeInTheDocument()
  })

  it('does not badge the label when the current assignee is internal', () => {
    renderPicker({ value: 'u2' })
    expect(
      screen.queryByLabelText('Grace Hopper is an external contractor')
    ).not.toBeInTheDocument()
  })

  it('does not badge the label when nobody is assigned', () => {
    renderPicker({ value: '' })
    expect(screen.queryByText('Contractor')).not.toBeInTheDocument()
  })
})

describe('an assignee who is no longer a member', () => {
  it('keeps both suffixes rather than trading one for the other', () => {
    // Somebody can have left the organisation AND have been a contractor.
    // Dropping either half loses information the reader needs.
    const departed: UserSimple = {
      id: 'u9',
      email: 'gone@example.com',
      display_name: 'Katherine Johnson',
    }
    renderPicker({
      value: 'u9',
      resolved: departed,
      memberTypeOf: (id) => (id === 'u9' ? 'external_contractor' : undefined),
    })
    expect(optionTexts()).toContain(
      'Katherine Johnson (not a current member) (Contractor)'
    )
  })

  it('stays visible in the list at all', () => {
    // The failure this component was written to prevent: an assignment that
    // silently renders as Unassigned.
    const departed: UserSimple = { id: 'u9', email: 'gone@example.com' }
    renderPicker({ value: 'u9', resolved: departed })
    const select = screen.getByRole('combobox') as HTMLSelectElement
    expect(select.value).toBe('u9')
    expect(within(select).getAllByRole('option')).toHaveLength(4)
  })
})
