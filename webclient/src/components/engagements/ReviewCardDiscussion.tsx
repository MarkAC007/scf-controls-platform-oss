import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createReviewComment,
  deleteReviewComment,
  getReviewCommentCounts,
  getReviewCommentHistory,
  listReviewComments,
  reviewKeys,
  updateReviewComment,
  type CommentVisibility,
  type ReviewComment,
  type ReviewPeople,
} from '../../data/engagementReviewApi'
import { cancelOnEscape, errorMessage, fmtDateTime } from './ReviewCardShared'

/**
 * The discussion on one evidence card: a Shared thread (auditor and
 * organisation) and, for org members only, an Internal thread.
 *
 * The Internal tab exists only when the counts response carries an `internal`
 * key. An auditor's counts have no such key, so an auditor sees no Internal
 * tab, no count and no hint, and the internal list is never requested.
 */

interface DiscussionProps {
  organizationId: string
  engagementId: string
  evidenceId: string
  people: ReviewPeople | undefined
}

interface MentionOption { user_id: string; name: string }

const TAB_HINT: Record<CommentVisibility, string> = {
  shared: 'Shared with the auditor and the organisation',
  internal: 'Organisation only — not visible to the auditor',
}

function mentionOptions(people: ReviewPeople | undefined, visibility: CommentVisibility): MentionOption[] {
  if (!people) return []
  const list: MentionOption[] = people.members.map(m => ({ user_id: m.user_id, name: m.name }))
  if (visibility === 'shared') {
    for (const a of people.auditors) list.push({ user_id: a.user_id, name: a.name })
  }
  const seen = new Set<string>()
  return list.filter(p => (seen.has(p.user_id) ? false : (seen.add(p.user_id), true)))
}

export default function ReviewCardDiscussion({ organizationId, engagementId, evidenceId, people }: DiscussionProps) {
  const [tab, setTab] = useState<CommentVisibility>('shared')

  const counts = useQuery({
    queryKey: reviewKeys.commentCounts(engagementId, evidenceId),
    queryFn: () => getReviewCommentCounts(organizationId, engagementId, evidenceId),
  })
  const hasInternal = typeof counts.data?.internal === 'number'
  const active: CommentVisibility = tab === 'internal' && hasInternal ? 'internal' : 'shared'

  const list = useQuery({
    queryKey: reviewKeys.comments(engagementId, evidenceId, active),
    queryFn: () => listReviewComments(organizationId, engagementId, evidenceId, active),
    // Never ask for the internal list unless the server said this caller may see it.
    enabled: active === 'shared' || hasInternal,
  })

  const tabs: CommentVisibility[] = hasInternal ? ['shared', 'internal'] : ['shared']

  const onTabKey = (e: React.KeyboardEvent) => {
    if (tabs.length < 2) return
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault()
      setTab(active === 'shared' ? 'internal' : 'shared')
    }
  }

  return (
    <section className="erp-section" aria-labelledby="erp-discussion-h">
      <h3 className="erp-h3" id="erp-discussion-h">Discussion</h3>
      {counts.isError && (
        <p className="erp-error" role="alert">{errorMessage(counts.error, 'Comment counts could not be loaded.')}</p>
      )}
      <div className="erp-tabs" role="tablist" aria-label="Discussion thread" onKeyDown={onTabKey}>
        {tabs.map(v => {
          const count = v === 'shared' ? counts.data?.shared : counts.data?.internal
          return (
            <button
              key={v}
              type="button"
              role="tab"
              id={`erp-tab-${v}`}
              aria-selected={active === v}
              aria-controls={`erp-tabpanel-${v}`}
              tabIndex={active === v ? 0 : -1}
              className={`erp-tab erp-tab-${v}${active === v ? ' is-active' : ''}`}
              onClick={() => setTab(v)}
            >
              <span>{v === 'shared' ? 'Shared' : 'Internal'}</span>
              {typeof count === 'number' && <span className="erp-tab-count">{count}</span>}
            </button>
          )
        })}
      </div>

      <div
        id={`erp-tabpanel-${active}`}
        role="tabpanel"
        aria-labelledby={`erp-tab-${active}`}
        className={`erp-thread erp-thread-${active}`}
      >
        <p className={`erp-thread-hint erp-thread-hint-${active}`}>{TAB_HINT[active]}</p>

        {list.isPending && list.fetchStatus !== 'idle' && <p className="erp-muted">Loading comments…</p>}
        {list.isError && (
          <p className="erp-error" role="alert">{errorMessage(list.error, 'Comments could not be loaded.')}</p>
        )}
        {list.data && (
          <Thread
            comments={list.data}
            organizationId={organizationId}
            engagementId={engagementId}
            evidenceId={evidenceId}
            visibility={active}
            mentions={mentionOptions(people, active)}
          />
        )}

        <Composer
          key={active}
          organizationId={organizationId}
          engagementId={engagementId}
          evidenceId={evidenceId}
          visibility={active}
          mentions={mentionOptions(people, active)}
        />
      </div>
    </section>
  )
}

// ---------------------------------------------------------------------------

