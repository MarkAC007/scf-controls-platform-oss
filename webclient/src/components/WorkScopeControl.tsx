/**
 * WorkScopeControl — the header's "Showing: Everything / My teams" switch.
 *
 * A real radiogroup rather than two divs that happen to look clickable: the
 * two options are mutually exclusive positions of one control, which is what a
 * radiogroup means, and it arrows between them and announces "2 of 2" without
 * any extra wiring.
 *
 * Deliberately NOT built on ``.scope-toggle-btn``: that class is defined twice
 * in styles.css and hardcodes an rgba() hover in a sheet that is otherwise
 * driven by :root custom properties, so it renders a near-white hover in dark
 * themes. Every colour below is a token.
 */
import { useWorkScope, type WorkScope } from '../contexts/WorkScopeContext'

const OPTIONS: { value: WorkScope; label: string }[] = [
  { value: 'everything', label: 'Everything' },
  { value: 'my_teams', label: 'My teams' },
]

export default function WorkScopeControl() {
  const { scope, setScope } = useWorkScope()

  return (
    <div className="work-scope-control">
      <span className="work-scope-label" id="work-scope-label">
        Showing:
      </span>
      <div
        className="work-scope-options"
        role="radiogroup"
        aria-labelledby="work-scope-label"
      >
        {OPTIONS.map(({ value, label }) => {
          const selected = scope === value
          return (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={selected}
              // Keyboard reaches the selected option only, then arrows within
              // the group — the standard radiogroup tab behaviour.
              tabIndex={selected ? 0 : -1}
              className={`work-scope-option${selected ? ' work-scope-option--selected' : ''}`}
              onClick={() => setScope(value)}
              onKeyDown={(e) => {
                if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
                  e.preventDefault()
                  setScope('my_teams')
                } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
                  e.preventDefault()
                  setScope('everything')
                }
              }}
            >
              {label}
            </button>
          )
        })}
      </div>
    </div>
  )
}
