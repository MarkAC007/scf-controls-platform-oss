import { useState, useEffect, useCallback } from 'react'
import { apiClient } from '../data/apiClient'
import InviteUserModal from './InviteUserModal'

interface User {
  id: string
  email: string
  display_name: string | null
}

interface OrganizationMember {
  id: string
  organization_id: string
  user_id: string
  role: string
  joined_at: string
  user: User | null
}

interface UserManagementProps {
  organizationId: string
}

// Role permissions definition
const ROLE_PERMISSIONS = {
  admin: {
    name: 'Admin',
    description: 'Full access to all features',
    cssClass: 'admin',
    permissions: [
      'Manage users and roles',
      'Invite new users',
      'Remove users from organization',
      'Create, edit, and delete controls',
      'Create, edit, and delete evidence',
      'Manage tasks and assignments',
      'View all data and reports',
      'Configure organization settings'
    ]
  },
  editor: {
    name: 'Editor',
    description: 'Can edit content but not manage users',
    cssClass: 'editor',
    permissions: [
      'Create, edit, and delete controls',
      'Create, edit, and delete evidence',
      'Manage tasks and assignments',
      'Add comments and mentions',
      'View all data and reports'
    ]
  },
  viewer: {
    name: 'Viewer',
    description: 'Read-only access',
    cssClass: 'viewer',
    permissions: [
      'View controls and evidence',
      'View tasks and assignments',
      'View reports and dashboards',
      'Add comments'
    ]
  }
}

