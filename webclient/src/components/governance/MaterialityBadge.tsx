import type { JSX } from 'react'

/* PIG R6: a material control may not leave scope, or be marked not applicable,
   without an approving admin. Shares the basis badge's shape. */
export default function MaterialityBadge({ material, title }: { material: boolean; title?: string }): JSX.Element {
  return (
    <span className={`basis-badge ${material ? 'basis-badge--material' : 'basis-badge--none'}`} title={title}>
      {material ? 'Material' : 'Not material'}
    </span>
  )
}
