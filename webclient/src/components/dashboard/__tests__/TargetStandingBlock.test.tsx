// SPDX-License-Identifier: AGPL-3.0-only
/**
 * The dashboard's control maturity against target: the band is the target,
 * the bar the level reached, and the count says whether the domain is there.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import TargetStandingBlock from '../TargetStandingBlock'
import type { DomainTargetStanding, TargetStanding } from '../../../data/targetMaturity'

afterEach(cleanup)

const domain = (over: Partial<DomainTargetStanding> = {}): DomainTargetStanding => ({
  code: 'GOV', name: 'Governance', target: 'L2', controls: 18, rated: 16, average: 2.5, withTarget: 18, atTarget: 14, ...over,
})

const standing = (domains: DomainTargetStanding[], over: Partial<TargetStanding> = {}): TargetStanding => ({
  domains,
  withTarget: domains.reduce((n, d) => n + d.withTarget, 0),
  atTarget: domains.reduce((n, d) => n + d.atTarget, 0),
  noTarget: 0,
  domainsWithoutTarget: 0,
  ...over,
})

const track = (code: string) => screen.getByTestId(`mtarget-row-${code}`).querySelector<HTMLElement>('.mtarget-track')!

describe('TargetStandingBlock', () => {
  it('says how many controls with a target have reached it', () => {
    render(<TargetStandingBlock standing={standing([domain(), domain({ code: 'CLD', name: 'Cloud', target: 'L3', withTarget: 4, atTarget: 4, average: 3.5 })])} />)
    expect(screen.getByRole('region', { name: 'Control maturity against target' })).toHaveTextContent('18 of 22 controls with a target have reached it')
  })

  it('places the target and the level reached on the L0 to L5 scale', () => {
    render(<TargetStandingBlock standing={standing([domain()])} />)
    const bar = track('GOV')
    expect(bar.style.getPropertyValue('--mtarget-target')).toBe('40%')
    expect(bar.style.getPropertyValue('--mtarget-actual')).toBe('50%')
    expect(bar.querySelector('.mtarget-shadow')).not.toBeNull()
    expect(bar.querySelector('.mtarget-mark')).not.toBeNull()
    expect(bar.querySelector('.mtarget-fill')).not.toBeNull()
  })

  it('reads the row out in words', () => {
    render(<TargetStandingBlock standing={standing([domain()])} />)
    expect(track('GOV')).toHaveAccessibleName('Governance: average level 2.5, target L2, 14 of 18 controls at target')
  })

  it('marks a domain below target while any control is, even when the average is past the target', () => {
    render(<TargetStandingBlock standing={standing([domain(), domain({ code: 'CLD', target: 'L3', withTarget: 4, atTarget: 4, average: 3.5 })])} />)
    expect(screen.getByTestId('mtarget-row-GOV')).toHaveClass('mtarget-row--below')
    expect(screen.getByTestId('mtarget-row-CLD')).toHaveClass('mtarget-row--met')
  })

  it('draws no bar for a domain with no rated control', () => {
    render(<TargetStandingBlock standing={standing([domain({ rated: 0, average: null, atTarget: 0 })])} />)
    expect(track('GOV').querySelector('.mtarget-fill')).toBeNull()
    expect(track('GOV')).toHaveAccessibleName('Governance: no control rated, target L2, 0 of 18 controls at target')
  })

  it('draws no target band for a domain whose targets sit on single controls', () => {
    render(<TargetStandingBlock standing={standing([domain({ target: null, withTarget: 2, atTarget: 1 })])} />)
    expect(track('GOV').querySelector('.mtarget-shadow')).toBeNull()
    expect(track('GOV').querySelector('.mtarget-mark')).toBeNull()
    expect(screen.getByTestId('mtarget-row-GOV')).toHaveTextContent('own targets')
  })

  it('says how many controls have no target and where targets are set', () => {
    render(<TargetStandingBlock standing={standing([domain()], { noTarget: 320, domainsWithoutTarget: 26 })} />)
    expect(screen.getByText(/320 controls have no target, 26 domains with none set\./)).toHaveTextContent('Settings, under Maturity targets')
  })

  it('shows the six domains furthest behind, and the rest on request', () => {
    const many = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'].map(code => domain({ code, name: `Domain ${code}` }))
    render(<TargetStandingBlock standing={standing(many)} />)
    expect(screen.getAllByRole('listitem')).toHaveLength(6)
    fireEvent.click(screen.getByRole('button', { name: 'Show all 8 domains' }))
    expect(screen.getAllByRole('listitem')).toHaveLength(8)
    fireEvent.click(screen.getByRole('button', { name: 'Show the 6 furthest behind' }))
    expect(screen.getAllByRole('listitem')).toHaveLength(6)
  })

  it('offers no toggle when every domain fits', () => {
    render(<TargetStandingBlock standing={standing([domain()])} />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('says so when no control has a target, and draws no rows', () => {
    render(<TargetStandingBlock standing={standing([], { noTarget: 40, domainsWithoutTarget: 5 })} />)
    const block = screen.getByRole('region', { name: 'Control maturity against target' })
    expect(within(block).getByText(/No control has a target maturity yet/)).toBeInTheDocument()
    expect(within(block).queryByRole('list')).not.toBeInTheDocument()
    expect(block).not.toHaveTextContent('have reached it')
  })
})