export default function UserManagement({ organizationId }: UserManagementProps) {
  const [members, setMembers] = useState<OrganizationMember[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showInviteModal, setShowInviteModal] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [showRolesInfo, setShowRolesInfo] = useState(false)
  const [copiedOrgId, setCopiedOrgId] = useState(false)

  const handleCopyOrgId = useCallback(() => {
    navigator.clipboard.writeText(organizationId).then(() => {
      setCopiedOrgId(true)
      setTimeout(() => setCopiedOrgId(false), 2000)
    })
  }, [organizationId])

  useEffect(() => {
    loadMembers()
  }, [organizationId])

  const loadMembers = async () => {
    try {
      setLoading(true)
      setError(null)
      const data = await apiClient.get<OrganizationMember[]>(`/organizations/${organizationId}/members`)
      setMembers(data)
    } catch (err: any) {
      console.error('Failed to load organization members:', err)
      setError(err.message || 'Failed to load users')
    } finally {
      setLoading(false)
    }
  }

  const handleRoleChange = async (userId: string, newRole: string) => {
    try {
      await apiClient.patch(`/organizations/${organizationId}/members/${userId}?role=${newRole}`, {})
      await loadMembers()
    } catch (err: any) {
      console.error('Failed to update role:', err)
      alert('Failed to update user role: ' + (err.message || 'Unknown error'))
    }
  }

  const handleRemoveMember = async (userId: string, userName: string) => {
    if (!confirm(`Are you sure you want to remove ${userName} from the organization?`)) {
      return
    }

    try {
      await apiClient.delete(`/organizations/${organizationId}/members/${userId}`)
      await loadMembers()
    } catch (err: any) {
      console.error('Failed to remove member:', err)
      alert('Failed to remove member: ' + (err.message || 'Unknown error'))
    }
  }

  const formatDate = (dateString: string): string => {
    const date = new Date(dateString)
    return date.toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric'
    })
  }

  const getInitials = (name: string | null, email: string): string => {
    if (name) {
      const parts = name.split(' ').filter(Boolean)
      if (parts.length >= 2) {
        return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
      }
      return name.substring(0, 2).toUpperCase()
    }
    return email.substring(0, 2).toUpperCase()
  }

  const getAvatarColor = (email: string): string => {
    const colors = [
      '#1976d2', '#388e3c', '#7b1fa2', '#c2185b',
      '#f57c00', '#00796b', '#5d4037', '#455a64'
    ]
    let hash = 0
    for (let i = 0; i < email.length; i++) {
      hash = email.charCodeAt(i) + ((hash << 5) - hash)
    }
    return colors[Math.abs(hash) % colors.length]
  }

  const getRoleClassName = (role: string): string => {
    switch (role) {
      case 'admin':
        return 'role-admin'
      case 'editor':
        return 'role-editor'
      case 'viewer':
        return 'role-viewer'
      default:
        return ''
    }
  }

  // Filter members based on search query
  const filteredMembers = members.filter(member => {
    const name = member.user?.display_name?.toLowerCase() || ''
    const email = member.user?.email?.toLowerCase() || ''
    const query = searchQuery.toLowerCase()
    return name.includes(query) || email.includes(query)
  })

  if (loading) {
    return (
      <div className="user-management">
        <div className="loading-state">Loading users...</div>
      </div>
    )
  }

  return (
    <div className="user-management">
      <div className="user-management-header">
        <div className="header-left">
          <h1>User Management</h1>
          <p className="subtitle">{members.length} member{members.length !== 1 ? 's' : ''} in organization</p>
          <div className="org-id-display">
            <span className="org-id-label">Organization ID:</span>
            <code className="org-id-value">{organizationId}</code>
            <button
              className="org-id-copy-btn"
              onClick={handleCopyOrgId}
              title="Copy Organization ID"
            >
              {copiedOrgId ? (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M20 6L9 17l-5-5" />
                </svg>
              ) : (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                  <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                </svg>
              )}
              {copiedOrgId ? 'Copied!' : 'Copy'}
            </button>
          </div>
        </div>
        <div className="header-right">
          <div className="search-box">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="11" cy="11" r="8" />
              <path d="M21 21l-4.35-4.35" />
            </svg>
            <input
              type="text"
              placeholder="Search users..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>
          <button
            className="btn-invite"
            onClick={() => setShowInviteModal(true)}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
              <circle cx="8.5" cy="7" r="4" />
              <line x1="20" y1="8" x2="20" y2="14" />
              <line x1="23" y1="11" x2="17" y2="11" />
            </svg>
            Invite User
          </button>
        </div>
      </div>

      {error && (
        <div className="error-banner">
          <span>{error}</span>
          <button onClick={loadMembers}>Retry</button>
        </div>
      )}

      {/* Role Permissions Info Panel */}
      <div className="roles-info-section">
        <button
          className="roles-info-toggle"
          onClick={() => setShowRolesInfo(!showRolesInfo)}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="10" />
            <path d="M12 16v-4" />
            <path d="M12 8h.01" />
          </svg>
          Role Permissions Reference
          <svg
            className={`toggle-arrow ${showRolesInfo ? 'open' : ''}`}
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path d="M6 9l6 6 6-6" />
          </svg>
        </button>

        {showRolesInfo && (
          <div className="roles-info-content">
            <div className="roles-info-grid">
              {Object.entries(ROLE_PERMISSIONS).map(([key, role]) => (
                <div key={key} className="role-card">
                  <div className={`role-card-header role-${role.cssClass}-header`}>
                    <span className={`role-badge role-${role.cssClass}-badge`}>
                      {role.name}
                    </span>
                    <span className="role-description">{role.description}</span>
                  </div>
                  <ul className="role-permissions-list">
                    {role.permissions.map((permission, idx) => (
                      <li key={idx}>
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <path d="M20 6L9 17l-5-5" />
                        </svg>
                        {permission}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
            <div className="roles-info-note">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <path d="M12 16v-4" />
                <path d="M12 8h.01" />
              </svg>
              <span>
                <strong>Note:</strong> Role-based access control enforcement is planned for a future update.
                Currently, all authenticated users have full access to all features.
              </span>
            </div>
          </div>
        )}
      </div>

      <div className="users-table-container">
        <table className="users-table">
          <thead>
            <tr>
              <th>User</th>
              <th>Role</th>
              <th>Joined</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {filteredMembers.length === 0 ? (
              <tr>
                <td colSpan={4} className="empty-state">
                  {searchQuery ? 'No users match your search' : 'No users found'}
                </td>
              </tr>
            ) : (
              filteredMembers.map(member => (
                <tr key={member.id}>
                  <td className="user-cell">
                    <div
                      className="user-avatar"
                      style={{ backgroundColor: getAvatarColor(member.user?.email || '') }}
                    >
                      {getInitials(member.user?.display_name || null, member.user?.email || '')}
                    </div>
                    <div className="user-info">
                      <div className="user-name">
                        {member.user?.display_name || 'No name'}
                      </div>
                      <div className="user-email">{member.user?.email}</div>
                    </div>
                  </td>
                  <td>
                    <select
                      className={`role-select ${getRoleClassName(member.role)}`}
                      value={member.role}
                      onChange={(e) => handleRoleChange(member.user_id, e.target.value)}
                    >
                      <option value="admin">Admin</option>
                      <option value="editor">Editor</option>
                      <option value="viewer">Viewer</option>
                    </select>
                  </td>
                  <td className="date-cell">
                    {formatDate(member.joined_at)}
                  </td>
                  <td className="actions-cell">
                    <button
                      className="btn-action btn-remove"
                      onClick={() => handleRemoveMember(
                        member.user_id,
                        member.user?.display_name || member.user?.email || 'this user'
                      )}
                      title="Remove from organization"
                    >
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M3 6h18" />
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                      </svg>
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {showInviteModal && (
        <InviteUserModal
          organizationId={organizationId}
          onClose={() => setShowInviteModal(false)}
          onInviteSent={() => {
            setShowInviteModal(false)
            // Optionally refresh members list
          }}
        />
      )}
    </div>
  )
}
