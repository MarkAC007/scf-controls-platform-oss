import React, { useState, useEffect } from 'react';
import { apiClient } from '../data/apiClient';

interface NotificationBellProps {
  onNavigateToEvidence?: (evidenceId: string) => void;
  onNavigateToControl?: (controlId: string) => void;
  onNavigateToTask?: () => void;
}

export const NotificationBell: React.FC<NotificationBellProps> = ({
  onNavigateToEvidence,
  onNavigateToControl,
  onNavigateToTask
}) => {
  const [unreadCount, setUnreadCount] = useState(0);
  const [notifications, setNotifications] = useState<any[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);

  useEffect(() => {
    loadNotifications();
    // Poll every 30 seconds
    const interval = setInterval(loadNotifications, 30000);
    return () => clearInterval(interval);
  }, []);

  const loadNotifications = async () => {
    try {
      const data = await apiClient.get('/notifications?limit=10');
      setUnreadCount(data.unread_count || 0);
      setNotifications(data.notifications || []);
    } catch (error) {
      console.error('Failed to load notifications:', error);
    }
  };

  const handleMarkAsRead = async (notificationId: string) => {
    try {
      await apiClient.patch(`/notifications/${notificationId}/read`, {});
      await loadNotifications();
    } catch (error) {
      console.error('Failed to mark notification as read:', error);
    }
  };

  const handleMarkAllAsRead = async () => {
    try {
      await apiClient.patch('/notifications/read-all', {});
      await loadNotifications();
      setShowDropdown(false);
    } catch (error) {
      console.error('Failed to mark all as read:', error);
    }
  };

  const handleNotificationClick = async (notification: any) => {
    // Mark as read
    if (!notification.is_read) {
      await handleMarkAsRead(notification.id);
    }

    try {
      // Navigate based on reference type
      if (notification.reference_type === 'task') {
        // For task notifications, navigate to Tasks tab
        onNavigateToTask?.();
        setShowDropdown(false);

      } else if (notification.reference_type === 'comment') {
        // For comment notifications, we need to find what the comment is on
        const comments = await apiClient.get(`/comments/${notification.reference_id}/history`);
        // For now, navigate to Tasks tab (most comments will be on tasks)
        onNavigateToTask?.();
        setShowDropdown(false);

      } else if (notification.reference_type === 'evidence') {
        // Get evidence details and navigate
        try {
          const tasks = await apiClient.get(`/evidence-tasks`);
          const task = tasks.find((t: any) => t.evidence_tracking_id === notification.reference_id);
          if (task && task.evidence_id) {
            onNavigateToEvidence?.(task.evidence_id);
          }
        } catch (error) {
          console.error('Could not navigate to evidence:', error);
        }
        setShowDropdown(false);

      } else if (notification.reference_type === 'control' && notification.reference_id) {
        // Navigate to control scoping
        onNavigateToControl?.(notification.reference_id);
        setShowDropdown(false);
      }
    } catch (error) {
      console.error('Navigation error:', error);
      setShowDropdown(false);
    }
  };

  return (
    <div className="notification-bell-container">
      <button
        onClick={() => setShowDropdown(!showDropdown)}
        className="notification-bell-button"
      >
        🔔
        {unreadCount > 0 && (
          <span className="notification-bell-badge">
            {unreadCount}
          </span>
        )}
      </button>

      {showDropdown && (
        <div className="notification-dropdown">
          <div className="notification-dropdown-header">
            <h3>Notifications</h3>
            {unreadCount > 0 && (
              <button
                onClick={handleMarkAllAsRead}
                className="notification-mark-all-btn"
              >
                Mark all as read
              </button>
            )}
          </div>

          {notifications.length === 0 ? (
            <div className="notification-empty">
              No notifications
            </div>
          ) : (
            notifications.map((notification) => (
              <div
                key={notification.id}
                onClick={() => handleNotificationClick(notification)}
                className={`notification-item ${!notification.is_read ? 'unread' : ''}`}
              >
                <div className="notification-item-content">
                  {!notification.is_read && (
                    <div className="notification-unread-dot" />
                  )}
                  <div className="notification-item-body">
                    <div className={`notification-item-message ${!notification.is_read ? 'unread' : ''}`}>
                      {notification.message}
                    </div>
                    <div className="notification-item-time">
                      {new Date(notification.created_at).toLocaleString()}
                    </div>
                  </div>
                  <div className="notification-item-arrow">
                    →
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
};
