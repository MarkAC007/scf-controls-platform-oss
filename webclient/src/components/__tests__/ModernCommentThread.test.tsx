/**
 * ModernCommentThread — bench restyle (#1154).
 *
 * What these tests defend:
 *   - by default the thread has a plain h4 heading with the count, because four
 *     of its five mount sites already print a label right above it;
 *   - with header="strip" (the Scoping notes tab) the heading sits in the bench
 *     header strip instead, still as an h4;
 *   - the placeholder is short and the formatting tip prints once, below the box;
 *   - the post button reads in sentence case, "Post comment" / "Post reply";
 *   - the component renders no inline styles of its own, so theme tokens and
 *     dark mode reach every part of it;
 *   - mention markup is still escaped and sanitised: user HTML never becomes
 *     an element, and a valid @[Name] mention and `code` span still render
 *     with theme tokens rather than hard-coded colours;
 *   - a user with an empty id still gets a real avatar tone.
 */
import type { ComponentProps } from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ModernCommentThread } from '../ModernCommentThread'

vi.mock('../../data/apiClient', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))

import { apiClient } from '../../data/apiClient'
const mockApi = vi.mocked(apiClient)

const ALICE = { id: 'u-alice', email: 'alice@example.com', display_name: 'Alice Smith' }

const COMMENT = {
  id: 'c-1',
  content: 'Looks good <img src=x onerror="alert(1)"> @[Alice Smith] **done** run `npm ci`',
  mentions: [ALICE.id],
  user: ALICE,
  created_at: new Date().toISOString(),
  is_edited: false,
  edited_at: null,
}

function stubApi(comments: unknown[]) {
  mockApi.get.mockImplementation((url: string) => {
    if (url.startsWith('/comments')) return Promise.resolve(comments)
    if (url.includes('/members')) return Promise.resolve([{ user: ALICE }])
    return Promise.resolve([])
  })
}

function renderThread(props: Partial<ComponentProps<typeof ModernCommentThread>> = {}) {
  return render(
    <ModernCommentThread commentableType="control" commentableId="ctl-1" organizationId="org-1" {...props} />,
  )
}

describe('ModernCommentThread', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('has a plain h4 heading by default, a short placeholder and one tip line', async () => {
    stubApi([])
    const { container } = renderThread()

    expect(screen.getByRole('heading', { level: 4, name: 'Comments · 0' })).toBeInTheDocument()
    expect(container.querySelector('.container-header')).toBeNull()

    const box = screen.getByPlaceholderText('Write a comment…')
    expect(box).toHaveClass('comment-thread-input')

    expect(screen.getAllByText(/\*\*bold\*\*/)).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Post comment' })).toHaveClass('btn-primary', 'btn-small')
    expect(screen.queryByRole('button', { name: 'Post Comment' })).toBeNull()
  })

  it('puts the h4 heading in the bench header strip when header="strip"', async () => {
    stubApi([])
    const { container } = renderThread({ header: 'strip' })

    const heading = screen.getByRole('heading', { level: 4, name: 'Comments · 0' })
    expect(heading).toHaveClass('container-title')
    expect(heading.parentElement).toHaveClass('container-header', 'bench-header')
    expect(container.querySelectorAll('h4')).toHaveLength(1)
  })

  it('gives a user with an empty id a real avatar tone', async () => {
    stubApi([{ ...COMMENT, id: 'c-2', user: { ...ALICE, id: '' }, mentions: [] }])
    const { container } = renderThread()
    await screen.findByText('Alice Smith')

    const avatar = container.querySelector('.comment-thread-avatar')
    expect(avatar).toHaveClass('comment-thread-avatar--blue')
    expect(avatar?.className).not.toContain('undefined')
  })

  it('switches the button to "Post reply" when replying', async () => {
    stubApi([COMMENT])
    renderThread()

    fireEvent.click(await screen.findByRole('button', { name: /Reply/ }))
    expect(screen.getByRole('button', { name: 'Post reply' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Post comment' })).toBeNull()
  })

  it('renders no inline style blocks of its own', async () => {
    stubApi([COMMENT])
    const { container } = renderThread()
    await screen.findByText('Alice Smith')
    fireEvent.click(screen.getByRole('button', { name: /Reply/ }))

    // Only the sanitised mention/code spans inside the rendered comment body
    // may carry a style attribute; every element the component builds must not.
    const styled = Array.from(container.querySelectorAll('[style]')).filter(
      (el) => !el.closest('.comment-thread-body'),
    )
    expect(styled).toEqual([])
  })

  it('keeps mention markup escaped and sanitised', async () => {
    stubApi([COMMENT])
    const { container } = renderThread()

    const body = await waitFor(() => {
      const el = container.querySelector('.comment-thread-body')
      expect(el).not.toBeNull()
      return el as HTMLElement
    })
    await waitFor(() => expect(body.querySelector('span')).not.toBeNull())

    expect(body.querySelector('img')).toBeNull()
    expect(body.querySelector('[onerror]')).toBeNull()
    expect(body).toHaveTextContent('<img src=x')
    expect(body.querySelector('span')).toHaveTextContent('@Alice Smith')
    expect(body.querySelector('strong')).toHaveTextContent('done')
    const code = body.querySelector('code')
    expect(code).not.toBeNull()
    expect(code).toHaveTextContent('npm ci')
    expect(code?.getAttribute('style')).toContain('var(--')
    expect(body.querySelector('span')?.getAttribute('style')).toContain('var(--primary-ink)')
    expect(body.innerHTML).not.toMatch(/#[0-9a-f]{3,6}\b/i)
  })
})
