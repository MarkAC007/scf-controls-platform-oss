import { useMemo, useState } from 'react'
import { useAttestStage, useImportJourney, useJourney } from '../../hooks/useJourney'
import { useHasOrgRole } from '../../hooks/useHasOrgRole'
import { deriveJourneyCompletion } from './journeyCompletion'
import type { JourneyStage, JourneyStageState } from '../../data/apiClient'

interface Props {
  organizationId: string
  /** Sends the viewer to the work queue rather than growing a second to-do list here. */
  onNavigateToTasks?: () => void
}

/**
 * The organisation's journey: an ordered path of stages, what this one asks
 * for, and what the next one will.
 *
 * Two rules shape everything on this screen.
 *
 * A stone never lights itself. The platform evaluates the mechanical
 * preconditions and shows them, but only a named person moves a stage to
 * passed. The green ticks below are information for that person, not a
 * substitute for them.
 *
 * An organisation with nobody helping them still sees the whole road. The
 * unlit state is "nobody is walking this with you yet", not "you cannot see
 * this". That distinction is the difference between a map and a paywall, and
 * it is why the API renders a template preview instead of returning a 404.
 */
export default function JourneyPage({ organizationId, onNavigateToTasks }: Props) {
  const { data, isLoading, isError, error } = useJourney(organizationId)
  const canAttest = useHasOrgRole(organizationId, 'editor')
  const isAdmin = useHasOrgRole(organizationId, 'admin')
  const attest = useAttestStage(organizationId)
  const importJourney = useImportJourney(organizationId)

  const [expandedKey, setExpandedKey] = useState<string | null>(null)
  const [attestingId, setAttestingId] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const [conditional, setConditional] = useState(false)
  const [targetDate, setTargetDate] = useState('')

  const stages = data?.stages ?? []
  const currentIndex = useMemo(
    () => stages.findIndex(s => s.key === data?.current_stage_key),
    [stages, data?.current_stage_key],
  )
  const current = currentIndex >= 0 ? stages[currentIndex] : null
  // No current stage means no next stage. Falling back to `stages[0]` here told
  // a finished journey its next step was the first one, which is where the
  // whole screen started lying.
  const next = currentIndex >= 0 ? stages[currentIndex + 1] ?? null : null

  const completion = useMemo(
    () =>
      deriveJourneyCompletion(
        stages,
        { provisioned: data?.provisioned ?? false, activated: data?.activated ?? false },
        data?.current_stage_key ?? null,
      ),
    [stages, data?.provisioned, data?.activated, data?.current_stage_key],
  )

  if (isLoading) {
    return <div className="journey-page"><div className="journey-loading">Loading the journey…</div></div>
  }

  if (isError) {
    return (
      <div className="journey-page">
        <div className="journey-empty-note">
          Could not load the journey: {(error as Error)?.message ?? 'unknown error'}
        </div>
      </div>
    )
  }

  if (!data) return null

  const engaged = !!data.practitioner
  const unlit = !data.provisioned || !data.activated

  const submitAttestation = async (stage: JourneyStage) => {
    if (!stage.id) return
    await attest.mutateAsync({
      stageId: stage.id,
      note: note.trim() || undefined,
      conditional,
      target_date: conditional && targetDate ? targetDate : undefined,
    })
    setAttestingId(null)
    setNote('')
    setConditional(false)
    setTargetDate('')
  }

  return (
    <div className="journey-page">
      <header className="journey-header">
        <div>
          <h1 className="journey-title">{data.name ?? 'Your compliance journey'}</h1>
          <p className="journey-subtitle">
            {/*
              * Four states, not two. "Not started" is now reachable only from
              * an absent *journey* — never from an absent current stage, which
              * is also what a finished path reports.
              */}
            {data.provisioned && data.activated && current ? (
              <>Stage {currentIndex + 1} of {stages.length} · <strong>{current.title}</strong></>
            ) : completion.complete ? (
              <>
                All {stages.length} stages attested
                {completion.conditionalStages.length > 0 && (
                  <> · {completion.conditionalStages.length} with conditions outstanding</>
                )}
                {completion.regressedStages.length > 0 && (
                  <>
                    {' '}· {completion.regressedStages.length} signed{' '}
                    {completion.regressedStages.length === 1 ? 'stage' : 'stages'} no longer passing
                  </>
                )}
              </>
            ) : completion.noActiveStage ? (
              <>{stages.length} stages · no stage is currently active</>
            ) : (
              <>{stages.length} stages · not started</>
            )}
            {data.practitioner?.company_name && <> · guided by {data.practitioner.company_name}</>}
          </p>
        </div>
        {isAdmin && !data.provisioned && (
          <button
            className="btn btn-primary"
            disabled={importJourney.isPending}
            onClick={() => importJourney.mutate({ template_key: data.template_key ?? undefined, activate: true })}
          >
            {importJourney.isPending ? 'Starting…' : 'Start this journey'}
          </button>
        )}
      </header>

      {unlit && (
        /*
         * The honest empty state. It says what is true — nobody is walking this
         * with you — and it does not pretend the road is unavailable. Everything
         * below stays readable; only the progress is absent.
         */
        <div className="journey-unlit-note" role="note">
          <strong>This is the road, not your progress yet.</strong>{' '}
          {engaged
            ? <>Your practitioner has not started the journey in the platform. Every stage below is visible so you can see what is coming.</>
            : <>No practitioner is engaged with this organisation, so no stage is in progress. You can read every stage below and walk it yourself — a practitioner tailors the order and decides when each one is finished.</>}
        </div>
      )}

      <ol className={`journey-path${unlit ? ' journey-path-unlit' : ''}`}>
        {stages.map((stage, index) => (
          <li
            key={stage.key}
            className={`journey-stone journey-stone-${stage.state}${stage.key === data.current_stage_key ? ' journey-stone-current' : ''}`}
          >
            <button
              type="button"
              className="journey-stone-button"
              aria-expanded={expandedKey === stage.key}
              aria-current={stage.key === data.current_stage_key ? 'step' : undefined}
              onClick={() => setExpandedKey(expandedKey === stage.key ? null : stage.key)}
            >
              <span className="journey-stone-marker" aria-hidden="true">{stoneGlyph(stage.state)}</span>
              <span className="journey-stone-ordinal">{index + 1}</span>
              <span className="journey-stone-title">{stage.title}</span>
              {/* State is spelled out, never colour alone. */}
              <span className="journey-stone-state">{stateLabel(stage.state, stage.key === data.current_stage_key)}</span>
              {stage.preconditions.total_count > 0 && (
                <span className="journey-stone-pre">
                  {stage.preconditions.met_count}/{stage.preconditions.total_count} checks
                </span>
              )}
            </button>

            {expandedKey === stage.key && (
              <div className="journey-stone-detail">
                {stage.summary && <p className="journey-stone-summary">{stage.summary}</p>}
                {stage.expect_next && (
                  <p className="journey-stone-expect">
                    <span className="journey-label">What to expect</span>
                    {stage.expect_next}
                  </p>
                )}

                {stage.preconditions.checks.length > 0 && (
                  <ul className="journey-check-list">
                    {stage.preconditions.checks.map((c, i) => (
                      <li key={i} className={`journey-check journey-check-${c.met === true ? 'met' : c.met === null ? 'unknown' : 'unmet'}`}>
                        <span className="journey-check-glyph" aria-hidden="true">
                          {c.met === true ? '✓' : c.met === null ? '?' : '·'}
                        </span>
                        <span className="journey-check-label">{c.label}</span>
                        <span className="journey-check-detail">{c.detail}</span>
                      </li>
                    ))}
                  </ul>
                )}

                {stage.attested_at && (
                  <p className="journey-attested">
                    Signed by {stage.attested_by_name ?? 'an unnamed user'} · {new Date(stage.attested_at).toLocaleDateString()}
                    {stage.target_date && <> · outstanding items due {new Date(stage.target_date).toLocaleDateString()}</>}
                    {stage.attestation_note && <><br /><em>{stage.attestation_note}</em></>}
                  </p>
                )}

                {canAttest && stage.id && !stage.attested_at &&
                  (stage.state === 'active' || stage.state === 'awaiting_attestation') && (
                  attestingId === stage.id ? (
                    <div className="journey-attest-form">
                      <label className="journey-label" htmlFor={`note-${stage.id}`}>
                        What did you check, and who was in the room?
                      </label>
                      <textarea
                        id={`note-${stage.id}`}
                        className="journey-attest-note"
                        rows={3}
                        value={note}
                        onChange={e => setNote(e.target.value)}
                        placeholder="The owners walked me through their controls without prompting. Second attendee: …"
                      />
                      <label className="journey-attest-conditional">
                        <input
                          type="checkbox"
                          checked={conditional}
                          onChange={e => setConditional(e.target.checked)}
                        />
                        Pass with items still outstanding
                      </label>
                      {conditional && (
                        <label className="journey-label">
                          Outstanding items due
                          <input
                            type="date"
                            value={targetDate}
                            onChange={e => setTargetDate(e.target.value)}
                          />
                        </label>
                      )}
                      <div className="journey-attest-actions">
                        <button
                          className="btn btn-primary"
                          disabled={attest.isPending || (conditional && !targetDate)}
                          onClick={() => submitAttestation(stage)}
                        >
                          {attest.isPending ? 'Signing…' : 'Sign and pass this stage'}
                        </button>
                        <button className="btn btn-secondary" onClick={() => setAttestingId(null)}>Cancel</button>
                      </div>
                      <p className="journey-attest-caveat">
                        Your name and the date are recorded against this stage. The checks above
                        inform this decision; they do not make it.
                      </p>
                    </div>
                  ) : (
                    <button className="btn btn-primary" onClick={() => setAttestingId(stage.id!)}>
                      Attest this stage
                    </button>
                  )
                )}
              </div>
            )}
          </li>
        ))}
      </ol>

      <div className="journey-panels">
        <section className="journey-focus">
          <h2 className="journey-panel-title">Today</h2>
          {!data.activated ? (
            <p className="journey-empty-note">Nothing is in progress yet.</p>
          ) : completion.complete ? (
            <>
              {completion.conditionalStages.length === 0 &&
                completion.regressedStages.length === 0 && (
                <p className="journey-empty-note">
                  Every stage on this path has been attested and every check still passes.
                  Nothing on the Journey is waiting on you.
                </p>
              )}

              {completion.conditionalStages.length > 0 && (
                <>
                  <p className="journey-empty-note">
                    Every stage has been attested. {completion.conditionalStages.length} passed
                    with conditions that are still open.
                  </p>
                  <ul className="journey-focus-list">
                    {completion.conditionalStages.map(stage => (
                      <li key={stage.key} className="journey-focus-item">
                        <span className="journey-focus-label">{stage.title}</span>
                        <span className="journey-focus-detail">
                          {stage.target_date
                            ? <>outstanding items due {new Date(stage.target_date).toLocaleDateString()}</>
                            : <>outstanding items, no due date recorded</>}
                          {stage.preconditions.total_count > 0 && (
                            <>
                              {' '}· {stage.preconditions.met_count}/
                              {stage.preconditions.total_count} checks met
                            </>
                          )}
                        </span>
                      </li>
                    ))}
                  </ul>
                  <p className="journey-attest-caveat">
                    These signatures stand as recorded. The conditions are the work that remains.
                  </p>
                </>
              )}

              {completion.regressedStages.length > 0 && (
                <>
                  <p className="journey-label">Signed, but no longer passing</p>
                  <ul className="journey-focus-list">
                    {completion.regressedStages.map(stage => (
                      <li key={stage.key} className="journey-focus-item">
                        <span className="journey-focus-label">{stage.title}</span>
                        <span className="journey-focus-detail">
                          signed complete on{' '}
                          {new Date(stage.attested_at as string).toLocaleDateString()}, now{' '}
                          {stage.preconditions.met_count} of {stage.preconditions.total_count}{' '}
                          checks.
                        </span>
                      </li>
                    ))}
                  </ul>
                  <p className="journey-attest-caveat">
                    The signature stands as recorded. This is today's evaluation of the same
                    checks; expanding the stage shows which ones changed.
                  </p>
                </>
              )}
            </>
          ) : !current ? (
            <p className="journey-empty-note">
              No stage is in progress. {completion.unsignedCount} of {stages.length} stages are
              still unsigned.
            </p>
          ) : data.focus.length === 0 ? (
            <p className="journey-empty-note">
              Every check on this stage is met. It is waiting on an attestation.
            </p>
          ) : (
            <ul className="journey-focus-list">
              {data.focus.map((c, i) => (
                <li key={i} className="journey-focus-item">
                  <span className="journey-focus-label">{c.label}</span>
                  <span className="journey-focus-detail">{c.detail}</span>
                </li>
              ))}
            </ul>
          )}
          {onNavigateToTasks && (
            <button className="btn btn-secondary journey-focus-link" onClick={onNavigateToTasks}>
              Open the full work queue →
            </button>
          )}
        </section>

        <section className="journey-next">
          <h2 className="journey-panel-title">What comes next</h2>
          {completion.complete ? (
            <>
              {completion.conditionalStages.length > 0 && (
                <p className="journey-next-body">
                  Closing the outstanding conditions above.
                  {completion.nextDue && (
                    <> The earliest is due {new Date(completion.nextDue).toLocaleDateString()}.</>
                  )}
                </p>
              )}
              {completion.regressedStages.length > 0 && (
                <p className="journey-next-body">
                  Restoring the checks on the{' '}
                  {completion.regressedStages.length === 1 ? 'stage' : 'stages'} listed above,
                  then re-attesting if the signer's judgement has changed.
                </p>
              )}
              {completion.conditionalStages.length === 0 &&
                completion.regressedStages.length === 0 &&
                completion.lastSigned && (
                <p className="journey-next-body">
                  This journey is complete. The last stage signed was{' '}
                  {completion.lastSigned.title}, on{' '}
                  {new Date(completion.lastSigned.attested_at as string).toLocaleDateString()}.
                </p>
              )}
            </>
          ) : next ? (
            <>
              <h3 className="journey-next-title">{next.title}</h3>
              {next.expect_next && <p className="journey-next-body">{next.expect_next}</p>}
            </>
          ) : (
            /* Reachable again now that an absent current stage no longer wraps to stages[0]. */
            <p className="journey-empty-note">This is the last stage on the path.</p>
          )}
        </section>
      </div>
    </div>
  )
}

/** A shape per state, so the path is legible without colour. */
function stoneGlyph(state: JourneyStageState): string {
  switch (state) {
    case 'passed': return '✓'
    case 'passed_conditional': return '✓!'
    case 'active': return '▶'
    case 'awaiting_attestation': return '◆'
    default: return '○'
  }
}

function stateLabel(state: JourneyStageState, isCurrent: boolean): string {
  switch (state) {
    case 'passed': return 'Complete'
    case 'passed_conditional': return 'Passed with conditions'
    case 'active': return isCurrent ? 'You are here' : 'In progress'
    case 'awaiting_attestation': return 'Awaiting sign-off'
    default: return 'Not started'
  }
}
