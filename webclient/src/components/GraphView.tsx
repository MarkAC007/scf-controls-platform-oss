/**
 * GraphView — a control's relationships: evidence → control → frameworks →
 * requirement IDs.
 *
 * Positions come from `layoutControlGraph` (pure, tested). Requirement IDs are
 * collapsed by default — a control can map to 80+ frameworks and a few hundred
 * requirement IDs — and open per framework on click or all at once from the
 * toolbar. The first view frames the control, its evidence and the top rows at
 * a readable zoom; scrolling pans down the framework column.
 *
 * Colours come from the `--graph-*` tokens via CSS classes, so both themes
 * follow without reading computed styles.
 */
import { useCallback, useEffect, useMemo, useState, type JSX } from 'react'
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  Panel,
  Position,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeMouseHandler,
} from 'reactflow'
import 'reactflow/dist/style.css'
import type { EnrichedControl } from '../types'
import {
  NODE_WIDTH,
  layoutControlGraph,
  type GraphNodeKind,
  type LayoutNode,
} from './graph/controlGraphLayout'

interface Props {
  control: EnrichedControl
  /** Clicking an evidence node opens that evidence item. */
  onOpenEvidence?: (evidenceId: string) => void
}

const NODE_TYPE: Record<GraphNodeKind, string> = {
  evidence: 'input',
  control: 'default',
  framework: 'default',
  requirement: 'output',
}

function nodeLabel(n: LayoutNode): JSX.Element {
  switch (n.kind) {
    case 'evidence':
      return (
        <>
          <span className="graph-node-kicker">{n.key}</span>
          <span className="graph-node-title">{n.label}</span>
        </>
      )
    case 'control':
      return (
        <>
          <span className="graph-node-kicker">{n.key}</span>
          <span className="graph-node-title">{n.label}</span>
        </>
      )
    case 'framework':
      return (
        <>
          <span className="graph-node-title" title={n.label}>{n.label}</span>
          <span className="graph-node-count" aria-hidden="true">
            {n.count}
            <span className="graph-node-caret">{n.expanded ? '−' : '+'}</span>
          </span>
        </>
      )
    default:
      return <span className="graph-node-title">{n.label}</span>
  }
}

