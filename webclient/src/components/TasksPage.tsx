// SPDX-License-Identifier: AGPL-3.0-only
import React, { useState, useEffect, useMemo, useCallback, useRef, type ReactNode } from 'react';
import { toast } from 'react-hot-toast';
import { apiClient } from '../data/apiClient';
import { listMeasurementTasks, type MeasurementTask } from '../data/securityObjectivesApi';
import { useIsOrgEditor } from '../hooks/useHasOrgRole';
import { useIsmsMetrics } from '../hooks/useSecurityObjectives';
import AppLink from './AppLink';
import LogValueModal from './isms/objectives/LogValueModal';
import { ModernCommentThread } from './ModernCommentThread';
import { frequencyLabel } from '../data/frequencyVocabulary'
import { useOrgMemberTypes } from '../hooks/useOrgMemberTypes';
import { useTaskTeamOwnership } from '../hooks/useTaskTeamOwnership';
import TaskOwningTeamBadge from './TaskOwningTeamBadge';
import TaskDetailPage from './TaskDetailPage';
import RichText from './RichText';
import FilterSidebar, {
  FilterGroup,
  FilterSelect,
  defaultFiltersCollapsed,
} from './explorer/FilterSidebar'
import FilterRadio from './explorer/FilterRadio';
import ListToolbar from './explorer/ListToolbar';

interface Task {
  id: string;
  evidence_tracking_id: string;
  evidence_id: string;
  task_type: string;
  title: string;
  description?: string;
  priority: string;
  due_date: string;
  status: string;
  assigned_user_id?: string;
  /** Null or absent means the task inherits its evidence item's team (#822 §6). */
  owning_team_id?: string | null;
  completed_date?: string;
  completion_notes?: string;
  /** When the task was minted (server timestamp). */
  created_at?: string | null;
  dependencies?: string[];
  attachments?: any[];
  frequency?: string;
  collecting_system?: string;
  method_of_collection?: string;
  owner?: string;
  assigned_user?: {
    id: string;
    email: string;
    display_name: string;
  };
}

interface TasksPageProps {
  onNavigateToEvidence: (evidenceId: string) => void;
  organizationId: string;
  /** The task id to show in detail, from ?task= URL param. null = list view. */
  taskItem?: string | null;
  /** Called when user opens/closes/pages the detail. App owns push/replace decision. */
  onTaskItemChange?: (id: string | null) => void;
}

const STATUS_OPTIONS = [
  { value: 'all', label: 'All Statuses' },
  { value: 'not_started', label: 'Not Started' },
  { value: 'in_progress', label: 'In Progress' },
  { value: 'completed', label: 'Completed' },
  { value: 'wont_do', label: "Won't Do" },
];

const TYPE_OPTIONS = [
  { value: 'all', label: 'All Types' },
  { value: 'feasibility', label: 'Feasibility' },
  { value: 'setup', label: 'Setup' },
  { value: 'collection', label: 'Collection' },
  { value: 'review', label: 'Review' },
  { value: 'documentation', label: 'Documentation' },
  { value: 'issue', label: 'Issue' },
];

/**
 * "Measurement" is a client-side type (phase 3 B2): the rows come from the ISMS metrics
 * measurement-tasks call, and the evidence-tasks endpoint rejects it as `task_type`.
 */
const MEASUREMENT_TYPE = 'measurement';

const TASK_TYPE_LABELS: Record<string, string> = {
  feasibility: 'Feasibility',
  setup: 'Setup',
  collection: 'Collection',
  review: 'Review',
  documentation: 'Documentation',
  issue: 'Issue',
  [MEASUREMENT_TYPE]: 'Measurement',
};

const STATUS_LABELS: Record<string, string> = {
  not_started: 'Not started',
  in_progress: 'In progress',
  completed: 'Completed',
  wont_do: "Won't do",
};

/** Done, or deliberately not going to be done: nobody needs to act on it. */
const CLOSED_STATUSES = new Set(['completed', 'wont_do']);
const isClosedStatus = (status: string) => CLOSED_STATUSES.has(status);

// ── Column sorting ─────────────────────────────────────────────────────────
type SortKey = 'evidence' | 'title' | 'type' | 'status' | 'priority' | 'due' | 'created' | 'team';
type SortDir = 'asc' | 'desc';

const PRIORITY_RANK: Record<string, number> = { low: 1, medium: 2, high: 3, critical: 4 };
const STATUS_RANK: Record<string, number> = { not_started: 0, in_progress: 1, completed: 2, wont_do: 3 };

const dateValue = (value?: string | null) => (value ? new Date(value).getTime() : Number.NaN);

/** Resolves a task to the team name shown in its TEAM cell ('' when none). */
export type TeamNameOf = (task: Task) => string;

/** Compare two tasks on one column. A NaN result means a missing date; sortTasks orders those last. */
function compareTasks(a: Task, b: Task, key: SortKey, teamNameOf?: TeamNameOf): number {
  switch (key) {
    case 'team': return (teamNameOf?.(a) || '').localeCompare(teamNameOf?.(b) || '');
    case 'evidence': return (a.evidence_id || '').localeCompare(b.evidence_id || '');
    case 'title': return (a.title || '').localeCompare(b.title || '');
    case 'type': return (a.task_type || '').localeCompare(b.task_type || '');
    case 'status': return (STATUS_RANK[a.status] ?? 99) - (STATUS_RANK[b.status] ?? 99);
    case 'priority': return (PRIORITY_RANK[a.priority] ?? 0) - (PRIORITY_RANK[b.priority] ?? 0);
    case 'due': return dateValue(a.due_date) - dateValue(b.due_date);
    case 'created': return dateValue(a.created_at) - dateValue(b.created_at);
  }
}

