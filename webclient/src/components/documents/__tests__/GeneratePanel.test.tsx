/**
 * The generate panel's primary action must be reachable.
 *
 * The panel is as tall as the number of generators the organisation has. With
 * enough document types on offer the Generate button — which lives in
 * `.doc-gen-footer` at the very bottom — fell below the fold, and nothing on
 * screen suggested it was there. The fix is a layout one: `.doc-gen-footer` is
 * `position: sticky; bottom: 0`, so the button, the Cancel beside it and the
 * "N documents selected" count stay on screen for the whole scroll.
 *
 * jsdom does not do layout, so no test here can prove the bar actually sticks —
 * that needs a browser, and is recorded as such rather than faked with an
 * assertion about a CSS property jsdom cannot compute. What these tests do
 * protect is the structural precondition the CSS depends on: the primary
 * action, the cancel and the selected-count all live inside the element the
 * sticky rule targets. A later refactor that lifts the button out of that
 * footer would silently un-fix the defect, and would fail here instead.
 */
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import GeneratePanel from '../GeneratePanel'
import {
  getDocGenSettings,
  getGenerationStatus,
  listGeneratableDomains,
  listGenerators,
} from '../../../data/documentsApi'
import type {
  DocGenSettings,
  GenerationStatus,
  GeneratorInfo,
} from '../../../data/documentsApi'

vi.mock('../../../data/documentsApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../data/documentsApi')>()
  return {
    ...actual,
    listGenerators: vi.fn(),
    getDocGenSettings: vi.fn(),
    listGeneratableDomains: vi.fn(),
    getGenerationStatus: vi.fn(),
  }
})

const SETTINGS: DocGenSettings = {
  enabled: true,
  derivative_generators_enabled: true,
  licence_acknowledged: true,
  licence_acknowledged_at: null,
  licence_acknowledged_by_email: null,
  licence_text_version: null,
  daily_generation_limit: 20,
  platform_disabled: false,
  acknowledgement_text: '',
}

const IDLE: GenerationStatus = { status: 'idle' }

/** Enough generators that the panel is taller than any plausible viewport. */
function manyGenerators(): GeneratorInfo[] {
  return Array.from({ length: 12 }, (_, i) => ({
    name: `gen_${i}`,
    display_name: `Generator ${i}`,
    tier: 1,
    document_type: 'report',
    is_derivative: false,
    domain_scoped: false,
    description: 'A deterministic report built from control data.',
  }))
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getDocGenSettings).mockResolvedValue(SETTINGS)
  vi.mocked(listGeneratableDomains).mockResolvedValue([])
  vi.mocked(getGenerationStatus).mockResolvedValue(IDLE)
  vi.mocked(listGenerators).mockResolvedValue(manyGenerators())
})

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(
    <QueryClientProvider client={client}>
      <GeneratePanel organizationId="org-1" onClose={() => {}} />
    </QueryClientProvider>
  )
}

describe('generate panel primary action', () => {
  it('keeps Generate, Cancel and the selected count inside the sticky footer', async () => {
    const { container } = renderPanel()
    await screen.findByText('Generator 0')

    const footer = container.querySelector('.doc-gen-footer')
    expect(footer).not.toBeNull()

    const generate = screen.getByRole('button', { name: 'Generate' })
    const cancel = screen.getByRole('button', { name: 'Cancel' })
    expect(footer).toContainElement(generate)
    expect(footer).toContainElement(cancel)
    expect(footer?.textContent).toContain('0 documents selected')
  })

  it('explains the disabled Generate button with a count in the same bar', async () => {
    // Nothing ticked, so the button is disabled. Before the footer stuck to the
    // bottom of the scrollport, the count that explains why was off-screen with
    // it; the two travelling together is the point.
    const { container } = renderPanel()
    await screen.findByText('Generator 0')

    const footer = container.querySelector('.doc-gen-footer')
    expect(screen.getByRole('button', { name: 'Generate' })).toBeDisabled()
    expect(footer?.querySelector('.doc-gen-count')?.textContent).toBe(
      '0 documents selected'
    )
  })
})
