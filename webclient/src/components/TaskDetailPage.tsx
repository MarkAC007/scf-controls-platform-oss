/**
 * TaskDetailPage — full-width task detail per TaskDetail.html spec.
 *
 * Reachable from TasksPage row TITLE click (not the expansion chevron, which
 * still opens the inline edit panel as before).
 *
 * Layout (top to bottom):
 *   1. Breadcrumb "‹ Task Management / <id>" + "k of N in view" pager
 *   2. Header: mono id + type chip + status chip + priority chip + CTAs (Save / Mark completed)
 *   3. Task title (large heading)
 *   4. 3-card grid: ASSIGNMENT / SCHEDULE / LINKED RECORDS
 *   5. DESCRIPTION block
 *   6. ACTIVITY section: comment thread
 *
 * Edit semantics: identical to the expansion panel (PATCH /evidence-tasks/{id}).
 * Save changes patches current state; Mark completed patches status=completed.
 *
 * Activity: comment thread via ModernCommentThread. Status history would
 * require a dedicated endpoint not present client-side — deviation noted.
 *
 * Keyboard: ArrowLeft→prev, ArrowRight→next, Escape→back.
 * Suppressed when focus is in input/textarea/select/contentEditable.
 */
import { useState, useEffect, useMemo, useCallback } from 'react'
import { apiClient } from '../data/apiClient'
import { ModernCommentThread } from './ModernCommentThread'
import { frequencyLabel } from '../data/frequencyVocabulary'

// ─── Types ────────────────────────────────────────────────────────────────────

export interface TaskForDetail {
  id: string
  evidence_tracking_id: string
  evidence_id: string
  task_type: string
  title: string
  description?: string | null
  priority: string
  due_date: string
  status: string
  assigned_user_id?: string | null
  owning_team_id?: string | null
  completed_date?: string
  completion_notes?: string
  frequency?: string | null
  collecting_system?: string | null
  method_of_collection?: string | null
  owner?: string | null
  assigned_user?: {
    id: string
    email: string
    display_name: string
  } | null
}

export interface TaskDetailPageProps {
  organizationId: string
  taskId: string
  /** The visible/filtered tasks — used for pager. */
  visibleTasks: TaskForDetail[]
  onTaskItemChange: (id: string | null) => void
  onNavigateToEvidence: (evidenceId: string) => void
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

/** True when the keyboard event target should suppress pager shortcuts. */
function isSuppressed(e: KeyboardEvent): boolean {
  const t = e.target
  if (!t || !(t instanceof Element)) return false
  const tag = (t as HTMLElement).tagName?.toLowerCase()
  if (!tag) return false
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return true
  if ((t as HTMLElement).isContentEditable) return true
  return !!document.querySelector('[role="listbox"]')
}

const TASK_TYPE_LABELS: Record<string, string> = {
  feasibility: 'Feasibility',
  setup: 'Setup',
  collection: 'Collection',
  review: 'Review',
  documentation: 'Documentation',
  issue: 'Issue',
}

const STATUS_LABELS: Record<string, string> = {
  not_started: 'Not started',
  in_progress: 'In progress',
  completed: 'Completed',
}

const PRIORITY_LABELS: Record<string, string> = {
  low: 'Low priority',
  medium: 'Medium priority',
  high: 'High priority',
  critical: 'Critical priority',
}

function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return '—'
  try {
    return new Date(dateStr).toLocaleDateString('en-GB', {
      weekday: 'short', day: 'numeric', month: 'short', year: 'numeric',
    })
  } catch {
    return dateStr
  }
}