export function sortTasks<T extends Task>(rows: T[], key: SortKey, dir: SortDir, teamNameOf?: TeamNameOf): T[] {
  const sign = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const result = compareTasks(a, b, key, teamNameOf);
    if (Number.isNaN(result)) {
      // At least one side has no value. Missing goes last regardless of
      // direction; two missing values are equal so the sort stays stable.
      const aMissing = key === 'created' ? !a.created_at : !a.due_date;
      const bMissing = key === 'created' ? !b.created_at : !b.due_date;
      if (aMissing && bMissing) return 0;
      return aMissing ? 1 : -1;
    }
    return result * sign;
  });
}

const PRIORITY_LABELS: Record<string, string> = {
  low: 'Low',
  medium: 'Medium',
  high: 'High',
  critical: 'Critical',
};

/** Color token class for the left-edge tick bar */
function tickBarClass(status: string, isOverdue: boolean): string {
  if (isOverdue) return 'task-row-tick--overdue';
  if (status === 'completed') return 'task-row-tick--completed';
  if (status === 'wont_do') return 'task-row-tick--wont-do';
  if (status === 'in_progress') return 'task-row-tick--in-progress';
  return 'task-row-tick--not-started';
}

/** Color token class for priority text */
function priorityClass(priority: string): string {
  return `task-priority-${priority}`;
}

/** Badge classes for task type */
function typeClass(taskType: string): string {
  return `task-badge task-type-badge task-type-${taskType}`;
}

/** Badge classes for status */
function statusBadgeClass(status: string): string {
  return `task-badge task-status-badge task-status-${status}`;
}

const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * Whole calendar days from today to the due date: 0 today, negative once the day is over.
 * The due date is a calendar date, not an instant: only its YYYY-MM-DD part is read, and it is
 * counted against today's date on the viewer's calendar, so no time zone moves it a day.
 */
export function getDaysUntilDue(dueDate: string): number {
  if (!dueDate) return Number.NaN;
  const [year, month, day] = dueDate.slice(0, 10).split('-').map(Number);
  const now = new Date();
  // Both dates as UTC midnights, so a daylight-saving change cannot make a day 23 or 25 hours.
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((Date.UTC(year, month - 1, day) - today) / DAY_MS);
}

/**
 * The one overdue rule, shared by the "Overdue" tile and the row flags: still open and due
 * strictly before today. A task due today is not overdue; the API filter says the same.
 */
export function isTaskOverdue(task: { due_date: string; status: string }): boolean {
  return !isClosedStatus(task.status) && getDaysUntilDue(task.due_date) < 0;
}

function getDueDateText(daysUntilDue: number, isCompleted: boolean): { text: string; cls: string } {
  if (isCompleted) return { text: '', cls: '' };
  if (daysUntilDue < 0) {
    return { text: `${Math.abs(daysUntilDue)} days overdue`, cls: 'task-due-overdue' };
  }
  if (daysUntilDue <= 7) {
    return { text: `${daysUntilDue} days`, cls: 'task-due-warning' };
  }
  return { text: `${daysUntilDue} days`, cls: 'task-due-ok' };
}

/** Return true if the search term matches the task */
function matchesSearch(task: Task, query: string): boolean {
  if (!query.trim()) return true;
  const q = query.toLowerCase();
  if (task.title && task.title.toLowerCase().includes(q)) return true;
  if (task.description && task.description.toLowerCase().includes(q)) return true;
  if (task.evidence_id && task.evidence_id.toLowerCase().includes(q)) return true;
  return false;
}

/** A row of the list: an evidence task, or a measurement task carried in a task's shape for sorting. */
type ListRow = Task & { measurement?: MeasurementTask };

function measurementAsRow(m: MeasurementTask): ListRow {
  return {
    id: m.id,
    evidence_tracking_id: '',
    evidence_id: m.ref,
    task_type: MEASUREMENT_TYPE,
    title: m.title,
    priority: '',
    due_date: m.due_date,
    status: m.status,
    owning_team_id: m.owning_team_id,
    created_at: null,
    completed_date: m.completed_date ?? undefined,
    measurement: m,
  };
}

/**
 * Measurement rows into the server's due-date order, without reordering the evidence tasks.
 * A measurement row goes before the first evidence task due later, or with no due date.
 */
export function mergeByDueDate(evidence: Task[], measures: MeasurementTask[]): ListRow[] {
  const queue = [...measures].sort((a, b) => a.due_date.localeCompare(b.due_date));
  const out: ListRow[] = [];
  let j = 0;
  for (const task of evidence) {
    while (j < queue.length && (!task.due_date || queue[j].due_date < task.due_date.slice(0, 10))) {
      out.push(measurementAsRow(queue[j++]));
    }
    out.push(task);
  }
  while (j < queue.length) out.push(measurementAsRow(queue[j++]));
  return out;
}

function matchesMeasurementSearch(m: MeasurementTask, query: string): boolean {
  if (!query.trim()) return true;
  const q = query.toLowerCase();
  return [m.title, m.ref, m.metric_name].some(text => text.toLowerCase().includes(q));
}

/**
 * The role check the objectives register uses for logging a value, asked once for the list and
 * only when a measurement row is shown (evidence rows never needed it).
 */