interface ThreadContext {
  organizationId: string
  engagementId: string
  evidenceId: string
  visibility: CommentVisibility
  mentions: MentionOption[]
}

function Thread({ comments, ...ctx }: ThreadContext & { comments: ReviewComment[] }) {
  const children = useMemo(() => {
    const map = new Map<string | null, ReviewComment[]>()
    const ids = new Set(comments.map(c => c.id))
    for (const c of comments) {
      // A reply whose parent is not in this list is shown at the top level.
      const parent = c.parent_comment_id && ids.has(c.parent_comment_id) ? c.parent_comment_id : null
      const arr = map.get(parent) ?? []
      arr.push(c)
      map.set(parent, arr)
    }
    return map
  }, [comments])

  const roots = children.get(null) ?? []
  if (roots.length === 0) {
    return <p className="erp-muted">No comments yet.</p>
  }
  return (
    <ul className="erp-comments">
      {roots.map(c => <CommentItem key={c.id} comment={c} childrenOf={children} {...ctx} />)}
    </ul>
  )
}

function CommentItem({
  comment, childrenOf, ...ctx
}: ThreadContext & { comment: ReviewComment; childrenOf: Map<string | null, ReviewComment[]> }) {
  const { organizationId, engagementId, evidenceId } = ctx
  const queryClient = useQueryClient()
  const [mode, setMode] = useState<'view' | 'edit' | 'reply' | 'confirm-delete'>('view')
  const [draft, setDraft] = useState(comment.content)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [showHistory, setShowHistory] = useState(false)

  const history = useQuery({
    queryKey: [...reviewKeys.comments(engagementId, evidenceId, ctx.visibility), 'history', comment.id],
    queryFn: () => getReviewCommentHistory(organizationId, engagementId, evidenceId, comment.id),
    enabled: showHistory,
  })

  const refresh = () => queryClient.invalidateQueries({ queryKey: reviewKeys.all(engagementId) })

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!draft.trim()) {
      setError('A comment cannot be empty.')
      return
    }
    setBusy(true); setError(null)
    try {
      await updateReviewComment(organizationId, engagementId, evidenceId, comment.id, draft.trim())
      setMode('view')
      await refresh()
    } catch (err) {
      setError(errorMessage(err, 'The comment could not be saved.'))
    } finally { setBusy(false) }
  }

  const remove = async () => {
    setBusy(true); setError(null)
    try {
      await deleteReviewComment(organizationId, engagementId, evidenceId, comment.id)
      setMode('view')
      await refresh()
    } catch (err) {
      setError(errorMessage(err, 'The comment could not be deleted.'))
    } finally { setBusy(false) }
  }

  const replies = childrenOf.get(comment.id) ?? []
  const author = comment.author.name ?? 'Unknown user'

  return (
    <li className={`erp-comment${comment.is_deleted ? ' is-deleted' : ''}`}>
      <div className="erp-comment-head">
        <span className="erp-comment-author">{comment.is_deleted ? 'Deleted comment' : author}</span>
        {comment.created_at && <span className="erp-comment-when">{fmtDateTime(comment.created_at)}</span>}
        {comment.is_edited && !comment.is_deleted && (
          <button
            type="button"
            className="erp-edited"
            aria-expanded={showHistory}
            onClick={() => setShowHistory(v => !v)}
            title="Show previous versions"
          >
            edited
          </button>
        )}
      </div>

      {comment.is_deleted ? (
        <p className="erp-comment-body erp-muted">This comment was deleted.</p>
      ) : mode === 'edit' ? (
        <form className="erp-inline-form" onSubmit={save} noValidate onKeyDown={cancelOnEscape(() => setMode('view'))}>
          <label className="erp-sr-only" htmlFor={`erp-edit-${comment.id}`}>Edit comment</label>
          <textarea
            id={`erp-edit-${comment.id}`}
            className="erp-textarea"
            rows={3}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            autoFocus
          />
          <div className="erp-form-actions">
            <button type="submit" className="erp-btn erp-btn-primary" disabled={busy}>Save</button>
            <button type="button" className="erp-btn" onClick={() => { setMode('view'); setDraft(comment.content) }}>Cancel</button>
          </div>
        </form>
      ) : (
        <p className="erp-comment-body">{comment.content}</p>
      )}

      {!comment.is_deleted && comment.mentions.length > 0 && (
        <p className="erp-comment-mentions">
          Mentions: {comment.mentions.map(m => m.name ?? 'Unknown user').join(', ')}
        </p>
      )}

      {showHistory && (
        <div className="erp-history" aria-label="Previous versions">
          {history.isPending && <p className="erp-muted">Loading previous versions…</p>}
          {history.isError && <p className="erp-error" role="alert">{errorMessage(history.error, 'Previous versions could not be loaded.')}</p>}
          {history.data && history.data.length === 0 && <p className="erp-muted">No previous versions recorded.</p>}
          {history.data && history.data.length > 0 && (
            <ol className="erp-history-list">
              {history.data.map(h => (
                <li key={h.id}>
                  <span className="erp-comment-when">
                    {fmtDateTime(h.edited_at) ?? 'Unknown time'}{h.edited_by.name ? ` · ${h.edited_by.name}` : ''}
                  </span>
                  <p className="erp-comment-body">{h.old_content}</p>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}

      {!comment.is_deleted && mode === 'view' && (
        <div className="erp-comment-actions">
          <button type="button" className="erp-btn-link" onClick={() => setMode('reply')}>Reply</button>
          {comment.can_edit && (
            <button type="button" className="erp-btn-link" onClick={() => { setDraft(comment.content); setMode('edit') }}>Edit</button>
          )}
          {comment.can_delete && (
            <button type="button" className="erp-btn-link erp-btn-danger" onClick={() => setMode('confirm-delete')}>Delete</button>
          )}
        </div>
      )}

      {mode === 'confirm-delete' && (
        <div className="erp-confirm" role="group" aria-label="Confirm delete">
          <span>Delete this comment? It stays in the thread, marked deleted.</span>
          <button type="button" className="erp-btn erp-btn-danger-solid" onClick={() => void remove()} disabled={busy}>Delete</button>
          <button type="button" className="erp-btn" onClick={() => setMode('view')}>Keep</button>
        </div>
      )}

      {error && <p className="erp-error" role="alert">{error}</p>}

      {mode === 'reply' && (
        <Composer {...ctx} parentId={comment.id} onDone={() => setMode('view')} />
      )}

      {replies.length > 0 && (
        <ul className="erp-comments erp-replies">
          {replies.map(r => <CommentItem key={r.id} comment={r} childrenOf={childrenOf} {...ctx} />)}
        </ul>
      )}
    </li>
  )
}

// ---------------------------------------------------------------------------

function Composer({
  organizationId, engagementId, evidenceId, visibility, mentions, parentId, onDone,
}: ThreadContext & { parentId?: string; onDone?: () => void }) {
  const queryClient = useQueryClient()
  const [content, setContent] = useState('')
  const [picked, setPicked] = useState<MentionOption[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const idBase = `erp-compose-${visibility}-${parentId ?? 'root'}`

  const addMention = (userId: string) => {
    const person = mentions.find(m => m.user_id === userId)
    if (!person || picked.some(p => p.user_id === userId)) return
    setPicked(prev => [...prev, person])
    setContent(prev => `${prev}${prev && !prev.endsWith(' ') ? ' ' : ''}@${person.name} `)
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!content.trim()) {
      setError('Write something first.')
      return
    }
    setBusy(true); setError(null)
    try {
      await createReviewComment(organizationId, engagementId, evidenceId, {
        content: content.trim(),
        visibility,
        ...(parentId ? { parent_comment_id: parentId } : {}),
        ...(picked.length ? { mentions: picked.map(p => p.user_id) } : {}),
      })
      setContent('')
      setPicked([])
      onDone?.()
      await queryClient.invalidateQueries({ queryKey: reviewKeys.all(engagementId) })
    } catch (err) {
      setError(errorMessage(err, 'The comment could not be posted.'))
    } finally { setBusy(false) }
  }

  const target = visibility === 'internal'
    ? 'Internal note — organisation only, not visible to the auditor'
    : 'Shared comment — the auditor and the organisation will see it'

  return (
    <form
      className={`erp-composer erp-composer-${visibility}`}
      onSubmit={submit}
      noValidate
      onKeyDown={onDone ? cancelOnEscape(onDone) : undefined}
    >
      <label className="erp-label" htmlFor={`${idBase}-text`}>
        {parentId ? 'Reply' : 'New comment'} <span className={`erp-target erp-target-${visibility}`}>{target}</span>
      </label>
      <textarea
        id={`${idBase}-text`}
        className="erp-textarea"
        rows={parentId ? 2 : 3}
        value={content}
        onChange={e => setContent(e.target.value)}
        autoFocus={Boolean(parentId)}
      />
      <div className="erp-composer-row">
        {mentions.length > 0 && (
          <>
            <label className="erp-sr-only" htmlFor={`${idBase}-mention`}>Mention someone</label>
            <select
              id={`${idBase}-mention`}
              className="erp-select"
              value=""
              onChange={e => { if (e.target.value) addMention(e.target.value) }}
            >
              <option value="">@ Mention…</option>
              {mentions.map(m => (
                <option key={m.user_id} value={m.user_id}>{m.name}</option>
              ))}
            </select>
          </>
        )}
        {picked.map(p => (
          <span key={p.user_id} className="erp-mention-chip">
            @{p.name}
            <button
              type="button"
              className="erp-chip-remove"
              aria-label={`Remove mention of ${p.name}`}
              onClick={() => setPicked(prev => prev.filter(x => x.user_id !== p.user_id))}
            >×</button>
          </span>
        ))}
        <span className="erp-spacer" />
        {onDone && <button type="button" className="erp-btn" onClick={onDone}>Cancel</button>}
        <button type="submit" className="erp-btn erp-btn-primary" disabled={busy}>
          {visibility === 'internal' ? 'Add internal note' : parentId ? 'Post reply' : 'Post comment'}
        </button>
      </div>
      {error && <p className="erp-error" role="alert">{error}</p>}
    </form>
  )
}
