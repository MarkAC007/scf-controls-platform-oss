import { useState, useRef, useEffect } from 'react'
import { useAuth } from '../contexts/AuthContext'

interface UserProfileDropdownProps {
  onNavigateToUsers?: () => void
}

export default function UserProfileDropdown({ onNavigateToUsers }: UserProfileDropdownProps) {
  const { user, logout } = useAuth()
  const [isOpen, setIsOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }

    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // Close on escape key
  useEffect(() => {
    function handleEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setIsOpen(false)
      }
    }

    document.addEventListener('keydown', handleEscape)
    return () => document.removeEventListener('keydown', handleEscape)
  }, [])

  if (!user) return null

  // Generate initials from name
  const getInitials = (name: string): string => {
    if (!name || name === 'Loading...') return '...'
    const parts = name.split(' ').filter(Boolean)
    if (parts.length >= 2) {
      return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
    }
    return name.substring(0, 2).toUpperCase()
  }

  // Generate a consistent color based on email
  const getAvatarColor = (email: string): string => {
    const colors = [
      '#1976d2', // blue
      '#388e3c', // green
      '#7b1fa2', // purple
      '#c2185b', // pink
      '#f57c00', // orange
      '#00796b', // teal
      '#5d4037', // brown
      '#455a64', // blue-grey
    ]
    let hash = 0
    for (let i = 0; i < email.length; i++) {
      hash = email.charCodeAt(i) + ((hash << 5) - hash)
    }
    return colors[Math.abs(hash) % colors.length]
  }

  const initials = getInitials(user.name)
  const avatarColor = getAvatarColor(user.email)

  return (
    <div className="user-profile-dropdown" ref={dropdownRef}>
      <button
        className="user-avatar-button"
        onClick={() => setIsOpen(!isOpen)}
        aria-expanded={isOpen}
        aria-haspopup="true"
        title={`${user.name} (${user.email})`}
      >
        {user.picture ? (
          <img
            src={user.picture}
            alt={user.name}
            className="user-avatar-image"
          />
        ) : (
          <div
            className="user-avatar-initials"
            style={{ backgroundColor: avatarColor }}
          >
            {initials}
          </div>
        )}
        <svg
          className={`dropdown-arrow ${isOpen ? 'open' : ''}`}
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="currentColor"
        >
          <path d="M2.5 4.5L6 8L9.5 4.5" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
      </button>

      {isOpen && (
        <div className="user-dropdown-menu">
          <div className="user-dropdown-header">
            {user.picture ? (
              <img
                src={user.picture}
                alt={user.name}
                className="user-dropdown-avatar-image"
              />
            ) : (
              <div
                className="user-dropdown-avatar-initials"
                style={{ backgroundColor: avatarColor }}
              >
                {initials}
              </div>
            )}
            <div className="user-dropdown-info">
              <div className="user-dropdown-name">{user.name}</div>
              <div className="user-dropdown-email">{user.email}</div>
            </div>
          </div>

          <div className="user-dropdown-divider" />

          <div className="user-dropdown-items">
            {onNavigateToUsers && (
              <button
                className="user-dropdown-item"
                onClick={() => {
                  setIsOpen(false)
                  onNavigateToUsers()
                }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
                  <circle cx="9" cy="7" r="4" />
                  <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
                  <path d="M16 3.13a4 4 0 0 1 0 7.75" />
                </svg>
                User Management
              </button>
            )}

            <button
              className="user-dropdown-item user-dropdown-signout"
              onClick={() => {
                setIsOpen(false)
                logout()
              }}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                <polyline points="16 17 21 12 16 7" />
                <line x1="21" y1="12" x2="9" y2="12" />
              </svg>
              Sign Out
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
