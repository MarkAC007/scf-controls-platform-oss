import React, { useState, useEffect, useMemo } from 'react';
import { apiClient } from '../data/apiClient';
import { TaskCreationModal } from './TaskCreationModal';
import { TaskEditModal } from './TaskEditModal';
import { ModernCommentThread } from './ModernCommentThread';
import { useOrgMemberTypes } from '../hooks/useOrgMemberTypes';
import { useTaskTeamOwnership } from '../hooks/useTaskTeamOwnership';
import TaskOwningTeamBadge from './TaskOwningTeamBadge';

interface Task {
  id: string;
  /** Present on the API payload; the edit modal needs it to resolve inheritance. */
  evidence_tracking_id?: string;
  /** Null or absent means the task inherits this evidence item's team (#822 §6). */
  owning_team_id?: string | null;
  due_date: string;
  status: string;
  task_type: string;
  title: string;
  description?: string;
  priority: string;
  completed_date?: string;
  completion_notes?: string;
  dependencies?: string[];
  attachments?: any[];
  assigned_user?: {
    id: string;
    email: string;
    display_name: string;
  };
}

interface EvidenceTaskListProps {
  evidenceTrackingId: string;
  evidenceId: string;
  organizationId: string;
  onTaskChange?: () => void;
  /**
   * No tracking row saved yet, so there is nothing to hang a task off.
   *
   * The card still renders. Hiding it entirely was the old behaviour and it
   * left no trace on the page that tasks exist at all, so the feature was
   * invisible to exactly the people who had not yet done the one thing that
   * turns it on.
   */
  disabled?: boolean;
}

