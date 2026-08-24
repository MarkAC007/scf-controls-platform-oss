/**
 * AccountableOwnerTypeFilter — narrow a list to the work whose accountable
 * team is led by an external contractor, or by permanent staff (#822 phase 2).
 *
 * "Who is answerable for this, and are they on our payroll?" is the question a
 * contractor label exists to let somebody ask across a whole framework rather
 * than one row at a time. A ``member_type`` column that only ever renders as a
 * badge beside a name answers it once per click; this answers it once.
 *
 * Follows ``TeamListFilters`` rather than inventing a second idiom: a
 * ``filter-select`` with an "all" sentinel, owned by the list, pushed to the
 * server as a query parameter. Deliberately simpler than that component in one
 * respect — it has no catalogue to load, because its two options are the two
 * legal values of the column and are known at build time. A request would buy
 * nothing and would give the control a loading state it does not need.
 *
 * Filtering by it confers nothing and is not a permission check, exactly like
 * filtering by domain or by team: it says "show me this slice".
 */
import type { MemberType } from '../types'

/** Sentinel for "do not narrow on this". Matches ``TeamListFilters``'s ``ALL``. */
export const ALL_OWNER_TYPES = 'all'

export type AccountableOwnerTypeValue = typeof ALL_OWNER_TYPES | MemberType

const OPTIONS: { value: AccountableOwnerTypeValue; label: string }[] = [
  { value: ALL_OWNER_TYPES, label: 'All Owner Types' },
  { value: 'external_contractor', label: 'Contractor-owned' },
  { value: 'internal', label: 'Internally owned' },
]

interface AccountableOwnerTypeFilterProps {
  value: AccountableOwnerTypeValue
  onChange: (value: AccountableOwnerTypeValue) => void
  className?: string
}

export default function AccountableOwnerTypeFilter({
  value,
  onChange,
  className,
}: AccountableOwnerTypeFilterProps) {
  return (
    <select
      aria-label="Filter by accountable owner type"
      title="Show only work whose accountable team is led by a contractor, or by internal staff"
      className={className ?? 'filter-select'}
      value={value}
      onChange={e => onChange(e.target.value as AccountableOwnerTypeValue)}
    >
      {OPTIONS.map(option => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  )
}
