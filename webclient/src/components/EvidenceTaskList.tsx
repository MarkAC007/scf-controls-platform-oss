import React, { useState, useEffect } from 'react';
import { apiClient } from '../data/apiClient';
import { TaskCreationModal } from './TaskCreationModal';
import { TaskEditModal } from './TaskEditModal';
import { ModernCommentThread } from './ModernCommentThread';

interface Task {
  id: string;
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
}

export const EvidenceTaskList: React.FC<EvidenceTaskListProps> = ({
  evidenceTrackingId,
  evidenceId,
  organizationId,
  onTaskChange
}) => {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingTask, setEditingTask] = useState<Task | null>(null);
  const [expandedTaskId, setExpandedTaskId] = useState<string | null>(null);

  useEffect(() => {
    loadTasks();
  }, [evidenceTrackingId]);

  const loadTasks = async () => {
    try {
      // Filter tasks by evidence tracking ID
      const allTasks = await apiClient.get('/evidence-tasks');
      const evidenceTasks = allTasks.filter(
        (t: any) => t.evidence_tracking_id === evidenceTrackingId
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

  if (loading) {
    return <div className="evidence-task-loading">Loading tasks...</div>;
  }

  const openTasks = tasks.filter(t => t.status !== 'completed');
  const completedTasks = tasks.filter(t => t.status === 'completed');

  return (
    <div className="evidence-task-list">
      <div className="evidence-task-list-header">
        <h4>
          Evidence Tasks
          <span className="evidence-task-count">{openTasks.length}</span>
        </h4>
        <button
          className="btn-create-task"
          onClick={() => setShowCreateModal(true)}
        >
          + Create Task
        </button>
      </div>

      {tasks.length === 0 ? (
        <p className="evidence-task-empty">
          No tasks created yet. Tasks are auto-generated based on collection frequency.
        </p>
      ) : (
        <>
          {/* Open Tasks */}
          {openTasks.length > 0 && (
            <div style={{ marginBottom: '1.5rem' }}>
              <h5 className="evidence-task-section-title">Open Tasks</h5>
              {openTasks.map((task) => {
                const daysUntilDue = getDaysUntilDue(task.due_date);
                const isOverdue = daysUntilDue < 0;

                return (
                  <div
                    key={task.id}
                    className={`evidence-task-card ${isOverdue ? 'overdue' : ''}`}
                  >
                    {/* Task Header with Badges */}
                    <div className="evidence-task-badges">
                      <span className={`task-type-badge ${getTaskTypeClass(task.task_type)}`}>
                        {getTaskTypeLabel(task.task_type)}
                      </span>
                      <span className={`priority-badge ${getPriorityClass(task.priority)}`}>
                        {task.priority}
                      </span>
                      <span className={`status-badge ${getStatusClass(task.status)}`}>
                        {task.status.replace('_', ' ')}
                      </span>
                      {isOverdue && (
                        <span className="status-badge status-overdue">OVERDUE</span>
                      )}
                    </div>

                    {/* Task Title */}
                    <div className="evidence-task-title">
                      {task.title || 'Untitled Task'}
                    </div>

                    {/* Task Description */}
                    {task.description && (
                      <div className="evidence-task-description">
                        {task.description}
                      </div>
                    )}

                    {/* Task Details */}
                    <div className="evidence-task-details">
                      <div className="evidence-task-due">
                        <strong>Due:</strong> {new Date(task.due_date).toLocaleDateString('en-US', {
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
                      </div>
                      {task.assigned_user && (
                        <div style={{ color: 'var(--muted)' }}>
                          <strong>Assigned:</strong> {task.assigned_user.display_name || task.assigned_user.email}
                        </div>
                      )}
                    </div>

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
                  <div style={{ marginBottom: '0.25rem' }}>
                    <strong>Completed:</strong> {task.completed_date ? new Date(task.completed_date).toLocaleDateString() : 'N/A'}
                  </div>
                  {task.completion_notes && (
                    <div style={{ color: 'var(--muted)' }}>
                      <strong>Notes:</strong> {task.completion_notes}
                    </div>
                  )}
                </div>
              ))}
            </details>
          )}
        </>
      )}

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
