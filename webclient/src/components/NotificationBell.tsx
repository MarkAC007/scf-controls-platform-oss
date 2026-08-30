import React, { useState, useEffect } from 'react';
import { apiClient } from '../data/apiClient';
import { interactiveRowProps } from '../data/interactiveRow';

interface NotificationBellProps {
  onNavigateToEvidence?: (evidenceId: string) => void;
  onNavigateToControl?: (controlId: string) => void;
  onNavigateToTask?: () => void;
  /** Catalog reconciliation notifications navigate to the catalog changelog */
  onNavigateToChangelog?: () => void;
}

export const NotificationBell: React.FC<NotificationBellProps> = ({
  onNavigateToEvidence,
  onNavigateToControl,
  onNavigateToTask,
  onNavigateToChangelog
}) => {
  const [unreadCount, setUnreadCount] = useState(0);
  const [notifications, setNotifications] = useState<any[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [navigationError, setNavigationError] = useState<string | null>(null);

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
    setNavigationError(null);
    // Mark as read
    if (!notification.is_read) {
      await handleMarkAsRead(notification.id);
    }

    try {
      // The server resolves the evidence key (E-HRS-16) for evidence and task
      // references and returns it as reference_key. Before this existed the
      // bell fetched /evidence-tasks to recover it, and when that row had no
      // tasks the click did nothing at all — no navigation, no message.
      if (notification.reference_key) {
        onNavigateToEvidence?.(notification.reference_key);
        setShowDropdown(false);

      } else if (notification.reference_type === 'task') {
        // A task notification with no evidence key is not about evidence.
        // The task list is the correct destination for those.
        onNavigateToTask?.();
        setShowDropdown(false);

      } else if (notification.reference_type === 'evidence') {
        // reference_type says evidence but the server could not resolve a key,
        // so the tracking row is gone. Say so rather than closing on nothing.
        setNavigationError('That evidence item no longer exists.');

      } else if (notification.reference_type === 'comment') {
        // For comment notifications, we need to find what the comment is on
        const comments = await apiClient.get(`/comments/${notification.reference_id}/history`);
        // For now, navigate to Tasks tab (most comments will be on tasks)
        onNavigateToTask?.();
        setShowDropdown(false);

      } else if (notification.reference_type === 'control' && notification.reference_id) {
        // Navigate to control scoping
        onNavigateToControl?.(notification.reference_id);
        setShowDropdown(false);

      } else if (notification.reference_type === 'catalog') {
        // Catalog reconciliation applied/rolled back — show what changed
        onNavigateToChangelog?.();
        setShowDropdown(false);

      } else {
        // engagement_query and any future reference_type land here. Previously
        // they fell past every branch, so the dropdown did not even close and
        // the click was indistinguishable from a missed tap.
        setNavigationError('There is nowhere to open this notification yet.');
      }
    } catch (error) {
      console.error('Navigation error:', error);
      setNavigationError('Could not open that item.');
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

          {navigationError && (
            <div className="notification-nav-error" role="alert">
              {navigationError}
            </div>
          )}

          {notifications.length === 0 ? (
            <div className="notification-empty">
              No notifications
            </div>
          ) : (
            notifications.map((notification) => (
              <div
                key={notification.id}
                className={`notification-item ${!notification.is_read ? 'unread' : ''}`}
                {...interactiveRowProps(() => handleNotificationClick(notification))}
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
                      {/* Org label (#852): the bell aggregates every accessible
                          org, so a consultant needs to see which is which. */}
                      {notification.organization_name && (
                        <span className="notification-item-org">
                          {notification.organization_name}
                          {' · '}
                        </span>
                      )}
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
