import React, { useState, useEffect } from 'react';
import { apiClient } from '../data/apiClient';
import { ModernCommentThread } from './ModernCommentThread';

interface Task {
  id: string;
  evidence_tracking_id: string;
  evidence_id: string;
  task_type: string;
  title: string;
  description?: string;
  priority: string;
  due_date: string;
  status: string;
  assigned_user_id?: string;
  completed_date?: string;
  completion_notes?: string;
  dependencies?: string[];
  attachments?: any[];
  frequency?: string;
  collecting_system?: string;
  method_of_collection?: string;
  owner?: string;
  assigned_user?: {
    id: string;
    email: string;
    display_name: string;
  };
}

interface TasksPageProps {
  onNavigateToEvidence: (evidenceId: string) => void;
  organizationId: string;
}

export const TasksPage: React.FC<TasksPageProps> = ({ onNavigateToEvidence, organizationId }) => {
  const [view, setView] = useState<'my-tasks' | 'all-tasks'>('my-tasks');
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [taskTypeFilter, setTaskTypeFilter] = useState<string>('all');
  const [editingTask, setEditingTask] = useState<string | null>(null);
  const [editStatus, setEditStatus] = useState<string>('');
  const [editNotes, setEditNotes] = useState<string>('');
  const [expandedComments, setExpandedComments] = useState<string | null>(null);
  useEffect(() => {
    loadTasks();
  }, [view, statusFilter, taskTypeFilter]);

  const loadTasks = async () => {
    setLoading(true);
    try {
      let allTasks = [];

      if (view === 'my-tasks') {
        allTasks = await apiClient.get('/evidence-tasks');
      } else {
        // Get all tasks
        let url = '/evidence-tasks';
        if (statusFilter !== 'all') {
          url += `?status_filter=${statusFilter}`;
        }
        allTasks = await apiClient.get(url);
      }

      // Apply task type filter on client side
      if (taskTypeFilter !== 'all') {
        allTasks = allTasks.filter((t: Task) => t.task_type === taskTypeFilter);
      }

      setTasks(allTasks);
    } catch (error) {
      console.error('Failed to load tasks:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleEditTask = async (taskId: string) => {
    try {
      await apiClient.patch(`/evidence-tasks/${taskId}`, {
        status: editStatus,
        completion_notes: editNotes || null
      });
      setEditingTask(null);
      setEditStatus('');
      setEditNotes('');
      await loadTasks();
    } catch (error) {
      console.error('Failed to update task:', error);
      alert('Failed to update task');
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

  const startEdit = (task: Task) => {
    setEditingTask(task.id);
    setEditStatus(task.status);
    setEditNotes(task.completion_notes || '');
  };

  const cancelEdit = () => {
    setEditingTask(null);
    setEditStatus('');
    setEditNotes('');
  };

  const getDaysUntilDue = (dueDate: string) => {
    const today = new Date();
    const due = new Date(dueDate);
    const diffTime = due.getTime() - today.getTime();
    const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
    return diffDays;
  };

  const getDaysRemainingClass = (days: number, isCompleted: boolean): string => {
    if (isCompleted) return '';
    if (days < 0) return 'days-overdue';
    if (days <= 7) return 'days-warning';
    return 'days-ok';
  };

  const stats = {
    total: tasks.length,
    not_started: tasks.filter(t => t.status === 'not_started').length,
    in_progress: tasks.filter(t => t.status === 'in_progress').length,
    completed: tasks.filter(t => t.status === 'completed').length,
    overdue: tasks.filter(t => new Date(t.due_date) < new Date() && t.status !== 'completed').length
  };

  return (
    <div className="tasks-page">
      <div className="tasks-header">
        <h1>Evidence Collection Tasks</h1>
        <p className="tasks-subtitle">
          Manage evidence collection tasks and track compliance activities
        </p>
      </div>

      {/* View Toggle */}
      <div className="tasks-view-toggle">
        <button
          onClick={() => setView('my-tasks')}
          className={`tasks-toggle-btn ${view === 'my-tasks' ? 'active' : ''}`}
        >
          My Tasks
        </button>
        <button
          onClick={() => setView('all-tasks')}
          className={`tasks-toggle-btn ${view === 'all-tasks' ? 'active' : ''}`}
        >
          All Tasks
        </button>
      </div>

      {/* Stats */}
      <div className="tasks-stats-grid">
        <div className="tasks-stat-card">
          <div className="tasks-stat-value text-blue">{stats.total}</div>
          <div className="tasks-stat-label">Total Tasks</div>
        </div>
        <div className="tasks-stat-card">
          <div className="tasks-stat-value text-blue">{stats.not_started}</div>
          <div className="tasks-stat-label">Not Started</div>
        </div>
        <div className="tasks-stat-card">
          <div className="tasks-stat-value text-orange">{stats.in_progress}</div>
          <div className="tasks-stat-label">In Progress</div>
        </div>
        <div className="tasks-stat-card">
          <div className="tasks-stat-value text-red">{stats.overdue}</div>
          <div className="tasks-stat-label">Overdue</div>
        </div>
        <div className="tasks-stat-card">
          <div className="tasks-stat-value text-green">{stats.completed}</div>
          <div className="tasks-stat-label">Completed</div>
        </div>
      </div>

      {/* Filters */}
      <div className="tasks-filters">
        <div className="tasks-filter-group">
          <label>Status:</label>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="tasks-filter-select"
          >
            <option value="all">All Statuses</option>
            <option value="not_started">Not Started</option>
            <option value="in_progress">In Progress</option>
            <option value="completed">Completed</option>
          </select>
        </div>

        <div className="tasks-filter-group">
          <label>Task Type:</label>
          <select
            value={taskTypeFilter}
            onChange={(e) => setTaskTypeFilter(e.target.value)}
            className="tasks-filter-select"
          >
            <option value="all">All Types</option>
            <option value="feasibility">Feasibility</option>
            <option value="setup">Setup</option>
            <option value="collection">Collection</option>
            <option value="review">Review</option>
            <option value="documentation">Documentation</option>
            <option value="issue">Issue</option>
          </select>
        </div>
      </div>

      {/* Task List */}
      {loading ? (
        <div className="tasks-loading">Loading tasks...</div>
      ) : tasks.length === 0 ? (
        <div className="tasks-empty-state">
          <div className="tasks-empty-icon">📋</div>
          <h3>No Tasks Found</h3>
          <p>
            {statusFilter !== 'all' ? 'Try changing the filter' : 'Tasks will appear here when evidence collection is scheduled'}
          </p>
        </div>
      ) : (
        <div className="tasks-list-container">
          {tasks.map((task) => {
            const daysUntilDue = getDaysUntilDue(task.due_date);
            const isOverdue = daysUntilDue < 0 && task.status !== 'completed';
            const isEditing = editingTask === task.id;

            return (
              <div
                key={task.id}
                className={`task-item ${isOverdue ? 'task-overdue' : ''}`}
              >
                {isEditing ? (
                  /* Edit Mode */
                  <div>
                    <div className="task-edit-header">
                      <h3 className="task-edit-title">
                        Editing: {task.title || task.evidence_id}
                      </h3>
                    </div>

                    <div className="task-edit-grid">
                      <div>
                        <label className="task-edit-field">Status:</label>
                        <select
                          value={editStatus}
                          onChange={(e) => setEditStatus(e.target.value)}
                          className="task-edit-select"
                        >
                          <option value="not_started">Not Started</option>
                          <option value="in_progress">In Progress</option>
                          <option value="completed">Completed</option>
                        </select>
                      </div>
                    </div>

                    <div>
                      <label className="task-edit-field">Notes:</label>
                      <textarea
                        value={editNotes}
                        onChange={(e) => setEditNotes(e.target.value)}
                        placeholder="Add completion notes..."
                        className="task-edit-textarea"
                      />
                    </div>

                    <div className="task-edit-actions">
                      <button
                        onClick={() => handleEditTask(task.id)}
                        className="task-edit-save"
                      >
                        Save Changes
                      </button>
                      <button
                        onClick={cancelEdit}
                        className="task-edit-cancel"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  /* View Mode */
                  <div className="task-view-layout">
                    {/* Left: Task Details */}
                    <div className="task-view-content">
                      {/* Badges */}
                      <div className="task-badges-row">
                        <span className={`task-badge ${getTaskTypeClass(task.task_type)}`}>
                          {getTaskTypeLabel(task.task_type)}
                        </span>
                        <span className={`task-badge ${getPriorityClass(task.priority)}`}>
                          {task.priority}
                        </span>
                        <div className={`task-badge ${getStatusClass(task.status)}`}>
                          {task.status.replace('_', ' ')}
                        </div>
                        {isOverdue && (
                          <div className="task-badge status-overdue">
                            OVERDUE
                          </div>
                        )}
                      </div>

                      {/* Task Title */}
                      <h3 className="task-title">
                        {task.title || 'Untitled Task'}
                      </h3>

                      {/* Evidence Link */}
                      <div className="task-evidence-link">
                        <a onClick={() => onNavigateToEvidence(task.evidence_id)}>
                          Evidence: {task.evidence_id} →
                        </a>
                      </div>

                      {/* Description */}
                      {task.description && (
                        <div className="task-description-block">
                          {task.description}
                        </div>
                      )}

                      <div className="task-details-grid">
                        <div>
                          <strong>Due Date:</strong>{' '}
                          {new Date(task.due_date).toLocaleDateString('en-US', {
                            weekday: 'short',
                            year: 'numeric',
                            month: 'short',
                            day: 'numeric'
                          })}
                          {daysUntilDue >= 0 ? (
                            <span className={`task-days-remaining ${getDaysRemainingClass(daysUntilDue, task.status === 'completed')}`}>
                              ({daysUntilDue} days)
                            </span>
                          ) : (
                            <span className="task-days-remaining days-overdue">
                              ({Math.abs(daysUntilDue)} days overdue)
                            </span>
                          )}
                        </div>
                        {task.frequency && (
                          <div>
                            <strong>Frequency:</strong> {task.frequency}
                          </div>
                        )}
                        {task.owner && (
                          <div>
                            <strong>Owner Team:</strong> {task.owner}
                          </div>
                        )}
                        {view === 'all-tasks' && task.assigned_user && (
                          <div>
                            <strong>Assigned To:</strong>{' '}
                            {task.assigned_user.display_name || task.assigned_user.email}
                          </div>
                        )}
                        {task.collecting_system && (
                          <div>
                            <strong>System:</strong> {task.collecting_system}
                          </div>
                        )}
                        {task.method_of_collection && (
                          <div>
                            <strong>Method:</strong> {task.method_of_collection}
                          </div>
                        )}
                      </div>

                      {task.completion_notes && (
                        <div className="task-completion-notes">
                          <strong>Notes:</strong> {task.completion_notes}
                        </div>
                      )}
                    </div>

                    {/* Right: Actions */}
                    <div className="task-actions">
                      <button onClick={() => startEdit(task)} className="task-btn task-btn-primary">
                        ✏️ Edit Task
                      </button>
                      <button
                        onClick={() => setExpandedComments(expandedComments === task.id ? null : task.id)}
                        className="task-btn task-btn-outline"
                      >
                        💬 Comments
                      </button>
                      <button
                        onClick={() => onNavigateToEvidence(task.evidence_id)}
                        className="task-btn task-btn-secondary"
                      >
                        View Evidence →
                      </button>
                    </div>
                  </div>
                )}

                {/* Expanded Comments Section - Outside Edit/View Toggle */}
                {!isEditing && expandedComments === task.id && (
                  <div className="task-comments-section">
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
    </div>
  );
};

export default TasksPage;
