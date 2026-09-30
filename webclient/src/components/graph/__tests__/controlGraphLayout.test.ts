import { describe, expect, it } from 'vitest'
import {
  FRAMEWORK_ROW,
  REQUIREMENT_ROW,
  controlNodeId,
  frameworkNodeId,
  layoutControlGraph,
  type LayoutNode,
} from '../controlGraphLayout'

const CONTROL = {
  scf_id: 'AST-02',
  control_name: 'Asset Governance',
  artifactsResolved: [
    { id: 'E-AST-01', title: 'Asset inventory' },
    { id: 'E-AST-02', title: 'Asset policy' },
  ],
  frameworksResolved: {
    'NIST 800-53 R5': ['PM-5', 'CM-8', 'CM-8(1)'],
    'ISO 27001 2022': ['A.5.9'],
    'CIS CSC 8.1': ['1.1', '1.2'],
    'SOC 2': [],
  },
}

const byKind = (nodes: LayoutNode[], kind: LayoutNode['kind']) => nodes.filter((n) => n.kind === kind)

/** Vertical extents in one column must not overlap. */
function assertNoOverlap(nodes: LayoutNode[], pitch: number) {
  const ys = nodes.map((n) => n.y).sort((a, b) => a - b)
  for (let i = 1; i < ys.length; i++) expect(ys[i] - ys[i - 1]).toBeGreaterThanOrEqual(pitch)
}

describe('layoutControlGraph', () => {
  it('lists frameworks alphabetically with requirement IDs collapsed by default', () => {
    const { nodes, frameworks, edges } = layoutControlGraph(CONTROL)
    expect(frameworks).toEqual(['CIS CSC 8.1', 'ISO 27001 2022', 'NIST 800-53 R5', 'SOC 2'])
    expect(byKind(nodes, 'requirement')).toHaveLength(0)
    expect(byKind(nodes, 'framework').find((n) => n.key === 'NIST 800-53 R5')?.count).toBe(3)
    // every framework and every evidence item connects to the control
    expect(edges.filter((e) => e.source === controlNodeId('AST-02'))).toHaveLength(4)
    expect(edges.filter((e) => e.target === controlNodeId('AST-02'))).toHaveLength(2)
  })

  it('expanding a framework pushes later rows down instead of overlapping them', () => {
    const { nodes } = layoutControlGraph(CONTROL, { expanded: new Set(['CIS CSC 8.1', 'NIST 800-53 R5']) })
    const fws = byKind(nodes, 'framework')
    const reqs = byKind(nodes, 'requirement')
    expect(reqs.map((r) => r.key)).toEqual(['1.1', '1.2', 'PM-5', 'CM-8', 'CM-8(1)'])
    assertNoOverlap(fws, FRAMEWORK_ROW)
    assertNoOverlap(reqs, REQUIREMENT_ROW)
    // CIS's two requirement IDs end above the next framework row
    const iso = fws.find((n) => n.key === 'ISO 27001 2022')!
    const lastCis = reqs.find((r) => r.key === '1.2')!
    expect(iso.y).toBeGreaterThanOrEqual(lastCis.y + REQUIREMENT_ROW)
  })

  it('a framework with no requirement IDs does not expand', () => {
    const { nodes } = layoutControlGraph(CONTROL, { expanded: new Set(['SOC 2']) })
    expect(byKind(nodes, 'framework').find((n) => n.key === 'SOC 2')?.expanded).toBe(false)
    expect(byKind(nodes, 'requirement')).toHaveLength(0)
  })

  it('filters frameworks by name or requirement ID, case-insensitively', () => {
    expect(layoutControlGraph(CONTROL, { query: 'iso' }).frameworks).toEqual(['ISO 27001 2022'])
    expect(layoutControlGraph(CONTROL, { query: 'cm-8' }).frameworks).toEqual(['NIST 800-53 R5'])
    const none = layoutControlGraph(CONTROL, { query: 'zzz' })
    expect(none.frameworks).toEqual([])
    expect(none.totalFrameworks).toBe(4)
    // the control and its evidence stay on screen with no matches
    expect(byKind(none.nodes, 'control')).toHaveLength(1)
    expect(byKind(none.nodes, 'evidence')).toHaveLength(2)
  })

  it('frames only the first rows of a long framework column on first view', () => {
    const many = {
      ...CONTROL,
      frameworksResolved: Object.fromEntries(
        Array.from({ length: 80 }, (_, i) => [`FW ${String(i).padStart(2, '0')}`, ['R1']]),
      ),
    }
    const { initialViewIds } = layoutControlGraph(many)
    const framed = initialViewIds.filter((id) => id.startsWith('fw-'))
    expect(framed.length).toBeLessThanOrEqual(12)
    expect(framed[0]).toBe(frameworkNodeId('FW 00'))
    expect(initialViewIds).toContain(controlNodeId('AST-02'))
  })

  it('copes with a control that has no evidence and no frameworks', () => {
    const bare = { ...CONTROL, artifactsResolved: [], frameworksResolved: {} }
    const { nodes, edges, totalRequirements } = layoutControlGraph(bare)
    expect(nodes).toHaveLength(1)
    expect(edges).toHaveLength(0)
    expect(totalRequirements).toBe(0)
  })
})