function getDaysUntilDue(dueDate: string): number {
  const today = new Date()
  const due = new Date(dueDate)
  return Math.ceil((due.getTime() - today.getTime()) / (1000 * 60 * 60 * 24))
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function TaskDetailPage({
  organizationId,
  taskId,
  visibleTasks,
  onTaskItemChange,
  onNavigateToEvidence,
}: TaskDetailPageProps) {
  const [task, setTask] = useState<TaskForDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editStatus, setEditStatus] = useState<string>('')
  const [editNotes, setEditNotes] = useState<string>('')
  const [saving, setSaving] = useState(false)

  // ── Pager ─────────────────────────────────────────────────────────────────

  const currentIndex = useMemo(
    () => visibleTasks.findIndex(t => t.id === taskId),
    [visibleTasks, taskId]
  )
  const total = visibleTasks.length
  const pagerText = currentIndex >= 0
    ? `${currentIndex + 1} of ${total} in view`
    : `— of ${total} in view`

  const prevDisabled = currentIndex <= 0
  const nextDisabled = currentIndex < 0 || currentIndex >= visibleTasks.length - 1

  const handleBack = useCallback(() => onTaskItemChange(null), [onTaskItemChange])

  const handlePrev = useCallback(() => {
    if (currentIndex > 0) {
      onTaskItemChange(visibleTasks[currentIndex - 1].id)
    }
  }, [currentIndex, visibleTasks, onTaskItemChange])

  const handleNext = useCallback(() => {
    if (currentIndex >= 0 && currentIndex < visibleTasks.length - 1) {
      onTaskItemChange(visibleTasks[currentIndex + 1].id)
    }
  }, [currentIndex, visibleTasks, onTaskItemChange])

  // ── Keyboard shortcuts ────────────────────────────────────────────────────

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (isSuppressed(e)) return
      if (e.key === 'ArrowRight') handleNext()
      else if (e.key === 'ArrowLeft') handlePrev()
      else if (e.key === 'Escape') handleBack()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [handleNext, handlePrev, handleBack])

  // ── Data loading ──────────────────────────────────────────────────────────

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    const load = async () => {
      try {
        const data = await apiClient.get(`/evidence-tasks/${taskId}`)
        if (cancelled) return
        setTask(data as TaskForDetail)
        setEditStatus((data as TaskForDetail).status)
        setEditNotes((data as TaskForDetail).completion_notes || '')
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load task')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    return () => { cancelled = true }
  }, [taskId])

  // ── Save handlers ─────────────────────────────────────────────────────────

  const handleSave = async () => {
    if (!task) return
    setSaving(true)
    try {
      await apiClient.patch(`/evidence-tasks/${task.id}`, {
        status: editStatus,
        completion_notes: editNotes || null,
      })
    } catch (err) {
      console.error('Failed to save task:', err)
    } finally {
      setSaving(false)
    }
  }

  const handleMarkCompleted = async () => {
    if (!task) return
    setSaving(true)
    try {
      await apiClient.patch(`/evidence-tasks/${task.id}`, {
        status: 'completed',
        completion_notes: editNotes || null,
      })
      setEditStatus('completed')
    } catch (err) {
      console.error('Failed to mark task completed:', err)
    } finally {
      setSaving(false)
    }
  }

  // ── Render states ─────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="task-detail-page">
        <div className="task-detail-page-loading">Loading task…</div>
      </div>
    )
  }

  if (error || !task) {
    return (
      <div className="task-detail-page">
        <button
          onClick={handleBack}
          className="task-detail-breadcrumb-back"
          type="button"
          aria-label="Task Management"
        >
          ‹ Task Management
        </button>
        <p className="task-detail-page-error">{error || 'Task not found'}</p>
      </div>
    )
  }

  const daysUntilDue = getDaysUntilDue(task.due_date)
  const isCompleted = task.status === 'completed'
  const isOverdue = daysUntilDue < 0 && !isCompleted

  return (
    <div className="task-detail-page">

      {/* ── Breadcrumb + pager ─────────────────────────────────────────── */}
      <div className="task-detail-breadcrumb-strip">
        <button
          onClick={handleBack}
          className="task-detail-breadcrumb-back"
          type="button"
          aria-label="Task Management"
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
            <path d="M9 2L4 7l5 5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
          </svg>
          Task Management
        </button>
        <span className="task-detail-breadcrumb-sep">/</span>
        <span className="task-detail-breadcrumb-id">{task.id}</span>

        <div className="task-detail-pager">
          <span className="task-detail-pager-count">{pagerText}</span>
          <div className="task-detail-pager-buttons">
            <button
              onClick={handlePrev}
              disabled={prevDisabled}
              aria-label="previous"
              className="task-detail-pager-btn"
              type="button"
            >
              <svg width="12" height="12" viewBox="0 0 14 14" fill="none" aria-hidden="true">
                <path d="M9 2L4 7l5 5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
            <button
              onClick={handleNext}
              disabled={nextDisabled}
              aria-label="next"
              className="task-detail-pager-btn"
              type="button"
            >
              <svg width="12" height="12" viewBox="0 0 14 14" fill="none" aria-hidden="true">
                <path d="M5 2l5 5-5 5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
          </div>
        </div>
      </div>

      {/* ── Page body ──────────────────────────────────────────────────── */}
      <div className="task-detail-page-body">

        {/* ── Header ───────────────────────────────────────────────────── */}
        <div className="task-detail-page-header">
          <div className="task-detail-page-chips-row">
            <div className="task-detail-page-tick-bar" aria-hidden="true" />
            <span className="task-detail-page-id">{task.id}</span>
            <span className="task-detail-page-chip">
              {TASK_TYPE_LABELS[task.task_type] || task.task_type}
            </span>
            <span className={`task-detail-page-chip task-detail-status-${task.status}`}>
              {STATUS_LABELS[task.status] || task.status}
            </span>
            <span className={`task-detail-page-chip task-detail-priority-${task.priority}`}>
              {PRIORITY_LABELS[task.priority] || `${task.priority} priority`}
            </span>
            <div className="task-detail-page-ctas">
              <button
                onClick={handleSave}
                disabled={saving}
                className="task-detail-save-btn"
                type="button"
                aria-label="Save changes"
              >
                Save changes
              </button>
              <button
                onClick={handleMarkCompleted}
                disabled={saving || isCompleted}
                className="task-detail-complete-btn"
                type="button"
                aria-label="Mark completed"
              >
                Mark completed
              </button>
            </div>
          </div>

          <h1 className="task-detail-page-title">
            {task.title || 'Untitled Task'}
          </h1>
        </div>

        {/* ── 3-card grid ──────────────────────────────────────────────── */}
        <div className="task-detail-page-cards">

          {/* ASSIGNMENT card */}
          <div className="task-detail-page-card">
            <div className="task-detail-page-card-label">ASSIGNMENT</div>
            {task.assigned_user ? (
              <div className="task-detail-assignee">
                <div className="task-detail-assignee-avatar" aria-hidden="true">
                  {task.assigned_user.display_name.charAt(0).toUpperCase()}
                </div>
                <span className="task-detail-assignee-name">
                  {task.assigned_user.display_name || task.assigned_user.email}
                </span>
              </div>
            ) : (
              <span className="task-detail-unassigned">Unassigned</span>
            )}
            {task.owner && (
              <p className="task-detail-card-note">
                Owning team — <span className="task-detail-card-note-em">{task.owner}</span>
              </p>
            )}
            {!task.owning_team_id && (
              <p className="task-detail-card-note">Inherits from evidence item</p>
            )}
          </div>

          {/* SCHEDULE card */}
          <div className="task-detail-page-card">
            <div className="task-detail-page-card-label">SCHEDULE</div>
            <div className="task-detail-due-row">
              <span className="task-detail-due-date">
                Due {formatDate(task.due_date)}
              </span>
              {!isCompleted && isOverdue && (
                <span className="task-detail-due-badge task-detail-due-badge--overdue">
                  {Math.abs(daysUntilDue)} days overdue
                </span>
              )}
              {!isCompleted && !isOverdue && daysUntilDue <= 7 && (
                <span className="task-detail-due-badge task-detail-due-badge--warning">
                  {daysUntilDue} days
                </span>
              )}
            </div>
            <p className="task-detail-card-note">
              {task.frequency && `Frequency — ${frequencyLabel(task.frequency)}`}
              {task.frequency && task.method_of_collection && ' · '}
              {task.method_of_collection && `Method — ${task.method_of_collection}`}
            </p>
          </div>

          {/* LINKED RECORDS card */}
          <div className="task-detail-page-card">
            <div className="task-detail-page-card-label">LINKED RECORDS</div>
            <div className="task-detail-linked-chips">
              {task.evidence_id && (
                <span className="task-detail-linked-chip">{task.evidence_id}</span>
              )}
            </div>
            {task.evidence_id && (
              <button
                onClick={() => onNavigateToEvidence(task.evidence_id)}
                className="task-detail-view-evidence-btn"
                type="button"
                aria-label="View evidence item"
              >
                View evidence item
              </button>
            )}
          </div>
        </div>

        {/* ── Description ──────────────────────────────────────────────── */}
        {task.description && (
          <div className="task-detail-description-block">
            <div className="task-detail-page-card-label">DESCRIPTION</div>
            <p className="task-detail-description-text">{task.description}</p>
          </div>
        )}

        {/* ── Activity / comments ───────────────────────────────────────── */}
        <div className="task-detail-activity-section">
          <div className="task-detail-page-card-label">ACTIVITY</div>
          <p className="task-detail-activity-deviation">
            Note: status history requires a dedicated endpoint not yet available — comments only shown below.
          </p>
          <ModernCommentThread
            commentableType="task"
            commentableId={task.id}
            organizationId={organizationId}
          />
        </div>

      </div>
    </div>
  )
}
