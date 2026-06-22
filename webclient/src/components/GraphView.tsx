import React, { useMemo, useCallback, useState, useEffect } from 'react'
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  Node,
  Edge,
  Position,
  useNodesState,
  useEdgesState,
  NodeMouseHandler,
  EdgeMouseHandler
} from 'reactflow'
import 'reactflow/dist/style.css'
import type { EnrichedControl } from '../types'

interface Props {
  control: EnrichedControl
}

// Hook to read CSS variables (for ReactFlow which requires inline styles)
function useGraphColors() {
  const [colors, setColors] = useState({
    controlBg: '#fffce8',
    controlBorder: '#444444',
    artifactBg: '#eafff4',
    artifactBorder: '#22aa66',
    frameworkBg: '#eef5ff',
    frameworkBorder: '#2266cc',
    frameworkChildBg: '#f7fbff',
    highlight: '#2563eb',
    background: '#aaaaaa'
  });

  useEffect(() => {
    const updateColors = () => {
      const style = getComputedStyle(document.documentElement);
      setColors({
        controlBg: style.getPropertyValue('--graph-control-bg').trim() || '#fffce8',
        controlBorder: style.getPropertyValue('--graph-control-border').trim() || '#444444',
        artifactBg: style.getPropertyValue('--graph-artifact-bg').trim() || '#eafff4',
        artifactBorder: style.getPropertyValue('--graph-artifact-border').trim() || '#22aa66',
        frameworkBg: style.getPropertyValue('--graph-framework-bg').trim() || '#eef5ff',
        frameworkBorder: style.getPropertyValue('--graph-framework-border').trim() || '#2266cc',
        frameworkChildBg: style.getPropertyValue('--graph-framework-child-bg').trim() || '#f7fbff',
        highlight: style.getPropertyValue('--graph-highlight').trim() || '#2563eb',
        background: style.getPropertyValue('--graph-background').trim() || '#aaaaaa'
      });
    };

    updateColors();

    // Listen for theme changes
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        if (mutation.attributeName === 'data-theme') {
          updateColors();
        }
      });
    });

    observer.observe(document.documentElement, { attributes: true });

    return () => observer.disconnect();
  }, []);

  return colors;
}