function EditorRole({ organizationId, children }: { organizationId: string; children: (canEdit: boolean) => ReactNode }) {
  return <>{children(useIsOrgEditor(organizationId))}</>;
}

function WithEditorRole({ organizationId, ask, children }: {
  organizationId: string;
  ask: boolean;
  children: (canEdit: boolean) => ReactNode;
}) {
  return ask ? <EditorRole organizationId={organizationId}>{children}</EditorRole> : <>{children(false)}</>;
}

/** The objectives register's log-value form, for the task's objective and period. */
function MeasurementLogValue({ organizationId, task, onClose, onLogged }: {
  organizationId: string;
  task: MeasurementTask;
  onClose: () => void;
  onLogged: () => void;
}) {
  const metrics = useIsmsMetrics(organizationId);
  const objective = metrics.data?.items.find(o => o.id === task.objective_id);
  const missing = metrics.isError || (metrics.isSuccess && !objective);
  useEffect(() => {
    if (missing) {
      toast.error(`${task.ref} could not be opened.`);
      onClose();
    }
  }, [missing, task.ref, onClose]);
  if (!objective) return null;
  return (
    <LogValueModal
      organizationId={organizationId}
      objective={objective}
      periodStart={task.period_start}
      onLogged={onLogged}
      onClose={onClose}
    />
  );
}

/** A date as the evidence rows print it (Nov 30, 2026), so one list has one date style. */
const taskDate = (iso: string) =>
  new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });

/**
 * A measurement task's row. It lines up with the evidence rows' columns and has one action,
 * "Log value": no edit, status, assign or delete control.
 */
function MeasurementTaskRow({ task, canLog, onLog }: { task: MeasurementTask; canLog: boolean; onLog: () => void }) {
  const done = task.status === 'completed';
  const daysUntilDue = getDaysUntilDue(task.due_date);
  const isOverdue = isTaskOverdue(task);
  const dueInfo = getDueDateText(daysUntilDue, done);
  return (
    <div
      className={`tasks-row tasks-row--measurement${isOverdue ? ' tasks-row--overdue' : ''}`}
      role="listitem"
      data-testid={`measurement-row-${task.id}`}
    >
      <div className="tasks-row-main">
        <div className={`tasks-row-tick ${tickBarClass(task.status, isOverdue)}`} aria-hidden="true" />
        <div className="tasks-col-evidence">
          <span className="tasks-evidence-id">{task.ref}</span>
        </div>
        <div className="tasks-col-task">
          <AppLink to={{ kind: 'isms-metric', id: task.objective_id }} className="tasks-row-title tasks-row-title-btn">
            <span className="tasks-row-title-text">{task.title}</span>
          </AppLink>
          <span className="tasks-measure-sub">
            {task.metric_name}
            {task.first && <> · <span className="tasks-measure-first">First measurement</span></>}
          </span>
          {done && task.value_text && (
            <span className="tasks-measure-sub">
              Logged {task.value_text}{task.completed_date ? ` on ${taskDate(task.completed_date)}` : ''}
            </span>
          )}
          {!done && canLog && (
            <button type="button" className="btn-secondary tasks-measure-log" onClick={onLog}>
              Log value
            </button>
          )}
        </div>
        <div className="tasks-col-type">
          <span className={typeClass(MEASUREMENT_TYPE)}>{TASK_TYPE_LABELS[MEASUREMENT_TYPE]}</span>
        </div>
        <div className="tasks-col-status">
          <span className={statusBadgeClass(task.status)}>{STATUS_LABELS[task.status]}</span>
          {isOverdue && <span className="task-badge task-overdue-badge">OVERDUE</span>}
        </div>
        <div className="tasks-col-priority" />
        <div className="tasks-col-due">
          <div className="tasks-due-date">
            {taskDate(task.due_date)}
          </div>
          {dueInfo.text && <div className={`tasks-due-countdown ${dueInfo.cls}`}>{dueInfo.text}</div>}
        </div>
        <div className="tasks-col-created">
          <span className="tasks-created-date tasks-created-date--missing">—</span>
        </div>
        <div className="tasks-col-team">
          {task.owning_team_name || task.owner_name ? (
            <span className="task-owning-team-name">
              {task.owning_team_name ? 'Owning team: ' : 'Owner: '}
              {task.owning_team_name || task.owner_name}
            </span>
          ) : '—'}
        </div>
        <div className="tasks-col-expand" aria-hidden="true" />
      </div>
    </div>
  );
}

