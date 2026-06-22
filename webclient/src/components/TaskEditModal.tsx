import React, { useState, useEffect } from 'react';
import { apiClient } from '../data/apiClient';

interface User {
  id: string;
  email: string;
  display_name: string | null;
}

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
  const [taskType, setTaskType] = useState(task.task_type || 'collection');
  const [title, setTitle] = useState(task.title || '');
  const [description, setDescription] = useState(task.description || '');
  const [priority, setPriority] = useState(task.priority || 'medium');
  const [status, setStatus] = useState(task.status || 'not_started');
  const [dueDate, setDueDate] = useState(task.due_date || '');
  const [assignedUserId, setAssignedUserId] = useState(task.assigned_user_id || '');
  const [members, setMembers] = useState<User[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    loadOrganizationMembers();
  }, []);

  const loadOrganizationMembers = async () => {
    try {
      const data = await apiClient.get(`/organizations/${organizationId}/members`);
      setMembers(data.map((m: any) => m.user).filter(Boolean));
    } catch (error) {
      console.error('Failed to load members:', error);
    }
  };

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
        assigned_user_id: assignedUserId || null
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

          {/* Assign To */}
          <div className="task-modal-form-group">
            <label className="task-modal-label">Assign To</label>
            <select
              value={assignedUserId}
              onChange={(e) => setAssignedUserId(e.target.value)}
              className="task-modal-select"
            >
              <option value="">Unassigned</option>
              {members.map((member) => (
                <option key={member.id} value={member.id}>
                  {member.display_name || member.email}
                </option>
              ))}
            </select>
          </div>

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
