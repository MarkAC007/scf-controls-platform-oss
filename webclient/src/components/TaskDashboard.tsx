import React, { useState, useEffect } from 'react';
import { apiClient } from '../data/apiClient';

interface DashboardData {
  total_tasks: number;
  not_started: number;
  in_progress: number;
  completed: number;
  overdue: number;
  upcoming_tasks: Array<{
    id: string;
    evidence_id: string;
    due_date: string;
    status: string;
    days_until_due: number;
    frequency?: string;
    collecting_system?: string;
    method_of_collection?: string;
    owner?: string;
  }>;
}

interface TaskDashboardProps {
  onNavigateToEvidence?: (evidenceId: string) => void;
}

export const TaskDashboard: React.FC<TaskDashboardProps> = ({ onNavigateToEvidence }) => {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadDashboard();
  }, []);

  const loadDashboard = async () => {
    try {
      const dashboardData = await apiClient.get('/users/me/dashboard');
      setData(dashboardData);
    } catch (error) {
      console.error('Failed to load dashboard:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleCompleteTask = async (taskId: string, e: React.MouseEvent) => {
    e.stopPropagation(); // Prevent navigation when clicking complete button

    if (!confirm('Mark this task as completed?')) return;

    try {
      await apiClient.post(`/evidence-tasks/${taskId}/complete`, {
        completion_notes: 'Completed from dashboard'
      });
      await loadDashboard(); // Refresh
    } catch (error) {
      console.error('Failed to complete task:', error);
      alert('Failed to complete task');
    }
  };

  const getDaysBadgeClass = (days: number): string => {
    if (days <= 3) return 'days-critical';
    if (days <= 7) return 'days-warning';
    return 'days-ok';
  };

  if (loading) {
    return <div>Loading tasks...</div>;
  }

  if (!data) {
    return <div>Failed to load tasks</div>;
  }

  return (
    <div className="task-dashboard">
      <h2>My Tasks</h2>

      {/* Stats */}
      <div className="task-dashboard-stats">
        <div className="task-dashboard-stat-card">
          <div className="task-dashboard-stat-value stat-total">
            {data.total_tasks}
          </div>
          <div className="task-dashboard-stat-label">Total</div>
        </div>
        <div className="task-dashboard-stat-card">
          <div className="task-dashboard-stat-value stat-in-progress">
            {data.in_progress}
          </div>
          <div className="task-dashboard-stat-label">In Progress</div>
        </div>
        <div className="task-dashboard-stat-card">
          <div className="task-dashboard-stat-value stat-overdue">
            {data.overdue}
          </div>
          <div className="task-dashboard-stat-label">Overdue</div>
        </div>
        <div className="task-dashboard-stat-card">
          <div className="task-dashboard-stat-value stat-completed">
            {data.completed}
          </div>
          <div className="task-dashboard-stat-label">Completed</div>
        </div>
      </div>

      {/* Upcoming Tasks */}
      <h3>Upcoming Tasks</h3>
      {data.upcoming_tasks.length === 0 ? (
        <p className="task-dashboard-empty">No upcoming tasks</p>
      ) : (
        <div className="task-dashboard-upcoming">
          {data.upcoming_tasks.map((task) => (
            <div
              key={task.id}
              onClick={() => onNavigateToEvidence?.(task.evidence_id)}
              className="task-dashboard-item"
            >
              <div className="task-dashboard-item-content">
                <div className="task-dashboard-item-left">
                  <div className="task-dashboard-item-id">
                    <div className="task-dashboard-item-id-link">
                      {task.evidence_id}
                    </div>
                    {onNavigateToEvidence && (
                      <span className="task-dashboard-item-arrow">→</span>
                    )}
                  </div>

                  <div className="task-dashboard-item-meta">
                    {task.frequency && (
                      <div>
                        <strong>Frequency:</strong> {task.frequency}
                      </div>
                    )}
                    {task.owner && (
                      <div>
                        <strong>Owner:</strong> {task.owner}
                      </div>
                    )}
                    {task.collecting_system && (
                      <div className="task-dashboard-item-meta-full">
                        <strong>System:</strong> {task.collecting_system}
                      </div>
                    )}
                    {task.method_of_collection && (
                      <div className="task-dashboard-item-meta-full">
                        <strong>Method:</strong> {task.method_of_collection}
                      </div>
                    )}
                  </div>

                  <div className="task-dashboard-item-due">
                    <strong>Due:</strong> {new Date(task.due_date).toLocaleDateString('en-US', {
                      weekday: 'short',
                      year: 'numeric',
                      month: 'short',
                      day: 'numeric'
                    })}
                  </div>
                </div>

                <div className="task-dashboard-item-right">
                  <div className={`task-dashboard-days-badge ${getDaysBadgeClass(task.days_until_due)}`}>
                    {task.days_until_due} days
                  </div>

                  <button
                    onClick={(e) => handleCompleteTask(task.id, e)}
                    className="task-dashboard-complete-btn"
                  >
                    ✓ Complete
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