export const TasksPage: React.FC<TasksPageProps> = ({
  onNavigateToEvidence,
  organizationId,
  taskItem = null,
  onTaskItemChange,
}) => {
  const [view, setView] = useState<'my-tasks' | 'all-tasks'>('my-tasks');
  const [tasks, setTasks] = useState<Task[]>([]);
  // This period's measurement tasks (phase 3 B2); empty when the call fails, as on an older server.
  const [measurements, setMeasurements] = useState<MeasurementTask[]>([]);
  const [logging, setLogging] = useState<MeasurementTask | null>(null);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [taskTypeFilter, setTaskTypeFilter] = useState<string>('all');
  const [owningTeamFilter, setOwningTeamFilter] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [filtersCollapsed, setFiltersCollapsed] = useState(defaultFiltersCollapsed);

  // Column sort. null keeps the server's order (due date, soonest first)
  // until a header is clicked, so the list reads as it always has.
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir(d => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      // Newest-first is the useful default for a date column you just chose;
      // everything else reads naturally ascending.
      setSortDir(key === 'created' ? 'desc' : 'asc');
    }
  };

  // Expansion state: which row is expanded (null = none)
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Edit state (lives at page level, used in the expanded panel)
  const [editStatus, setEditStatus] = useState<string>('');
  const [editNotes, setEditNotes] = useState<string>('');

  useEffect(() => {
    loadTasks();
  }, [view, statusFilter, taskTypeFilter, organizationId]);

  // Guards against an out-of-order response after an org/filter switch: a
  // slower response for the previous org must not render under the new one.
  const loadSeq = useRef(0);

  /**
   * The measurement tasks the filters can show, never rejecting. "My Tasks" is
   * `assigned_to_me`; a measurement task is only ever not started or completed, so the
   * status filter asks for done ones (`include_done`) under All and Completed, keeps
   * Completed to the done ones, and asks for nothing under In progress or Won't do. A
   * failed call (an older server answers 404) shows no measurement row and says nothing.
   */
  const fetchMeasurements = async (): Promise<MeasurementTask[]> => {
    const typeShows = taskTypeFilter === 'all' || taskTypeFilter === MEASUREMENT_TYPE;
    const statusShows = statusFilter === 'all' || statusFilter === 'not_started' || statusFilter === 'completed';
    if (!organizationId || !typeShows || !statusShows) return [];
    try {
      const rows = await listMeasurementTasks(organizationId, {
        assignedToMe: view === 'my-tasks',
        includeDone: statusFilter !== 'not_started',
      });
      return statusFilter === 'completed' ? rows.filter(r => r.status === 'completed') : rows;
    } catch {
      return [];
    }
  };

  /** After a value is logged: the measurement rows again, without the list's loading state. */
  const refreshMeasurements = async () => {
    const seq = loadSeq.current;
    const rows = await fetchMeasurements();
    if (seq === loadSeq.current) setMeasurements(rows);
  };

  const loadTasks = async () => {
    const seq = ++loadSeq.current;
    const measured = fetchMeasurements();
    // Fail closed: without an org there is nothing this page may show.
    // (URLSearchParams would stringify undefined into "undefined" and the
    // backend would 422 the whole request.)
    if (!organizationId) {
      setTasks([]);
      setMeasurements([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      // Every filter is a query param (#788). "My Tasks" previously fetched
      // the whole organisation's tasks and applied NO user filter at all — it
      // showed everybody's work under a heading that said it was yours — and
      // the status dropdown was silently ignored on that view. `assigned_to_me`
      // is resolved from the caller's token server-side.
      const params = new URLSearchParams();
      // This page shows one organisation's tasks. Without this param the
      // endpoint returns every accessible org's tasks commingled, so a
      // consultant on several client orgs saw cross-org rows here.
      params.set('organization_id', organizationId);
      if (view === 'my-tasks') params.set('assigned_to_me', 'true');
      if (statusFilter !== 'all') params.set('status_filter', statusFilter);
      if (taskTypeFilter !== 'all') params.set('task_type', taskTypeFilter);
      // No bare-/evidence-tasks arm: the un-scoped URL is exactly the
      // cross-org fetch this page must never make again. "Measurement" is
      // never sent as a type: it names no evidence task.
      const allTasks = taskTypeFilter === MEASUREMENT_TYPE
        ? []
        : await apiClient.get(`/evidence-tasks?${params.toString()}`);

      if (seq !== loadSeq.current) return;
      setTasks(allTasks);
    } catch (error) {
      console.error('Failed to load tasks:', error);
      // A failed fetch must not leave the previous org's tasks on screen.
      if (seq === loadSeq.current) setTasks([]);
    } finally {
      const rows = await measured;
      if (seq === loadSeq.current) {
        setMeasurements(rows);
        setLoading(false);
      }
    }
  };

  /** Toggle expansion: same row collapses; a different row replaces the current. */
  const toggleExpand = useCallback((task: Task) => {
    setExpandedId(prev => {
      if (prev === task.id) {
        // Collapsing — reset edit state
        setEditStatus('');
        setEditNotes('');
        return null;
      }
      // Opening a new row — seed edit state from current task values
      setEditStatus(task.status);
      setEditNotes(task.completion_notes || '');
      return task.id;
    });
  }, []);

  const handleSave = async (taskId: string) => {
    try {
      await apiClient.patch(`/evidence-tasks/${taskId}`, {
        status: editStatus,
        completion_notes: editNotes || null,
      });
      setExpandedId(null);
      setEditStatus('');
      setEditNotes('');
      await loadTasks();
    } catch (error) {
      console.error('Failed to update task:', error);
      alert('Failed to update task');
    }
  };

  const handleCancel = () => {
    setExpandedId(null);
    setEditStatus('');
    setEditNotes('');
  };

  /* -- Owning team (#822 phase 4) ------------------------------------------
   *
   * Resolved once for the whole list, never per row: a task inherits its
   * evidence item's accountable team unless it names its own, and asking each
   * row would be the N+1 phase 3 already had to unpick.
   * --------------------------------------------------------------------- */
  const {
    ownershipFor,
    resolved: ownershipResolved,
    teams,
    error: ownershipError,
  } = useTaskTeamOwnership(organizationId, tasks);
  const { memberTypeOf } = useOrgMemberTypes(organizationId);

  /**
   * The tasks actually shown after team-ownership filter.
   *
   * ``null`` is a third state and it carries weight: the user has asked for
   * one team and we cannot yet say which tasks that team owns. It is NOT an
   * empty list — that would read as "this team has no work" — and it is
   * emphatically not the unfiltered list, which would present everybody's
   * work under a heading naming one team. Both are the same class of defect
   * as an assignment field no query consumes.
   */
  const teamFilteredTasks: Task[] | null = useMemo(() => {
    if (owningTeamFilter === 'all') return tasks;
    if (!ownershipResolved) return null;
    return tasks.filter(task => ownershipFor(task).team?.id === owningTeamFilter);
  }, [tasks, owningTeamFilter, ownershipResolved, ownershipFor]);

  /** Apply client-side search on top of the team-filtered list, then the column sort. */
  const visibleTasks: Task[] | null = useMemo(() => {
    if (teamFilteredTasks === null) return null;
    const searched = searchQuery.trim()
      ? teamFilteredTasks.filter(t => matchesSearch(t, searchQuery))
      : teamFilteredTasks;
    if (!sortKey) return searched;
    const teamNameOf: TeamNameOf = task => ownershipFor(task).team?.name ?? '';
    return sortTasks(searched, sortKey, sortDir, teamNameOf);
  }, [teamFilteredTasks, searchQuery, sortKey, sortDir, ownershipFor]);

  /** The measurement rows after the team filter and the search. */
  const visibleMeasurements = useMemo(() => measurements.filter(m =>
    (owningTeamFilter === 'all' || m.owning_team_id === owningTeamFilter) && matchesMeasurementSearch(m, searchQuery),
  ), [measurements, owningTeamFilter, searchQuery]);

  /** What the list shows: the evidence tasks with the measurement rows merged by due date, or both sorted. */
  const visibleRows: ListRow[] | null = useMemo(() => {
    if (visibleTasks === null) return null;
    if (visibleMeasurements.length === 0) return visibleTasks;
    if (!sortKey) return mergeByDueDate(visibleTasks, visibleMeasurements);
    const teamNameOf: TeamNameOf = (task: ListRow) =>
      task.measurement ? (task.measurement.owning_team_name || task.measurement.owner_name || '') : ownershipFor(task).team?.name ?? '';
    return sortTasks([...visibleTasks, ...visibleMeasurements.map(measurementAsRow)], sortKey, sortDir, teamNameOf);
  }, [visibleTasks, visibleMeasurements, sortKey, sortDir, ownershipFor]);

  const closeLogging = useCallback(() => setLogging(null), []);

  /** Drop a task the detail page has just deleted, without a refetch. */
  const handleTaskDeleted = useCallback((taskId: string) => {
    setTasks(prev => prev.filter(t => t.id !== taskId));
    setExpandedId(prev => (prev === taskId ? null : prev));
  }, []);

  const stats = useMemo(() => ({
    total: visibleRows?.length,
    not_started: visibleRows?.filter(t => t.status === 'not_started').length,
    in_progress: visibleRows?.filter(t => t.status === 'in_progress').length,
    completed: visibleRows?.filter(t => t.status === 'completed').length,
    wont_do: visibleRows?.filter(t => t.status === 'wont_do').length,
    overdue: visibleRows?.filter(isTaskOverdue).length,
  }), [visibleRows]);

  /** An unanswered count is a dash, not a zero. Zero is a claim. */
  const statValue = (value: number | undefined) => (value === undefined ? '—' : value);

  const teamOptions = useMemo(() => [
    { value: 'all', label: 'All Teams' },
    ...teams
      .filter(team => team.is_active)
      .map(team => ({ value: team.id, label: team.name })),
  ], [teams]);

  // ── Detail page (shown when taskItem is set) ──────────────────────────────
  if (taskItem) {
    return (
      <TaskDetailPage
        organizationId={organizationId}
        taskId={taskItem}
        visibleTasks={visibleTasks ?? []}
        onTaskItemChange={onTaskItemChange ?? (() => {})}
        onNavigateToEvidence={onNavigateToEvidence}
        onTaskDeleted={handleTaskDeleted}
      />
    );
  }

  /**
   * A sortable column header: click sorts, click again flips direction.
   * The rows are a list, not a grid, so sort state lives on the button
   * (aria-pressed + direction in the name) rather than on aria-sort.
   */
  const sortHeader = (key: SortKey, label: string, className: string) => {
    const active = sortKey === key;
    return (
      <div
        className={`${className} tasks-col-sortable${active ? ' tasks-col-sorted' : ''}`}
        data-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
      >
        <button
          type="button"
          className="tasks-sort-btn"
          onClick={() => handleSort(key)}
          aria-pressed={active}
          aria-label={`Sort by ${label.toLowerCase()}${active ? `, ${sortDir === 'asc' ? 'ascending' : 'descending'}` : ''}`}
        >
          {label}
          <span className="tasks-sort-indicator" aria-hidden="true">
            {active ? (sortDir === 'asc' ? '▲' : '▼') : '↕'}
          </span>
        </button>
      </div>
    );
  };

  return (
    <div className="tasks-page tasks-explorer-page">
      {/* Explorer shell: filter sidebar + main content */}
      <div className="tasks-explorer-shell">

        {/* Filter Sidebar */}
        <FilterSidebar
          collapsed={filtersCollapsed}
          onToggleCollapsed={() => setFiltersCollapsed(c => !c)}
          aria-label="Task filters"
        >
          {/* View toggle */}
          <FilterGroup label="VIEW">
            <div className="task-view-toggle">
              <button
                onClick={() => setView('my-tasks')}
                className={`task-view-btn${view === 'my-tasks' ? ' task-view-btn--active' : ''}`}
                type="button"
              >
                My Tasks
              </button>
              <button
                onClick={() => setView('all-tasks')}
                className={`task-view-btn${view === 'all-tasks' ? ' task-view-btn--active' : ''}`}
                type="button"
              >
                All Tasks
              </button>
            </div>
          </FilterGroup>

          {/* Status filter — radio: only one status active at a time */}
          <FilterGroup label="STATUS">
            <FilterRadio
              label="STATUS"
              name="task-status-filter"
              options={[
                { value: 'all', label: 'All' },
                { value: 'not_started', label: 'Not started' },
                { value: 'in_progress', label: 'In progress' },
                { value: 'completed', label: 'Completed' },
              ]}
              value={statusFilter}
              onChange={setStatusFilter}
            />
          </FilterGroup>

          {/* Task type filter — radio: only one type active at a time */}
          <FilterGroup label="TASK TYPE">
            <FilterRadio
              label="TASK TYPE"
              name="task-type-filter"
              options={[
                { value: 'all', label: 'All types' },
                { value: 'feasibility', label: 'Feasibility' },
                { value: 'setup', label: 'Setup' },
                { value: 'collection', label: 'Collection' },
                { value: 'review', label: 'Review' },
                { value: 'documentation', label: 'Documentation' },
                { value: 'issue', label: 'Issue' },
                { value: MEASUREMENT_TYPE, label: 'Measurement' },
              ]}
              value={taskTypeFilter}
              onChange={setTaskTypeFilter}
            />
          </FilterGroup>

          {/* Owning team (#822 phase 4). Includes tasks that INHERIT the team
              from their evidence item, which is most of them — a filter that
              matched only explicit overrides would answer a question nobody
              asked and report a team as owning almost nothing. */}
          <FilterGroup label="OWNING TEAM">
            <div className="explorer-filter-select-wrap">
              <div className="explorer-filter-select-chrome">
                <select
                  id="tasks-owning-team-filter"
                  value={owningTeamFilter}
                  onChange={(e) => setOwningTeamFilter(e.target.value)}
                  className="explorer-filter-select"
                  aria-label="Filter tasks by owning team"
                >
                  {teamOptions.map(opt => (
                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                  ))}
                </select>
                <svg
                  className="explorer-filter-select-arrow"
                  width="12"
                  height="12"
                  viewBox="0 0 12 12"
                  fill="none"
                  aria-hidden="true"
                >
                  <path d="M2 4l4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
              </div>
              <div className="task-team-filter-hint">
                Includes tasks inheriting the team from their evidence item.
              </div>
            </div>
          </FilterGroup>
        </FilterSidebar>

        {/* Main content column */}
        <div className="tasks-explorer-main">

          {/* ListToolbar: search + count */}
          <ListToolbar
            search={searchQuery}
            onSearchChange={setSearchQuery}
            searchPlaceholder="Search tasks — title, evidence id, description…"
            count={
              visibleRows !== null
                ? `${visibleRows.length} task${visibleRows.length !== 1 ? 's' : ''}`
                : undefined
            }
          />

          {/* Stats strip */}
          <div className="tasks-stats-strip">
            <div className="tasks-stat-item" data-testid="task-stat-total">
              <span className="tasks-stat-value tasks-stat-total">{statValue(stats.total)}</span>
              <span className="tasks-stat-label">TOTAL</span>
            </div>
            <div className="tasks-stat-item" data-testid="task-stat-not-started">
              <span className="tasks-stat-value tasks-stat-not-started">{statValue(stats.not_started)}</span>
              <span className="tasks-stat-label">NOT STARTED</span>
            </div>
            <div className="tasks-stat-item" data-testid="task-stat-in-progress">
              <span className="tasks-stat-value tasks-stat-in-progress">{statValue(stats.in_progress)}</span>
              <span className="tasks-stat-label">IN PROGRESS</span>
            </div>
            <div className="tasks-stat-item" data-testid="task-stat-overdue">
              <span className="tasks-stat-value tasks-stat-overdue">{statValue(stats.overdue)}</span>
              <span className="tasks-stat-label">OVERDUE</span>
            </div>
            <div className="tasks-stat-item" data-testid="task-stat-completed">
              <span className="tasks-stat-value tasks-stat-completed">{statValue(stats.completed)}</span>
              <span className="tasks-stat-label">COMPLETED</span>
            </div>
          </div>

          {/* Team ownership error banner */}
          {ownershipError && owningTeamFilter !== 'all' && (
            <div className="error-banner">
              <span>Could not read team ownership: {ownershipError}</span>
            </div>
          )}

          {/* Column header row — every named column sorts */}
          <div className="tasks-col-header">
            <div className="tasks-col-tick" aria-hidden="true" />
            {sortHeader('evidence', 'CONTROL', 'tasks-col-evidence')}
            {sortHeader('title', 'TASK', 'tasks-col-task')}
            {sortHeader('type', 'TYPE', 'tasks-col-type')}
            {sortHeader('status', 'STATUS', 'tasks-col-status')}
            {sortHeader('priority', 'PRIORITY', 'tasks-col-priority')}
            {sortHeader('due', 'DUE', 'tasks-col-due')}
            {sortHeader('created', 'CREATED', 'tasks-col-created')}
            {sortHeader('team', 'TEAM', 'tasks-col-team')}
            <div className="tasks-col-expand" aria-hidden="true" />
          </div>

          {/* Task rows */}
          {loading ? (
            <div className="tasks-loading">Loading tasks...</div>
          ) : visibleRows === null ? (
            /* A team filter is active and ownership has not been resolved. Neither
               the unfiltered list nor an empty one is honest here — the first
               shows other teams' work under this team's name, the second says
               this team has none. Say what is actually true instead. */
            <div className="tasks-loading">Resolving team ownership…</div>
          ) : visibleRows.length === 0 ? (
            <div className="tasks-empty-state">
              <div className="tasks-empty-icon">📋</div>
              <h3>No Tasks Found</h3>
              <p>
                {owningTeamFilter !== 'all'
                  ? 'No tasks are owned by that team — directly or inherited from their evidence item.'
                  : searchQuery.trim()
                    ? 'No tasks match your search. Try different keywords.'
                    : statusFilter !== 'all'
                      ? 'Try changing the filter'
                      : 'Tasks will appear here when evidence collection is scheduled'}
              </p>
            </div>
          ) : (
            <WithEditorRole organizationId={organizationId} ask={visibleMeasurements.length > 0}>
            {canLog => (
            <div className="tasks-row-list" role="list">
              {visibleRows.map((task) => {
                if (task.measurement) {
                  const m = task.measurement;
                  return <MeasurementTaskRow key={task.id} task={m} canLog={canLog} onLog={() => setLogging(m)} />;
                }
                const daysUntilDue = getDaysUntilDue(task.due_date);
                const isCompleted = isClosedStatus(task.status);
                const isOverdue = isTaskOverdue(task);
                const isExpanded = expandedId === task.id;
                const dueInfo = getDueDateText(daysUntilDue, isCompleted);
                const ownership = ownershipFor(task);

                return (
                  <div
                    key={task.id}
                    className={`tasks-row${isOverdue ? ' tasks-row--overdue' : ''}${isExpanded ? ' tasks-row--expanded' : ''}`}
                    role="listitem"
                  >
                    {/* Main row */}
                    <div className="tasks-row-main">
                      {/* Status-colored left tick bar */}
                      <div
                        className={`tasks-row-tick ${tickBarClass(task.status, isOverdue)}`}
                        aria-hidden="true"
                      />

                      {/* Evidence/Control ID */}
                      <div className="tasks-col-evidence">
                        <span
                          className="tasks-evidence-id"
                          onClick={() => onNavigateToEvidence(task.evidence_id)}
                          role="button"
                          tabIndex={0}
                          onKeyDown={e => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault();
                              onNavigateToEvidence(task.evidence_id);
                            }
                          }}
                          title={`Navigate to evidence ${task.evidence_id}`}
                        >
                          {task.evidence_id}
                        </span>
                      </div>

                      {/* Task title + evidence link */}
                      <div className="tasks-col-task">
                        {/* Title is now a button — click opens TaskDetailPage */}
                        <button
                          className="tasks-row-title tasks-row-title-btn"
                          onClick={() => onTaskItemChange?.(task.id)}
                          type="button"
                          aria-label={task.title || 'Untitled Task'}
                        >
                          <span className="tasks-row-title-text">{task.title || 'Untitled Task'}</span>
                          {task.completion_notes && (
                            <span
                              className="tasks-notes-indicator"
                              title={task.completion_notes}
                              aria-label="Has completion notes"
                            >
                              📝
                            </span>
                          )}
                        </button>
                        <button
                          className="tasks-evidence-link"
                          onClick={() => onNavigateToEvidence(task.evidence_id)}
                          type="button"
                        >
                          Evidence: {task.evidence_id} →
                        </button>
                      </div>

                      {/* Type badge */}
                      <div className="tasks-col-type">
                        <span className={typeClass(task.task_type)}>
                          {TASK_TYPE_LABELS[task.task_type] || task.task_type}
                        </span>
                      </div>

                      {/* Status badge */}
                      <div className="tasks-col-status">
                        <span className={statusBadgeClass(task.status)}>
                          {STATUS_LABELS[task.status] || task.status.replace('_', ' ')}
                        </span>
                        {isOverdue && (
                          <span className="task-badge task-overdue-badge">OVERDUE</span>
                        )}
                      </div>

                      {/* Priority */}
                      <div className="tasks-col-priority">
                        <span className={`tasks-priority-text ${priorityClass(task.priority)}`}>
                          {PRIORITY_LABELS[task.priority] || task.priority}
                        </span>
                      </div>

                      {/* No assignee column. The all-tasks view used to carry one
                          beside TEAM, which made the same row answer "who has
                          this" twice and disagree with itself on every task whose
                          individual had moved on. TEAM is the answer. */}

                      {/* Due date + days remaining */}
                      <div className="tasks-col-due">
                        {task.due_date && (
                          <>
                            <div className="tasks-due-date">
                              {new Date(task.due_date).toLocaleDateString('en-US', {
                                month: 'short',
                                day: 'numeric',
                                year: 'numeric',
                              })}
                            </div>
                            {dueInfo.text && (
                              <div className={`tasks-due-countdown ${dueInfo.cls}`}>
                                {dueInfo.text}
                              </div>
                            )}
                          </>
                        )}
                      </div>

                      {/* Created date */}
                      <div className="tasks-col-created">
                        {task.created_at ? (
                          <div className="tasks-created-date" title={new Date(task.created_at).toLocaleString()}>
                            {new Date(task.created_at).toLocaleDateString('en-US', {
                              month: 'short',
                              day: 'numeric',
                              year: 'numeric',
                            })}
                          </div>
                        ) : (
                          <span className="tasks-created-date tasks-created-date--missing">—</span>
                        )}
                      </div>

                      {/* Team — includes inherited/override pill and warnings
                          so the owning-team column is never silent (#822) */}
                      <div className="tasks-col-team">
                        <TaskOwningTeamBadge
                          ownership={ownership}
                          memberType={memberTypeOf(ownership.team?.person_user_id)}
                          resolved={ownershipResolved}
                        />
                      </div>

                      {/* Expand/collapse chevron */}
                      <div className="tasks-col-expand">
                        <button
                          type="button"
                          className={`tasks-expand-btn${isExpanded ? ' tasks-expand-btn--open' : ''}`}
                          aria-label={isExpanded ? 'Collapse row' : 'Expand row'}
                          aria-expanded={isExpanded}
                          onClick={() => toggleExpand(task)}
                        >
                          <svg
                            width="16"
                            height="16"
                            viewBox="0 0 16 16"
                            fill="none"
                            aria-hidden="true"
                          >
                            <path
                              d={isExpanded ? 'M4 10l4-4 4 4' : 'M4 6l4 4 4-4'}
                              stroke="currentColor"
                              strokeWidth="1.5"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            />
                          </svg>
                        </button>
                      </div>
                    </div>

                    {/* Expanded panel: inline edit form + comment thread */}
                    {isExpanded && (
                      <div className="tasks-row-expansion">
                        <div className="tasks-expansion-inner">

                          {/* Inline edit form (same PATCH endpoint as before) */}
                          <div className="tasks-edit-panel">
                            <h4 className="tasks-edit-heading">
                              Edit: {task.title || task.evidence_id}
                            </h4>

                            {/* Optional read-only context: system + method + frequency */}
                            {(task.collecting_system || task.method_of_collection || task.frequency || task.owner || task.description) && (
                              <div className="tasks-edit-context">
                                {task.description && (
                                  <div className="tasks-edit-context-row">
                                    <span className="tasks-edit-context-label">Description:</span>
                                    <RichText text={task.description} as="div" />
                                  </div>
                                )}
                                {task.owner && (
                                  <div className="tasks-edit-context-row">
                                    <span className="tasks-edit-context-label">Owner:</span>
                                    <span>{task.owner}</span>
                                  </div>
                                )}
                                {task.frequency && (
                                  <div className="tasks-edit-context-row">
                                    <span className="tasks-edit-context-label">Frequency:</span>
                                    <span>{frequencyLabel(task.frequency)}</span>
                                  </div>
                                )}
                                {task.collecting_system && (
                                  <div className="tasks-edit-context-row">
                                    <span className="tasks-edit-context-label">System:</span>
                                    <span>{task.collecting_system}</span>
                                  </div>
                                )}
                                {task.method_of_collection && (
                                  <div className="tasks-edit-context-row">
                                    <span className="tasks-edit-context-label">Method:</span>
                                    <span>{task.method_of_collection}</span>
                                  </div>
                                )}
                              </div>
                            )}

                            {/* Full team ownership badge in the expansion */}
                            <div className="tasks-edit-team-row">
                              <TaskOwningTeamBadge
                                ownership={ownership}
                                memberType={memberTypeOf(ownership.team?.person_user_id)}
                                resolved={ownershipResolved}
                              />
                            </div>

                            {/* Status select */}
                            <div className="tasks-edit-field-row">
                              <label htmlFor={`task-status-${task.id}`} className="tasks-edit-label">
                                Status:
                              </label>
                              <select
                                id={`task-status-${task.id}`}
                                value={editStatus}
                                onChange={e => setEditStatus(e.target.value)}
                                className="task-edit-select"
                                aria-label="Status"
                              >
                                <option value="not_started">Not Started</option>
                                <option value="in_progress">In Progress</option>
                                <option value="completed">Completed</option>
                                <option value="wont_do">Won&rsquo;t Do</option>
                              </select>
                            </div>

                            {/* Completion notes */}
                            <div className="tasks-edit-field-row">
                              <label htmlFor={`task-notes-${task.id}`} className="tasks-edit-label">
                                Notes:
                              </label>
                              <textarea
                                id={`task-notes-${task.id}`}
                                value={editNotes}
                                onChange={e => setEditNotes(e.target.value)}
                                placeholder="Add completion notes..."
                                className="task-edit-textarea"
                              />
                            </div>

                            {/* Save / Cancel */}
                            <div className="task-edit-actions">
                              <button
                                type="button"
                                onClick={() => handleSave(task.id)}
                                className="task-edit-save"
                              >
                                Save Changes
                              </button>
                              <button
                                type="button"
                                onClick={handleCancel}
                                className="task-edit-cancel"
                              >
                                Cancel
                              </button>
                            </div>
                          </div>

                          {/* Comment thread */}
                          <div className="tasks-comments-panel">
                            <ModernCommentThread
                              commentableType="task"
                              commentableId={task.id}
                              organizationId={organizationId}
                            />
                          </div>

                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            )}
            </WithEditorRole>
          )}
        </div>
      </div>
      {logging && (
        <MeasurementLogValue
          organizationId={organizationId}
          task={logging}
          onClose={closeLogging}
          onLogged={() => void refreshMeasurements()}
        />
      )}
    </div>
  );
};

export default TasksPage;
