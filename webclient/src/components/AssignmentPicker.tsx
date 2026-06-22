import React, { useState, useEffect } from 'react';
import { apiClient } from '../data/apiClient';

interface User {
  id: string;
  email: string;
  display_name: string | null;
}

interface AssignmentPickerProps {
  organizationId: string;
  assignableType: 'control' | 'evidence';
  assignableId: string;
  currentAssignments?: any[];
  onAssignmentChange?: () => void;
}

export const AssignmentPicker: React.FC<AssignmentPickerProps> = ({
  organizationId,
  assignableType,
  assignableId,
  currentAssignments = [],
  onAssignmentChange
}) => {
  const [members, setMembers] = useState<User[]>([]);
  const [loading, setLoading] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const [assignments, setAssignments] = useState(currentAssignments);

  useEffect(() => {
    loadOrganizationMembers();
  }, [organizationId]);

  useEffect(() => {
    loadAssignments();
  }, [assignableType, assignableId]);

  const loadOrganizationMembers = async () => {
    try {
      const data = await apiClient.get(`/organizations/${organizationId}/members`);
      setMembers(data.map((m: any) => m.user).filter(Boolean));
    } catch (error) {
      console.error('Failed to load organization members:', error);
    }
  };

  const loadAssignments = async () => {
    try {
      const data = await apiClient.get(`/assignments?assignable_type=${assignableType}&assignable_id=${assignableId}`);
      setAssignments(data);
    } catch (error) {
      console.error('Failed to load assignments:', error);
    }
  };

  const handleAssign = async (userId: string) => {
    setLoading(true);
    try {
      await apiClient.post('/assignments', {
        assignable_type: assignableType,
        assignable_id: assignableId,
        user_id: userId,
        role: 'primary'
      });
      await loadAssignments();
      setShowDropdown(false);
      onAssignmentChange?.();
    } catch (error: any) {
      if (error?.message?.includes('already assigned')) {
        await loadAssignments();
        setShowDropdown(false);
      } else {
        console.error('Failed to create assignment:', error);
        alert('Failed to assign user');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleUnassign = async (assignmentId: string) => {
    setLoading(true);
    try {
      await apiClient.delete(`/assignments/${assignmentId}`);
      await loadAssignments();
      onAssignmentChange?.();
    } catch (error) {
      console.error('Failed to remove assignment:', error);
      alert('Failed to remove assignment');
    } finally {
      setLoading(false);
    }
  };

  const assignedUserIds = new Set(assignments.map((a: any) => String(a.user_id)));
  const availableMembers = members.filter(m => !assignedUserIds.has(String(m.id)));

  return (
    <div className="assignment-picker">
      <label className="assignment-picker-label">Assigned To</label>

      <div className="assignment-picker-list">
        {assignments.map((assignment: any) => (
          <span key={assignment.id} className="assignment-picker-tag">
            {assignment.user?.display_name || assignment.user?.email || 'Unknown'}
            <button
              onClick={() => handleUnassign(assignment.id)}
              disabled={loading}
              className="assignment-picker-tag-remove"
              aria-label="Remove assignment"
            >
              &times;
            </button>
          </span>
        ))}

        {assignments.length === 0 && (
          <span className="assignment-picker-empty">No assignments</span>
        )}
      </div>

      <div className="assignment-picker-action">
        <button
          onClick={() => setShowDropdown(!showDropdown)}
          disabled={loading || availableMembers.length === 0}
          className="btn-outline assignment-picker-btn"
        >
          + Assign User
        </button>

        {showDropdown && availableMembers.length > 0 && (
          <div className="assignment-picker-dropdown">
            {availableMembers.map(member => (
              <div
                key={member.id}
                onClick={() => handleAssign(member.id)}
                className="assignment-picker-dropdown-item"
              >
                <span className="assignment-picker-dropdown-name">
                  {member.display_name || 'No name'}
                </span>
                <span className="assignment-picker-dropdown-email">
                  {member.email}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
