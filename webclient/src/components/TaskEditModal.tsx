import React, { useState } from 'react';
import { apiClient } from '../data/apiClient';
import TaskOwningTeamField from './TaskOwningTeamField';
import { useModalDismiss } from '../hooks/useModalDismiss';

interface TaskEditModalProps {
  task: any;
  organizationId: string;
  onClose: () => void;
  onTaskUpdated: () => void;
}

const TASK_TYPES = [
  { value: 'feasibility', label: 'Feasibility Check' },
  { value: 'setup', label: 'Setup/Configuration' },
  { value: 'collection', label: 'Collection' },
  { value: 'review', label: 'Review/Validation' },
  { value: 'documentation', label: 'Documentation' },
  { value: 'issue', label: 'Exception/Issue' }
];

const PRIORITIES = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
  { value: 'critical', label: 'Critical' }
];

const STATUSES = [
  { value: 'not_started', label: 'Not Started' },
  { value: 'in_progress', label: 'In Progress' },
  { value: 'completed', label: 'Completed' }
];

export const TaskEditModal: React.FC<TaskEditModalProps> = ({
  task,
  organizationId,
  onClose,
  onTaskUpdated
}) => {
  useModalDismiss(true, onClose);

  const [taskType, setTaskType] = useState(task.task_type || 'collection');
  const [title, setTitle] = useState(task.title || '');
  const [description, setDescription] = useState(task.description || '');
  const [priority, setPriority] = useState(task.priority || 'medium');
  const [status, setStatus] = useState(task.status || 'not_started');
  const [dueDate, setDueDate] = useState(task.due_date || '');
  // Tri-state, and the empty string is NOT one of its values: null means
  // inherit from the evidence item and a team id means override (#822 §6).
  // Normalising an absent key to null rather than to '' matters — a server
  // that has not shipped the column yet must read as inheriting, which is the
  // truth, and not as an override onto a team called ''.
  const [owningTeamId, setOwningTeamId] = useState<string | null>(
    task.owning_team_id ?? null
  );

  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!title.trim() || !dueDate) {
      alert('Title and due date are required');
      return;
    }

    setLoading(true);
    try {
      await apiClient.patch(`/evidence-tasks/${task.id}`, {
        task_type: taskType,
        title: title.trim(),
        description: description.trim() || null,
        priority: priority,
        status: status,
        due_date: dueDate,
        // `assigned_user_id` is deliberately NOT sent, and its absence is the
        // point. This modal used to re-send the stored value on every save; now
        // that the API refuses a non-null assignee, re-sending one would 422 an
        // edit to an unrelated field on any task carrying a pre-cutover
        // assignee. Omitting the key leaves that value untouched, which is what
        // a title edit should do to it. Clearing one is a deliberate act with
        // its own path, not a side effect of saving this form.
        // Always sent, including as null. Null is a value here, not an
        // omission: returning an overriding task to inheriting is a thing a
        // user must be able to do, so the field cannot be one the client only
        // sends when it is set.
        owning_team_id: owningTeamId
      });

      onTaskUpdated();
      onClose();
    } catch (error) {
      console.error('Failed to update task:', error);
      alert('Failed to update task');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="task-modal-overlay" onClick={onClose}>
      <div className="task-modal-content" onClick={(e) => e.stopPropagation()}>
        <h2 className="task-modal-title">Edit Task</h2>

        <form onSubmit={handleSubmit}>
          {/* Task Type */}
          <div className="task-modal-form-group">
            <label className="task-modal-label">Task Type</label>
            <select
              value={taskType}
              onChange={(e) => setTaskType(e.target.value)}
              className="task-modal-select"
            >
              {TASK_TYPES.map((type) => (
                <option key={type.value} value={type.value}>
                  {type.label}
                </option>
              ))}
            </select>
          </div>

          {/* Title */}
          <div className="task-modal-form-group">
            <label className="task-modal-label">
              Title <span className="task-modal-required">*</span>
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
              className="task-modal-input"
            />
          </div>

          {/* Description */}
          <div className="task-modal-form-group">
            <label className="task-modal-label">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Detailed instructions or context..."
              rows={4}
              className="task-modal-textarea"
            />
          </div>

          {/* Three Column Row: Status, Priority, Due Date */}
          <div className="task-modal-grid">
            {/* Status */}
            <div className="task-modal-form-group" style={{ marginBottom: 0 }}>
              <label className="task-modal-label">Status</label>
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value)}
                className="task-modal-select"
              >
                {STATUSES.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Priority */}
            <div className="task-modal-form-group" style={{ marginBottom: 0 }}>
              <label className="task-modal-label">Priority</label>
              <select
                value={priority}
                onChange={(e) => setPriority(e.target.value)}
                className="task-modal-select"
              >
                {PRIORITIES.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Due Date */}
            <div className="task-modal-form-group" style={{ marginBottom: 0 }}>
              <label className="task-modal-label">Due Date</label>
              <input
                type="date"
                value={dueDate}
                onChange={(e) => setDueDate(e.target.value)}
                required
                className="task-modal-input"
              />
            </div>
          </div>

          {/* Owning team — inherit from the evidence item, or override it */}
          {task.evidence_tracking_id && (
            <div className="task-modal-form-group">
              <TaskOwningTeamField
                organizationId={organizationId}
                evidenceTrackingId={String(task.evidence_tracking_id)}
                value={owningTeamId}
                onChange={setOwningTeamId}
                disabled={loading}
                idPrefix={`task-${task.id}`}
              />
            </div>
          )}

          {/* Action Buttons */}
          <div className="task-modal-actions">
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              className="task-modal-btn task-modal-btn-cancel"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading || !title.trim()}
              className="task-modal-btn task-modal-btn-submit"
            >
              {loading ? 'Saving...' : 'Save Changes'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