function GraphCanvas({ control, onOpenEvidence }: Props): JSX.Element {
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const { fitView } = useReactFlow()

  // A different control starts clean.
  useEffect(() => {
    setQuery('')
    setExpanded(new Set())
    setHoveredId(null)
  }, [control.scf_id])

  const layout = useMemo(
    () => layoutControlGraph(control, { query, expanded }),
    [control, query, expanded],
  )

  // Hover emphasises a node's own connections and fades the rest.
  const connected = useMemo(() => {
    if (!hoveredId) return null
    const ids = new Set<string>([hoveredId])
    const edgeIds = new Set<string>()
    for (const e of layout.edges) {
      if (e.source === hoveredId || e.target === hoveredId) {
        ids.add(e.source)
        ids.add(e.target)
        edgeIds.add(e.id)
      }
    }
    return { ids, edgeIds }
  }, [hoveredId, layout.edges])

  const nodes: Node[] = useMemo(
    () =>
      layout.nodes.map((n) => ({
        id: n.id,
        type: NODE_TYPE[n.kind],
        position: { x: n.x, y: n.y },
        data: { label: nodeLabel(n), kind: n.kind, key: n.key },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        draggable: false,
        connectable: false,
        className: [
          'graph-node',
          `graph-node--${n.kind}`,
          n.expanded ? 'is-expanded' : '',
          (n.kind === 'framework' && (n.count ?? 0) > 0) || (n.kind === 'evidence' && onOpenEvidence)
            ? 'is-clickable'
            : '',
          connected && !connected.ids.has(n.id) ? 'is-dim' : '',
        ]
          .filter(Boolean)
          .join(' '),
        style: { width: NODE_WIDTH[n.kind] },
      })),
    [layout.nodes, connected, onOpenEvidence],
  )

  const edges: Edge[] = useMemo(
    () =>
      layout.edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        // Stepped edges share their trunk, so a long framework column reads as
        // one bus line instead of a fan of curves.
        type: e.kind === 'evidence' ? 'default' : 'smoothstep',
        focusable: false,
        className: [
          'graph-edge',
          `graph-edge--${e.kind}`,
          connected ? (connected.edgeIds.has(e.id) ? 'is-active' : 'is-dim') : '',
        ]
          .filter(Boolean)
          .join(' '),
      })),
    [layout.edges, connected],
  )

  // Re-frame the top rows when the filter changes (not on expand — that
  // would jump the view away from the framework just opened).
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      void fitView({
        nodes: layout.initialViewIds.map((id) => ({ id })),
        padding: 0.15,
        maxZoom: 1,
        duration: 250,
      })
    })
    return () => cancelAnimationFrame(frame)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, control.scf_id, fitView])

  const onNodeClick: NodeMouseHandler = useCallback(
    (_event, node) => {
      const { kind, key } = node.data as { kind: GraphNodeKind; key: string }
      if (kind === 'framework') {
        setExpanded((prev) => {
          const next = new Set(prev)
          if (next.has(key)) next.delete(key)
          else next.add(key)
          return next
        })
      } else if (kind === 'evidence' && onOpenEvidence) {
        onOpenEvidence(key)
      }
    },
    [onOpenEvidence],
  )

  const allExpanded =
    layout.frameworks.length > 0 && layout.frameworks.every((fw) => expanded.has(fw))
  const toggleAll = () =>
    setExpanded(allExpanded ? new Set() : new Set(layout.frameworks))

  const shownRequirements = layout.frameworks.reduce(
    (sum, fw) => sum + (control.frameworksResolved[fw]?.length ?? 0),
    0,
  )

  return (
    <ReactFlow
      className="graph-canvas"
      nodes={nodes}
      edges={edges}
      onNodeClick={onNodeClick}
      onNodeMouseEnter={(_e, n) => setHoveredId(n.id)}
      onNodeMouseLeave={() => setHoveredId(null)}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
      panOnScroll
      fitView
      fitViewOptions={{
        nodes: layout.initialViewIds.map((id) => ({ id })),
        padding: 0.15,
        maxZoom: 1,
      }}
      minZoom={0.1}
      maxZoom={2}
      proOptions={{ hideAttribution: true }}
    >
      <Background className="graph-background" gap={20} />

      <Panel position="top-left" className="graph-toolbar">
        <input
          type="search"
          className="graph-search"
          placeholder="Filter frameworks or requirement IDs"
          aria-label="Filter frameworks or requirement IDs"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape' && query) setQuery('')
          }}
        />
        <span className="graph-toolbar-count" aria-live="polite">
          {layout.frameworks.length === layout.totalFrameworks
            ? `${layout.totalFrameworks} frameworks`
            : `${layout.frameworks.length} of ${layout.totalFrameworks} frameworks`}
        </span>
        <button
          type="button"
          className="btn-outline btn-sm"
          onClick={toggleAll}
          disabled={layout.frameworks.length === 0}
        >
          {allExpanded ? 'Collapse requirement IDs' : 'Show all requirement IDs'}
        </button>
      </Panel>

      <Panel position="bottom-left" className="graph-legend" aria-label="Legend">
        <span className="graph-legend-item">
          <i className="graph-swatch graph-swatch--evidence" />Evidence ({control.artifactsResolved.length})
        </span>
        <span className="graph-legend-item">
          <i className="graph-swatch graph-swatch--control" />Control
        </span>
        <span className="graph-legend-item">
          <i className="graph-swatch graph-swatch--framework" />Frameworks ({layout.frameworks.length})
        </span>
        <span className="graph-legend-item">
          <i className="graph-swatch graph-swatch--requirement" />Requirement IDs ({shownRequirements})
        </span>
        <span className="graph-legend-hint">
          Click a framework for its requirement IDs
          {onOpenEvidence ? ' · click evidence to open it' : ''} · scroll to pan · ⌘/Ctrl + scroll to zoom
        </span>
      </Panel>

      {layout.frameworks.length === 0 && query && (
        <Panel position="top-center" className="graph-empty">
          No framework or requirement ID matches “{query}”.
        </Panel>
      )}

      <MiniMap
        className="graph-minimap"
        pannable
        zoomable
        nodeClassName={(n) => `graph-mini--${(n.data as { kind: GraphNodeKind }).kind}`}
      />
      <Controls className="graph-controls" position="top-right" showInteractive={false} />
    </ReactFlow>
  )
}

export default function GraphView(props: Props): JSX.Element {
  return (
    <div className="graph-view">
      <ReactFlowProvider key={props.control.scf_id}>
        <GraphCanvas {...props} />
      </ReactFlowProvider>
    </div>
  )
}