export default function GraphView({ control }: Props) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null)
  const graphColors = useGraphColors()

  const initialNodesAndEdges = useMemo(() => {
    const nodes: Node[] = []
    const edges: Edge[] = []

    const centerId = `control-${control.scf_id}`
    nodes.push({
      id: centerId,
      data: { label: `${control.scf_id}: ${control.control_name}` },
      position: { x: 0, y: 0 },
      style: {
        padding: 12,
        border: `2px solid ${graphColors.controlBorder}`,
        borderRadius: 8,
        background: graphColors.controlBg,
        fontWeight: 600,
        fontSize: '14px',
        boxShadow: '0 4px 6px rgba(0,0,0,0.1)'
      },
      draggable: true
    })

    // Artifacts on the left
    control.artifactsResolved.forEach((a, index) => {
      const y = index * 80 - (control.artifactsResolved.length * 80) / 2
      const id = `artifact-${a.id}`
      nodes.push({
        id,
        data: { label: `${a.id}: ${a.title}` },
        position: { x: -450, y },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        style: {
          padding: 8,
          border: `2px solid ${graphColors.artifactBorder}`,
          borderRadius: 6,
          background: graphColors.artifactBg,
          cursor: 'grab',
          transition: 'all 0.2s'
        },
        draggable: true
      })
      edges.push({
        id: `${id}->${centerId}`,
        source: id,
        target: centerId,
        animated: false,
        style: { strokeWidth: 2 },
        type: 'smoothstep'
      })
    })

    // Framework group nodes on the right
    const frameworkNames = Object.keys(control.frameworksResolved)
    frameworkNames.forEach((fw, i) => {
      const y = i * 100 - (frameworkNames.length * 100) / 2
      const fwNodeId = `fw-${fw}`
      nodes.push({
        id: fwNodeId,
        data: { label: fw },
        position: { x: 450, y },
        sourcePosition: Position.Left,
        targetPosition: Position.Right,
        style: {
          padding: 8,
          border: `2px solid ${graphColors.frameworkBorder}`,
          borderRadius: 6,
          background: graphColors.frameworkBg,
          cursor: 'grab',
          transition: 'all 0.2s'
        },
        draggable: true
      })
      edges.push({
        id: `${centerId}->${fwNodeId}`,
        source: centerId,
        target: fwNodeId,
        animated: false,
        style: { strokeWidth: 2 },
        type: 'smoothstep'
      })

      const refs = control.frameworksResolved[fw]
      refs.forEach((ref, j) => {
        const childId = `fw-${fw}-${j}`
        nodes.push({
          id: childId,
          data: { label: ref },
          position: { x: 650, y: y + j * 50 - (refs.length * 50) / 2 },
          sourcePosition: Position.Left,
          targetPosition: Position.Right,
          style: {
            padding: 6,
            border: `1px dashed ${graphColors.frameworkBorder}`,
            borderRadius: 6,
            background: graphColors.frameworkChildBg,
            cursor: 'grab',
            fontSize: '12px',
            transition: 'all 0.2s'
          },
          draggable: true
        })
        edges.push({
          id: `${fwNodeId}->${childId}`,
          source: fwNodeId,
          target: childId,
          animated: false,
          style: { strokeWidth: 1, strokeDasharray: '5,5' },
          type: 'smoothstep'
        })
      })
    })

    return { nodes, edges }
  }, [control, graphColors])

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodesAndEdges.nodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialNodesAndEdges.edges)

  // Update nodes and edges when control changes
  useEffect(() => {
    setNodes(initialNodesAndEdges.nodes)
    setEdges(initialNodesAndEdges.edges)
    setSelectedNodeId(null)
    setHoveredNodeId(null)
  }, [control.scf_id, setNodes, setEdges])

  // Get connected edges for a node
  const getConnectedEdges = useCallback((nodeId: string) => {
    return edges.filter(edge => edge.source === nodeId || edge.target === nodeId)
  }, [edges])

  // Handle node click - highlight node and connected edges
  const onNodeClick: NodeMouseHandler = useCallback((event, node) => {
    setSelectedNodeId(node.id)

    const connectedEdgeIds = getConnectedEdges(node.id).map(e => e.id)

    setEdges(edges => edges.map(edge => ({
      ...edge,
      animated: connectedEdgeIds.includes(edge.id),
      style: {
        ...edge.style,
        stroke: connectedEdgeIds.includes(edge.id) ? graphColors.highlight : undefined,
        strokeWidth: connectedEdgeIds.includes(edge.id) ? 3 : edge.style?.strokeWidth
      }
    })))

    setNodes(nodes => nodes.map(n => ({
      ...n,
      style: {
        ...n.style,
        boxShadow: n.id === node.id ? `0 0 0 3px ${graphColors.highlight}` : n.style?.boxShadow,
        transform: n.id === node.id ? 'scale(1.05)' : undefined
      }
    })))
  }, [getConnectedEdges, setEdges, setNodes, graphColors.highlight])

  // Handle node hover
  const onNodeMouseEnter: NodeMouseHandler = useCallback((event, node) => {
    setHoveredNodeId(node.id)
    const connectedEdgeIds = getConnectedEdges(node.id).map(e => e.id)

    setEdges(edges => edges.map(edge => ({
      ...edge,
      animated: connectedEdgeIds.includes(edge.id),
      style: {
        ...edge.style,
        opacity: connectedEdgeIds.includes(edge.id) ? 1 : 0.3
      }
    })))
  }, [getConnectedEdges, setEdges])

  const onNodeMouseLeave: NodeMouseHandler = useCallback(() => {
    setHoveredNodeId(null)
    setEdges(edges => edges.map(edge => ({
      ...edge,
      animated: false,
      style: {
        ...edge.style,
        opacity: 1
      }
    })))
  }, [setEdges])

  // Handle pane click - clear selection
  const onPaneClick = useCallback(() => {
    setSelectedNodeId(null)
    setEdges(edges => edges.map(edge => ({
      ...edge,
      animated: false,
      style: {
        ...edge.style,
        stroke: undefined,
        strokeWidth: edge.id.includes('artifact') ? 2 : edge.style?.strokeWidth
      }
    })))
    setNodes(nodes => nodes.map(n => ({
      ...n,
      style: {
        ...n.style,
        boxShadow: n.id.startsWith('control-') ? '0 4px 6px rgba(0,0,0,0.1)' : undefined,
        transform: undefined
      }
    })))
  }, [setEdges, setNodes])

  return (
    <div style={{ width: '100%', height: '100%' }}>
      <ReactFlow
        key={control.scf_id}
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={onNodeClick}
        onNodeMouseEnter={onNodeMouseEnter}
        onNodeMouseLeave={onNodeMouseLeave}
        onPaneClick={onPaneClick}
        nodesDraggable={true}
        nodesConnectable={false}
        elementsSelectable={true}
        fitView
        minZoom={0.2}
        maxZoom={2}
        defaultEdgeOptions={{
          type: 'smoothstep',
          animated: false
        }}
      >
        <Background color={graphColors.background} gap={16} />
        <MiniMap
          nodeColor={(node) => {
            if (node.id.startsWith('control-')) return graphColors.controlBg
            if (node.id.startsWith('artifact-')) return graphColors.artifactBg
            if (node.id.startsWith('fw-')) return graphColors.frameworkBg
            return graphColors.frameworkChildBg
          }}
          maskColor="rgba(0, 0, 0, 0.1)"
        />
        <Controls />
      </ReactFlow>
    </div>
  )
}