export const EvidenceTaskList: React.FC<EvidenceTaskListProps> = ({
  evidenceTrackingId,
  evidenceId,
  organizationId,
  onTaskChange,
  disabled = false
}) => {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(!disabled);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingTask, setEditingTask] = useState<Task | null>(null);
  /** The one row showing its detail. Single-valued: opening one closes the last. */
  const [openTaskId, setOpenTaskId] = useState<string | null>(null);
  const [expandedTaskId, setExpandedTaskId] = useState<string | null>(null);

  /* Owning team (#822 phase 4). Every task here belongs to one evidence item,
     so the parent is known without asking the payload for it — but the
     payload's own value wins where it has one, so a task that was moved keeps
     resolving against the item it actually belongs to. */
  const ownableTasks = useMemo(
    () =>
      tasks.map(task => ({
        id: task.id,
        evidence_tracking_id: task.evidence_tracking_id ?? evidenceTrackingId,
        owning_team_id: task.owning_team_id ?? null,
      })),
    [tasks, evidenceTrackingId]
  );
  const { ownershipFor, resolved: ownershipResolved } = useTaskTeamOwnership(
    organizationId,
    ownableTasks
  );
  const { memberTypeOf } = useOrgMemberTypes(organizationId);

  const ownershipOf = (task: Task) =>
    ownershipFor({
      id: task.id,
      evidence_tracking_id: task.evidence_tracking_id ?? evidenceTrackingId,
      owning_team_id: task.owning_team_id ?? null,
    });

  useEffect(() => {
    if (disabled || !evidenceTrackingId) return;
    loadTasks();
  }, [evidenceTrackingId, disabled]);

  const loadTasks = async () => {
    try {
      // Filtered in SQL, not here: this list is unpaginated, so fetching every
      // task in the organisation to display one row's handful grew with the
      // whole tenant (#788).
      const evidenceTasks = await apiClient.get(
        `/evidence-tasks?evidence_tracking_id=${encodeURIComponent(evidenceTrackingId)}`
      );
      setTasks(evidenceTasks);
    } catch (error) {
      console.error('Failed to load tasks:', error);
    } finally {
      setLoading(false);
    }
  };

  const getTaskTypeClass = (taskType: string): string => {
    const typeClasses: Record<string, string> = {
      feasibility: 'task-type-feasibility',
      setup: 'task-type-setup',
      collection: 'task-type-collection',
      review: 'task-type-review',
      documentation: 'task-type-documentation',
      issue: 'task-type-issue'
    };
    return typeClasses[taskType] || '';
  };

  const getTaskTypeLabel = (taskType: string): string => {
    const labels: Record<string, string> = {
      feasibility: 'Feasibility',
      setup: 'Setup',
      collection: 'Collection',
      review: 'Review',
      documentation: 'Documentation',
      issue: 'Issue'
    };
    return labels[taskType] || taskType;
  };

  const getPriorityClass = (priority: string): string => {
    return `priority-${priority}`;
  };

  const getStatusClass = (status: string): string => {
    return `status-${status}`;
  };

  const getDaysUntilDue = (dueDate: string) => {
    const today = new Date();
    const due = new Date(dueDate);
    const diffTime = due.getTime() - today.getTime();
    const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
    return diffDays;
  };

  const openTasks = tasks.filter(t => t.status !== 'completed');
  const completedTasks = tasks.filter(t => t.status === 'completed');

  return (
    <div className={`detail-section-container evidence-task-list ${disabled ? 'evidence-task-list-disabled' : ''}`}>
      <div className="container-header">
        <span className="container-icon">{'✅'}</span>
        <span className="container-title">Evidence Tasks</span>
        <span className="container-count">{openTasks.length}</span>
        <button
          className="btn-create-task"
          onClick={() => setShowCreateModal(true)}
          disabled={disabled}
        >
          + New Task
        </button>
      </div>

      <div className="container-content">
        {disabled ? (
          <p className="evidence-task-empty">
            Save this evidence tracking to enable tasks.
          </p>
        ) : loading ? (
          <div className="evidence-task-loading">Loading tasks...</div>
        ) : tasks.length === 0 ? (
          <p className="evidence-task-empty">
            No tasks created yet. Tasks are auto-generated based on collection frequency.
          </p>
        ) : (
          <>
            {/* Open Tasks */}
            {openTasks.length > 0 && (
              <div className="evidence-task-open-group">
                <h5 className="evidence-task-section-title">Open Tasks</h5>
                {openTasks.map((task) => {
                  const daysUntilDue = getDaysUntilDue(task.due_date);
                  const isOverdue = daysUntilDue < 0;
                  const isOpen = openTaskId === task.id;

                  return (
                    <div
                      key={task.id}
                      className={`evidence-task-row-group ${isOverdue ? 'overdue' : ''} ${isOpen ? 'open' : ''}`}
                    >
                      {/* Collapsed row: everything needed to triage without opening it. */}
                      <div className="evidence-task-row">
                        <button
                          type="button"
                          className="evidence-task-row-toggle"
                          aria-expanded={isOpen}
                          onClick={() => setOpenTaskId(isOpen ? null : task.id)}
                        >
                          <span className="evidence-task-row-chevron" aria-hidden="true">
                            {isOpen ? '▾' : '▸'}
                          </span>
                          <span className={`task-type-badge ${getTaskTypeClass(task.task_type)}`}>
                            {getTaskTypeLabel(task.task_type)}
                          </span>
                          <span className="evidence-task-row-title">
                            {task.title || 'Untitled Task'}
                          </span>
                          <span className={`status-badge ${getStatusClass(task.status)}`}>
                            {task.status.replace('_', ' ')}
                          </span>
                          {isOverdue && (
                            <span className="status-badge status-overdue">OVERDUE</span>
                          )}
                          <span className="evidence-task-row-due">
                            {new Date(task.due_date).toLocaleDateString('en-US', {
                              month: 'short',
                              day: 'numeric',
                              year: 'numeric'
                            })}
                            {isOverdue ? (
                              <span className="days-overdue">
                                ({Math.abs(daysUntilDue)} days overdue)
                              </span>
                            ) : (
                              <span className={daysUntilDue <= 7 ? 'days-warning' : 'days-ok'}>
                                ({daysUntilDue} days)
                              </span>
                            )}
                          </span>
                          <span className="evidence-task-row-assignee">
                            {task.assigned_user
                              ? task.assigned_user.display_name || task.assigned_user.email
                              : 'Unassigned'}
                          </span>
                        </button>

                        {/* Stays on the collapsed row rather than moving into the
                            detail. An unassigned task is not an unowned one — its
                            evidence item's accountable team has it — and that only
                            stops "nobody is on this" being read off the row if it is
                            legible without opening the row first (#822). */}
                        <TaskOwningTeamBadge
                          ownership={ownershipOf(task)}
                          memberType={memberTypeOf(ownershipOf(task).team?.person_user_id)}
                          resolved={ownershipResolved}
                        />
                      </div>

                      {isOpen && (
                        <div className="evidence-task-row-body">
                          <div className="evidence-task-badges">
                            <span className={`priority-badge ${getPriorityClass(task.priority)}`}>
                              {task.priority}
                            </span>
                          </div>

                          {task.description && (
                            <div className="evidence-task-description">
                              {task.description}
                            </div>
                          )}

                          {/* Action Buttons */}
                          <div className="evidence-task-actions">
                            <button
                              className="btn-task-edit"
                              onClick={() => setEditingTask(task)}
                            >
                              ✏️ Edit Task
                            </button>
                            <button
                              className="btn-task-comments"
                              onClick={() => setExpandedTaskId(expandedTaskId === task.id ? null : task.id)}
                            >
                              💬 Comments ({expandedTaskId === task.id ? 'Hide' : 'Show'})
                            </button>
                          </div>

                          {/* Expanded Comments Section */}
                          {expandedTaskId === task.id && (
                            <div className="evidence-task-comments-section">
                              <ModernCommentThread
                                commentableType="task"
                                commentableId={task.id}
                                organizationId={organizationId}
                              />
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {/* Completed Tasks */}
            {completedTasks.length > 0 && (
              <details className="evidence-completed-tasks">
                <summary>Completed Tasks ({completedTasks.length})</summary>
                {completedTasks.map((task) => (
                  <div key={task.id} className="evidence-completed-task-card">
                    <div className="evidence-completed-task-line">
                      <strong>Completed:</strong> {task.completed_date ? new Date(task.completed_date).toLocaleDateString() : 'N/A'}
                    </div>
                    {task.completion_notes && (
                      <div className="evidence-completed-task-notes">
                        <strong>Notes:</strong> {task.completion_notes}
                      </div>
                    )}
                  </div>
                ))}
              </details>
            )}
          </>
        )}
      </div>

      {/* Task Creation Modal */}
      {showCreateModal && (
        <TaskCreationModal
          evidenceTrackingId={evidenceTrackingId}
          evidenceId={evidenceId}
          organizationId={organizationId}
          onClose={() => setShowCreateModal(false)}
          onTaskCreated={() => {
            setShowCreateModal(false);
            loadTasks();
            onTaskChange?.();
          }}
        />
      )}

      {/* Task Edit Modal */}
      {editingTask && (
        <TaskEditModal
          task={editingTask}
          organizationId={organizationId}
          onClose={() => setEditingTask(null)}
          onTaskUpdated={() => {
            setEditingTask(null);
            loadTasks();
            onTaskChange?.();
          }}
        />
      )}
    </div>
  );
};
