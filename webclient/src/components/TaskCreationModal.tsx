import React, { useState, useEffect } from 'react';
import { apiClient } from '../data/apiClient';

interface User {
  id: string;
  email: string;
  display_name: string | null;
}

interface TaskCreationModalProps {
  evidenceTrackingId: string;
  evidenceId: string;
  organizationId: string;
  onClose: () => void;
  onTaskCreated: () => void;
}

const TASK_TYPES = [
  { value: 'feasibility', label: 'Feasibility Check', description: 'Confirm evidence can be collected' },
  { value: 'setup', label: 'Setup/Configuration', description: 'Prepare collection mechanism' },
  { value: 'collection', label: 'Collection', description: 'Gather the evidence' },
  { value: 'review', label: 'Review/Validation', description: 'Verify evidence quality' },
  { value: 'documentation', label: 'Documentation', description: 'Update procedures/notes' },
  { value: 'issue', label: 'Exception/Issue', description: 'Handle problems' }
];

const PRIORITIES = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
  { value: 'critical', label: 'Critical' }
];

export const TaskCreationModal: React.FC<TaskCreationModalProps> = ({
  evidenceTrackingId,
  evidenceId,
  organizationId,
  onClose,
  onTaskCreated
}) => {
  const [taskType, setTaskType] = useState('collection');
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [priority, setPriority] = useState('medium');
  const [dueDate, setDueDate] = useState('');
  const [assignedUserId, setAssignedUserId] = useState('');
  const [members, setMembers] = useState<User[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    loadOrganizationMembers();
    // Set default due date to 30 days from now
    const defaultDate = new Date();
    defaultDate.setDate(defaultDate.getDate() + 30);
    setDueDate(defaultDate.toISOString().split('T')[0]);
  }, []);

  useEffect(() => {
    // Update title based on task type if it's empty or was auto-generated
    const selectedType = TASK_TYPES.find(t => t.value === taskType);
    if (selectedType && (!title || title.startsWith('Collect Evidence:') || title.includes(evidenceId))) {
      setTitle(`${selectedType.label}: ${evidenceId}`);
    }
  }, [taskType, evidenceId]);

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
      await apiClient.post('/evidence-tasks', {
        evidence_tracking_id: evidenceTrackingId,
        task_type: taskType,
        title: title.trim(),
        description: description.trim() || null,
        priority: priority,
        due_date: dueDate,
        status: 'not_started',
        assigned_user_id: assignedUserId || null,
        dependencies: [],
        attachments: []
      });

      onTaskCreated();
      onClose();
    } catch (error) {
      console.error('Failed to create task:', error);
      alert('Failed to create task');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="task-modal-overlay" onClick={onClose}>
      <div className="task-modal-content" onClick={(e) => e.stopPropagation()}>
        <div style={{ marginBottom: '1.5rem' }}>
          <h2 className="task-modal-title" style={{ marginBottom: '0.5rem' }}>Create New Task</h2>
          <p className="task-modal-subtitle">
            Evidence: <strong>{evidenceId}</strong>
          </p>
        </div>

        <form onSubmit={handleSubmit}>
          {/* Task Type */}
          <div className="task-modal-form-group">
            <label className="task-modal-label">
              Task Type <span className="task-modal-required">*</span>
            </label>
            <div className="task-modal-type-grid">
              {TASK_TYPES.map((type) => (
                <div
                  key={type.value}
                  onClick={() => setTaskType(type.value)}
                  className={`task-modal-type-card ${taskType === type.value ? 'selected' : ''}`}
                >
                  <div className="task-modal-type-card-label">{type.label}</div>
                  <div className="task-modal-type-card-desc">{type.description}</div>
                </div>
              ))}
            </div>
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
              placeholder="e.g., Confirm AWS CloudTrail Access"
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
              placeholder="Detailed instructions or context for this task..."
              rows={4}
              className="task-modal-textarea"
            />
          </div>

          {/* Priority and Due Date Row */}
          <div className="task-modal-grid-2">
            {/* Priority */}
            <div className="task-modal-form-group" style={{ marginBottom: 0 }}>
              <label className="task-modal-label">
                Priority <span className="task-modal-required">*</span>
              </label>
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
              <label className="task-modal-label">
                Due Date <span className="task-modal-required">*</span>
              </label>
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

          {/* Task Type Help Text */}
          <div className="task-modal-help">
            <strong>Task Type Guide:</strong>
            <ul>
              <li><strong>Feasibility:</strong> Confirm evidence can be collected</li>
              <li><strong>Setup:</strong> Configure automated collection</li>
              <li><strong>Collection:</strong> Actually gather the evidence (auto-generated)</li>
              <li><strong>Review:</strong> Validate collected evidence</li>
              <li><strong>Documentation:</strong> Update processes/notes</li>
              <li><strong>Issue:</strong> Resolve collection problems</li>
            </ul>
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
              disabled={loading || !title.trim() || !dueDate}
              className="task-modal-btn task-modal-btn-submit"
            >
              {loading ? 'Creating...' : 'Create Task'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
