/**
 * controlGraphLayout — positions for the control relationship graph.
 *
 * Three columns: evidence on the left, the control in the middle, frameworks
 * on the right. A framework's requirement IDs sit in a fourth column beside it
 * when that framework is expanded. Rows are stacked cumulatively, so an
 * expanded framework pushes the ones below it down instead of overlapping them.
 *
 * Pure: no React Flow imports, so it is unit-testable in jsdom.
 */

export type GraphNodeKind = 'evidence' | 'control' | 'framework' | 'requirement'

export interface LayoutNode {
  id: string
  kind: GraphNodeKind
  x: number
  y: number
  /** Evidence ID, framework name or requirement ID. */
  key: string
  label: string
  /** Framework rows: how many requirement IDs it maps. */
  count?: number
  expanded?: boolean
}

export interface LayoutEdge {
  id: string
  source: string
  target: string
  kind: 'evidence' | 'framework' | 'requirement'
}

export interface ControlGraphInput {
  scf_id: string
  control_name: string
  artifactsResolved: { id: string; title: string }[]
  frameworksResolved: Record<string, string[]>
}

export interface ControlGraphLayout {
  nodes: LayoutNode[]
  edges: LayoutEdge[]
  /** Frameworks shown after filtering, in display order. */
  frameworks: string[]
  totalFrameworks: number
  totalRequirements: number
  /** Node IDs to frame on first view: control, evidence and the top rows. */
  initialViewIds: string[]
}

export const NODE_WIDTH = {
  evidence: 260,
  control: 260,
  framework: 320,
  requirement: 150,
} as const

const COL_X = {
  evidence: -NODE_WIDTH.evidence - 140,
  control: 0,
  framework: NODE_WIDTH.control + 140,
  requirement: NODE_WIDTH.control + 140 + NODE_WIDTH.framework + 60,
}

/** Vertical pitch of one framework row and of one requirement ID. */
export const FRAMEWORK_ROW = 48
export const REQUIREMENT_ROW = 36
const EVIDENCE_ROW = 64
/** Framework rows framed on the first view. */
const INITIAL_ROWS = 12

export const controlNodeId = (scfId: string) => `control-${scfId}`
export const evidenceNodeId = (id: string) => `evidence-${id}`
export const frameworkNodeId = (fw: string) => `fw-${fw}`
export const requirementNodeId = (fw: string, i: number) => `fw-${fw}-req-${i}`

function matches(query: string, fw: string, refs: string[]): boolean {
  if (!query) return true
  const q = query.toLowerCase()
  return fw.toLowerCase().includes(q) || refs.some((r) => r.toLowerCase().includes(q))
}

export function layoutControlGraph(
  control: ControlGraphInput,
  options: { query?: string; expanded?: ReadonlySet<string> } = {},
): ControlGraphLayout {
  const query = (options.query ?? '').trim()
  const expanded = options.expanded ?? new Set<string>()
  const nodes: LayoutNode[] = []
  const edges: LayoutEdge[] = []

  const allFrameworks = Object.keys(control.frameworksResolved).sort((a, b) =>
    a.localeCompare(b, undefined, { sensitivity: 'base', numeric: true }),
  )
  const frameworks = allFrameworks.filter((fw) =>
    matches(query, fw, control.frameworksResolved[fw] ?? []),
  )

  // Framework column, stacked top-down from y = 0.
  let y = 0
  const rowTops: number[] = []
  frameworks.forEach((fw) => {
    const refs = control.frameworksResolved[fw] ?? []
    const isOpen = expanded.has(fw) && refs.length > 0
    const fwId = frameworkNodeId(fw)
    rowTops.push(y)
    nodes.push({
      id: fwId,
      kind: 'framework',
      x: COL_X.framework,
      y,
      key: fw,
      label: fw,
      count: refs.length,
      expanded: isOpen,
    })
    if (isOpen) {
      refs.forEach((ref, j) => {
        const reqId = requirementNodeId(fw, j)
        nodes.push({
          id: reqId,
          kind: 'requirement',
          x: COL_X.requirement,
          y: y + j * REQUIREMENT_ROW,
          key: ref,
          label: ref,
        })
        edges.push({ id: `${fwId}->${reqId}`, source: fwId, target: reqId, kind: 'requirement' })
      })
    }
    y += Math.max(FRAMEWORK_ROW, isOpen ? refs.length * REQUIREMENT_ROW + 12 : 0)
  })

  // The control sits level with the middle of the first screenful of rows, so
  // the opening view shows it beside its first frameworks rather than centred
  // on a column that may run thousands of pixels down.
  const firstScreen = rowTops.length > INITIAL_ROWS ? rowTops[INITIAL_ROWS] : y
  const controlY = Math.max(0, firstScreen / 2 - 24)
  const centerId = controlNodeId(control.scf_id)
  nodes.push({
    id: centerId,
    kind: 'control',
    x: COL_X.control,
    y: controlY,
    key: control.scf_id,
    label: control.control_name,
  })
  frameworks.forEach((fw) => {
    const fwId = frameworkNodeId(fw)
    edges.push({ id: `${centerId}->${fwId}`, source: centerId, target: fwId, kind: 'framework' })
  })

  // Evidence column, centred on the control.
  const evidenceTop = controlY + 24 - (control.artifactsResolved.length * EVIDENCE_ROW) / 2
  control.artifactsResolved.forEach((a, i) => {
    const id = evidenceNodeId(a.id)
    nodes.push({
      id,
      kind: 'evidence',
      x: COL_X.evidence,
      y: evidenceTop + i * EVIDENCE_ROW,
      key: a.id,
      label: a.title,
    })
    edges.push({ id: `${id}->${centerId}`, source: id, target: centerId, kind: 'evidence' })
  })

  const initialViewIds = [
    centerId,
    ...control.artifactsResolved.map((a) => evidenceNodeId(a.id)),
    ...nodes
      .filter((n) => (n.kind === 'framework' || n.kind === 'requirement') && n.y < firstScreen)
      .map((n) => n.id),
  ]

  return {
    nodes,
    edges,
    frameworks,
    totalFrameworks: allFrameworks.length,
    totalRequirements: allFrameworks.reduce(
      (sum, fw) => sum + (control.frameworksResolved[fw]?.length ?? 0),
      0,
    ),
    initialViewIds,
  }
}
