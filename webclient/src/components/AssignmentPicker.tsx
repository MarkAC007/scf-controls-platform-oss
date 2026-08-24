import React, { useState, useEffect } from 'react';
import { apiClient } from '../data/apiClient';
import { ContractorBadge } from './ContractorBadge';
import { useOrgMemberTypes } from '../hooks/useOrgMemberTypes';

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
  /**
   * Heading for the list. Defaults to "Assigned To".
   *
   * This component manages the polymorphic `assignments` table — a multi-user,
   * role-bearing list that nothing downstream reads. It is NOT the field that
   * decides who a generated collection task belongs to; that is
   * `evidence_tracking.assigned_user_id`, edited by `EvidenceAssigneeSelect`.
   * The evidence panel now shows both, so it overrides this label to say which
   * is which (#781).
   */
  label?: string;
}

export const AssignmentPicker: React.FC<AssignmentPickerProps> = ({
  organizationId,
  assignableType,
  assignableId,
  currentAssignments = [],
  onAssignmentChange,
  label = 'Assigned To'
}) => {
  const [members, setMembers] = useState<User[]>([]);
  const [loading, setLoading] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const [assignments, setAssignments] = useState(currentAssignments);

  // Contractor labelling for both halves of this control: the people already
  // assigned, and the people you could assign next. Everyone stays assignable
  // — this says who somebody is, not whether they may be picked.
  const { memberTypeOf } = useOrgMemberTypes(organizationId);

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
      <label className="assignment-picker-label">{label}</label>

      <div className="assignment-picker-list">
        {assignments.map((assignment: any) => (
          <span key={assignment.id} className="assignment-picker-tag">
            {assignment.user?.display_name || assignment.user?.email || 'Unknown'}
            <ContractorBadge
              className="contractor-badge-inline"
              memberType={memberTypeOf(assignment.user_id)}
              personName={assignment.user?.display_name || assignment.user?.email}
            />
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
                  <ContractorBadge
                    className="contractor-badge-inline"
                    memberType={memberTypeOf(member.id)}
                    personName={member.display_name || member.email}
                  />
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
