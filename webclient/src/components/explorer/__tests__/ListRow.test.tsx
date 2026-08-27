import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import ExplorerListRow, {
  RowChip,
  RowMeta,
  RowTickCircle,
  RowWeightBar,
} from '../ListRow'

describe('ExplorerListRow', () => {
  it('renders the monoId', () => {
    render(<ExplorerListRow monoId="GOV-01" title="My Title" />)
    expect(screen.getByText('GOV-01')).toBeInTheDocument()
  })

  it('renders the title', () => {
    render(<ExplorerListRow monoId="GOV-01" title="Security Program" />)
    expect(screen.getByText('Security Program')).toBeInTheDocument()
  })

  it('renders description when provided', () => {
    render(
      <ExplorerListRow monoId="GOV-01" title="Title" description="Some description" />,
    )
    expect(screen.getByText('Some description')).toBeInTheDocument()
  })

  it('does not render description when omitted', () => {
    const { container } = render(<ExplorerListRow monoId="GOV-01" title="Title" />)
    expect(container.querySelector('.explorer-row-desc')).not.toBeInTheDocument()
  })

  it('accent=true adds explorer-row-tick--accent class', () => {
    const { container } = render(
      <ExplorerListRow monoId="GOV-01" title="Title" accent={true} />,
    )
    expect(container.querySelector('.explorer-row-tick--accent')).toBeInTheDocument()
  })

  it('accent=false does not add explorer-row-tick--accent class', () => {
    const { container } = render(
      <ExplorerListRow monoId="GOV-01" title="Title" accent={false} />,
    )
    expect(container.querySelector('.explorer-row-tick--accent')).not.toBeInTheDocument()
  })

  it('highlighted=true adds explorer-row--highlighted class', () => {
    const { container } = render(
      <ExplorerListRow monoId="GOV-01" title="Title" highlighted={true} />,
    )
    expect(container.querySelector('.explorer-row--highlighted')).toBeInTheDocument()
  })

  it('highlighted=false does not add explorer-row--highlighted class', () => {
    const { container } = render(
      <ExplorerListRow monoId="GOV-01" title="Title" highlighted={false} />,
    )
    expect(container.querySelector('.explorer-row--highlighted')).not.toBeInTheDocument()
  })

  it('renders children in the row', () => {
    render(
      <ExplorerListRow monoId="GOV-01" title="Title">
        <span>Extra cell</span>
      </ExplorerListRow>,
    )
    expect(screen.getByText('Extra cell')).toBeInTheDocument()
  })

  it('with onClick has role="button" and tabIndex=0', () => {
    render(
      <ExplorerListRow monoId="GOV-01" title="Title" onClick={vi.fn()} />,
    )
    const row = screen.getByRole('button')
    expect(row).toHaveAttribute('tabindex', '0')
  })

  it('without onClick has no role="button"', () => {
    render(<ExplorerListRow monoId="GOV-01" title="Title" />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('click fires onClick once', () => {
    const onClick = vi.fn()
    render(<ExplorerListRow monoId="GOV-01" title="Title" onClick={onClick} />)
    fireEvent.click(screen.getByRole('button'))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('Enter key fires onClick once', () => {
    const onClick = vi.fn()
    render(<ExplorerListRow monoId="GOV-01" title="Title" onClick={onClick} />)
    fireEvent.keyDown(screen.getByRole('button'), { key: 'Enter' })
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('Space key fires onClick once', () => {
    const onClick = vi.fn()
    render(<ExplorerListRow monoId="GOV-01" title="Title" onClick={onClick} />)
    fireEvent.keyDown(screen.getByRole('button'), { key: ' ' })
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('other keys do not fire onClick', () => {
    const onClick = vi.fn()
    render(<ExplorerListRow monoId="GOV-01" title="Title" onClick={onClick} />)
    fireEvent.keyDown(screen.getByRole('button'), { key: 'Tab' })
    expect(onClick).not.toHaveBeenCalled()
  })

  it('has explorer-row class', () => {
    const { container } = render(<ExplorerListRow monoId="GOV-01" title="Title" />)
    expect(container.querySelector('.explorer-row')).toBeInTheDocument()
  })

  it('omitting monoId renders no .explorer-row-id element', () => {
    const { container } = render(<ExplorerListRow title="Title Only" />)
    expect(container.querySelector('.explorer-row-id')).not.toBeInTheDocument()
  })
})

describe('RowChip', () => {
  it('renders children', () => {
    render(<RowChip>Process</RowChip>)
    expect(screen.getByText('Process')).toBeInTheDocument()
  })

  it('has explorer-row-chip class', () => {
    const { container } = render(<RowChip>Process</RowChip>)
    expect(container.querySelector('.explorer-row-chip')).toBeInTheDocument()
  })
})

describe('RowMeta', () => {
  it('renders children', () => {
    render(<RowMeta>97 maps</RowMeta>)
    expect(screen.getByText('97 maps')).toBeInTheDocument()
  })

  it('applies width as inline style when provided', () => {
    const { container } = render(<RowMeta width={62}>97 maps</RowMeta>)
    const el = container.firstChild as HTMLElement
    expect(el.style.width).toBe('62px')
  })
})

describe('RowWeightBar', () => {
  it('renders the numeric value', () => {
    render(<RowWeightBar value={7} />)
    expect(screen.getByText('7')).toBeInTheDocument()
  })

  it('renders value 10', () => {
    render(<RowWeightBar value={10} />)
    expect(screen.getByText('10')).toBeInTheDocument()
  })

  it('renders value 0', () => {
    render(<RowWeightBar value={0} />)
    expect(screen.getByText('0')).toBeInTheDocument()
  })

  it('has explorer-row-weight class', () => {
    const { container } = render(<RowWeightBar value={5} />)
    expect(container.querySelector('.explorer-row-weight')).toBeInTheDocument()
  })
})

describe('RowTickCircle', () => {
  it('on=true renders a circle element (not empty)', () => {
    const { container } = render(<RowTickCircle on={true} />)
    // Should have the tick circle container
    const el = container.querySelector('.explorer-row-tick-circle')
    expect(el).toBeInTheDocument()
    // Should contain an svg
    expect(el?.querySelector('svg')).toBeInTheDocument()
  })

  it('on=false renders off-state spacer (no svg, has --off modifier)', () => {
    const { container } = render(<RowTickCircle on={false} />)
    const el = container.querySelector('.explorer-row-tick-circle')
    expect(el).toBeInTheDocument()
    expect(el?.querySelector('svg')).not.toBeInTheDocument()
    expect(el).toHaveClass('explorer-row-tick-circle--off')
  })
})
