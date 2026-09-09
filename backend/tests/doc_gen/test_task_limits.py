"""Time limits, TTLs and interruption behaviour for ``doc_gen.generate``.

These tests exist because of a production incident: a batch was SIGKILLed by
Celery's global 600s hard limit after nine of twenty-two documents, and the
Redis status key was left saying ``running`` forever. Three things were wrong
together — no per-task limit, a broad ``except Exception`` inside the loop that
swallowed ``SoftTimeLimitExceeded``, and TTLs shorter than any timeout large
enough to fix the first.

The constant assertions below are the cheap half. The half that matters is
behavioural: that a soft limit STOPS the loop rather than letting it run on into
an uncatchable SIGKILL, and that an ordinary refusal still does not. Those two
paths are one keyword apart in the source and asserting the constants alone
would not tell them apart.
"""
import inspect
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from celery.exceptions import SoftTimeLimitExceeded

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import tasks_doc_gen  # noqa: E402
from tasks_doc_gen import (  # noqa: E402
    DOCGEN_LOCK_TTL,
    DOCGEN_SOFT_TIME_LIMIT,
    DOCGEN_STATUS_TTL,
    DOCGEN_TIME_LIMIT,
    generate_documents_task,
)

# Measured on a real deployment, ``doc_gen context assembled`` to
# ``doc_gen created``, 14 samples: min 46s, median 67s, p95 81s.
OBSERVED_P95_SECONDS_PER_DOCUMENT = 81

# 34 catalogue domains x 3 domain-scoped generators + 6 organisation-level.
FULL_CATALOGUE_DOCUMENTS = 108


# ---------------------------------------------------------------------------
# Limits and TTLs
# ---------------------------------------------------------------------------


def test_task_declares_its_own_time_limits():
    """The task must not inherit the global 600s/540s.

    That inheritance is the original defect: at a 67s median it capped a batch
    at roughly eight documents.
    """
    assert generate_documents_task.time_limit == DOCGEN_TIME_LIMIT
    assert generate_documents_task.soft_time_limit == DOCGEN_SOFT_TIME_LIMIT


def test_hard_limit_covers_a_worst_case_full_catalogue_run():
    worst_case = FULL_CATALOGUE_DOCUMENTS * OBSERVED_P95_SECONDS_PER_DOCUMENT
    assert DOCGEN_TIME_LIMIT > worst_case, (
        f"hard limit {DOCGEN_TIME_LIMIT}s does not cover a p95 full-catalogue "
        f"run of {worst_case}s"
    )


def test_soft_limit_leaves_a_usable_grace_window():
    """The handler has to roll back and report a batch of up to 108 results."""
    assert DOCGEN_SOFT_TIME_LIMIT < DOCGEN_TIME_LIMIT
    assert DOCGEN_TIME_LIMIT - DOCGEN_SOFT_TIME_LIMIT >= 300


@pytest.mark.parametrize(
    "name,ttl",
    [("DOCGEN_STATUS_TTL", DOCGEN_STATUS_TTL), ("DOCGEN_LOCK_TTL", DOCGEN_LOCK_TTL)],
)
def test_ttls_outlive_the_longest_possible_run(name, ttl):
    """A TTL shorter than the timeout is a bug the timeout itself creates.

    An expired lock lets a second generation start for the same organisation;
    an expired status key blanks the progress bar and, because the endpoint
    then reports ``idle``, stops the client polling entirely.
    """
    assert ttl > DOCGEN_TIME_LIMIT, f"{name}={ttl} does not outlive {DOCGEN_TIME_LIMIT}"


# ---------------------------------------------------------------------------
# Interruption behaviour
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


class _FakeResult:
    action = "created"

    def __init__(self, generator, domain_id):
        self.generator = generator
        self.domain_id = domain_id

    def to_dict(self):
        return {
            "generator": self.generator,
            "domain_id": self.domain_id or "",
            "action": self.action,
        }


@pytest.fixture
def harness(monkeypatch):
    """Run the real task body against fake Redis, DB and pipeline.

    Only the boundaries are faked. The loop, the exception handlers and the
    summary — everything the incident was about — are the real code.
    """
    statuses = []
    session = _FakeSession()

    monkeypatch.setattr(tasks_doc_gen, "_get_sync_session", lambda: session)
    monkeypatch.setattr(tasks_doc_gen, "acquire_lock", lambda org: True)
    monkeypatch.setattr(tasks_doc_gen, "refresh_lock", lambda org: None)
    monkeypatch.setattr(tasks_doc_gen, "release_lock", lambda org: None)
    monkeypatch.setattr(tasks_doc_gen, "get_status", lambda org: None)
    monkeypatch.setattr(
        tasks_doc_gen,
        "_set_status",
        lambda org, status, **extra: statuses.append({"status": status, **extra}),
    )

    calls = []

    def install(side_effect):
        """Install a fake ``run_generation`` and record every call."""

        def fake_run_generation(_session, **kwargs):
            calls.append(kwargs)
            outcome = side_effect(len(calls) - 1, kwargs)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        import services.doc_gen.pipeline as pipeline

        monkeypatch.setattr(pipeline, "run_generation", fake_run_generation)

    return type(
        "Harness",
        (),
        {
            "statuses": statuses,
            "calls": calls,
            "session": session,
            "install": staticmethod(install),
        },
    )


def _requests(n):
    return [{"generator": "policy", "domain_id": f"D{i}"} for i in range(n)]


def _run(n):
    return generate_documents_task.run(
        organization_id="org-1", requests=_requests(n), user_id=None,
        user_email="a@example.com", force=False,
    )


def test_soft_limit_stops_the_loop_instead_of_starting_the_next_document(harness):
    """The core regression.

    ``SoftTimeLimitExceeded`` subclasses ``Exception``. Caught by the broad
    in-loop handler it was recorded as one failed document and the loop moved
    on, spending the whole grace window on work that could not finish and
    guaranteeing an uncatchable SIGKILL.

    The call count is the assertion that distinguishes ``break`` from
    ``continue``: five documents requested, the third raises, so
    ``run_generation`` must be called exactly three times — never four.
    """
    harness.install(
        lambda i, kw: SoftTimeLimitExceeded() if i == 2 else _FakeResult("policy", kw["domain_id"])
    )

    summary = _run(5)

    assert len(harness.calls) == 3, "loop continued past the soft limit"
    assert summary["generated"] == 2
    assert summary["failed"] == 0, "the interrupted document is not a failure"
    assert summary["interrupted"] is True


def test_soft_limit_reports_partial_and_keeps_the_finished_documents(harness):
    harness.install(
        lambda i, kw: SoftTimeLimitExceeded() if i == 2 else _FakeResult("policy", kw["domain_id"])
    )

    _run(5)

    final = harness.statuses[-1]
    assert final["status"] == "partial"
    assert final["completed"] == 2, "must report documents actually produced"
    assert final["total"] == 5
    assert final["generated"] == 2


def test_soft_limit_rolls_back_the_interrupted_document(harness):
    harness.install(
        lambda i, kw: SoftTimeLimitExceeded() if i == 2 else _FakeResult("policy", kw["domain_id"])
    )

    _run(5)

    assert harness.session.rollbacks == 1
    assert harness.session.commits == 2


def test_an_expected_refusal_still_continues_the_loop(harness):
    """The mirror of the regression above.

    A refused generator must NOT stop the batch — that behaviour predates this
    fix and the new handler must not have swallowed it.
    """
    from services.doc_gen.pipeline import PipelineError

    harness.install(
        lambda i, kw: PipelineError("no controls in scope")
        if i == 2
        else _FakeResult("policy", kw["domain_id"])
    )

    summary = _run(5)

    assert len(harness.calls) == 5, "an expected refusal must not stop the batch"
    assert summary["generated"] == 4
    assert summary["failed"] == 1
    assert summary["interrupted"] is False
    assert harness.statuses[-1]["status"] == "completed_with_errors"


def test_an_uninterrupted_run_still_reports_completed(harness):
    harness.install(lambda i, kw: _FakeResult("policy", kw["domain_id"]))

    summary = _run(3)

    assert summary["generated"] == 3
    assert summary["interrupted"] is False
    assert harness.statuses[-1]["status"] == "completed"
    assert harness.statuses[-1]["completed"] == 3


# ---------------------------------------------------------------------------
# The signal has to survive every broad handler between here and the model call
# ---------------------------------------------------------------------------
#
# ``SoftTimeLimitExceeded`` subclasses ``Exception``, so ANY ``except
# Exception`` between the pool and the task's own handler destroys the signal —
# either by swallowing it or by relabelling it as something else. The handler in
# the task is worthless if an intermediate frame gets there first, and the
# frames below are all on the hot path. These tests pin each one; the tests
# above cannot see them because they fake ``run_generation`` wholesale.


def test_the_model_call_does_not_relabel_a_soft_limit_as_a_generation_error():
    """The single most likely place for the signal to be delivered.

    Essentially all of a document's 46-81s is the model call, and its broad
    handler wraps everything into ``GenerationError``. A relabelled soft limit
    is invisible to the task — it lands in the broad in-loop handler, is
    recorded as one failed document, and the batch carries on into the SIGKILL.
    """
    import services.doc_gen.tier2 as tier2

    class _Boom:
        class messages:  # noqa: N801 - mirrors the SDK's shape
            @staticmethod
            def create(**_kwargs):
                raise SoftTimeLimitExceeded()

    source = inspect.getsource(tier2.generate_document)
    assert "except SoftTimeLimitExceeded:" in source, (
        "tier2.generate_document must let the soft limit through before its "
        "broad handler wraps everything into GenerationError"
    )
    # The guard must come BEFORE the broad handler, or it never runs.
    assert source.index("except SoftTimeLimitExceeded:") < source.index(
        "except Exception as exc:"
    )


@pytest.mark.parametrize(
    "helper,call",
    [
        ("_set_status", lambda: tasks_doc_gen._set_status("org-1", "running")),
        ("get_status", lambda: tasks_doc_gen.get_status("org-1")),
        ("acquire_lock", lambda: tasks_doc_gen.acquire_lock("org-1")),
        ("refresh_lock", lambda: tasks_doc_gen.refresh_lock("org-1")),
    ],
)
def test_redis_helpers_let_a_soft_limit_through(helper, call, monkeypatch):
    """These swallow Redis errors by design — but not this one.

    ``refresh_lock`` runs at the top of every iteration and ``_set_status`` runs
    at every stage of every document, so between them they are executing for a
    good part of the batch. The signal is delivered exactly once; swallowed
    here it is gone, and the task runs on to an uncatchable SIGKILL.
    """

    class _AngryRedis:
        def __getattr__(self, _name):
            def _boom(*_a, **_kw):
                raise SoftTimeLimitExceeded()

            return _boom

    monkeypatch.setattr(tasks_doc_gen, "_get_sync_redis", lambda: _AngryRedis())

    with pytest.raises(SoftTimeLimitExceeded):
        call()


def test_release_lock_deliberately_does_not_re_raise(monkeypatch):
    """The one helper that must NOT propagate.

    It runs in the task's ``finally``, after the outcome is decided and the
    status written. Raising out of cleanup would replace whatever was
    propagating and turn a finished run into a failed one.
    """

    class _AngryRedis:
        def __getattr__(self, _name):
            def _boom(*_a, **_kw):
                raise SoftTimeLimitExceeded()

            return _boom

    monkeypatch.setattr(tasks_doc_gen, "_get_sync_redis", lambda: _AngryRedis())

    tasks_doc_gen.release_lock("org-1")  # must not raise


# ---------------------------------------------------------------------------
# Lock refusal
# ---------------------------------------------------------------------------


def test_refusal_does_not_overwrite_a_live_progress_key(monkeypatch):
    """A redelivered duplicate must stay silent.

    The broker redelivers a task that outlives its visibility timeout, so with
    ``acks_late`` a second copy of a long batch reaches the refusal path while
    the first is still working. Writing "failed" there reports the refusal as
    though the running batch had died — the progress bar dies and the client
    stops polling mid-generation.
    """
    statuses = []
    # _set_status always stamps updated_at, so a real incumbent always has one.
    live = {
        "status": "running",
        "completed": 40,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    monkeypatch.setattr(tasks_doc_gen, "acquire_lock", lambda org: False)
    monkeypatch.setattr(tasks_doc_gen, "release_lock", lambda org: None)
    monkeypatch.setattr(tasks_doc_gen, "get_status", lambda org: live)
    monkeypatch.setattr(
        tasks_doc_gen,
        "_set_status",
        lambda org, status, **extra: statuses.append(status),
    )

    result = _run(5)

    assert result["error"] == "already_running"
    assert statuses == [], "refusal clobbered the incumbent's progress key"


def test_refusal_reports_failure_when_the_incumbent_has_gone_quiet(monkeypatch):
    """A wedged organisation must surface, not sit silent.

    The lock now outlives a whole run and is refreshed per document, so a
    worker killed without unwinding leaves the lock AND a "running" status
    behind for hours. Staying quiet on that would return 200 to every Generate,
    write nothing, and leave the client polling a frozen progress bar with
    nothing to act on — worse than the 15-minute self-heal it replaced.
    """
    statuses = []
    stale = (
        datetime.now(timezone.utc)
        - timedelta(seconds=tasks_doc_gen.DOCGEN_HEARTBEAT_STALE_AFTER + 60)
    ).isoformat()

    monkeypatch.setattr(tasks_doc_gen, "acquire_lock", lambda org: False)
    monkeypatch.setattr(tasks_doc_gen, "release_lock", lambda org: None)
    monkeypatch.setattr(
        tasks_doc_gen,
        "get_status",
        lambda org: {"status": "running", "completed": 40, "updated_at": stale},
    )
    monkeypatch.setattr(
        tasks_doc_gen, "_set_status", lambda org, status, **extra: statuses.append(status)
    )

    _run(5)

    assert statuses == ["failed"], "a dead incumbent must not silence the refusal"


def test_a_live_heartbeat_still_silences_the_refusal(monkeypatch):
    """The other half: a genuinely running batch keeps its progress key."""
    statuses = []
    fresh = datetime.now(timezone.utc).isoformat()

    monkeypatch.setattr(tasks_doc_gen, "acquire_lock", lambda org: False)
    monkeypatch.setattr(tasks_doc_gen, "release_lock", lambda org: None)
    monkeypatch.setattr(
        tasks_doc_gen,
        "get_status",
        lambda org: {"status": "running", "completed": 40, "updated_at": fresh},
    )
    monkeypatch.setattr(
        tasks_doc_gen, "_set_status", lambda org, status, **extra: statuses.append(status)
    )

    _run(5)

    assert statuses == []


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"status": "running"},  # no heartbeat at all
        {"status": "running", "updated_at": "not-a-timestamp"},
        {"status": "completed", "updated_at": datetime.now(timezone.utc).isoformat()},
    ],
)
def test_unreadable_or_terminal_status_counts_as_not_alive(payload):
    """Fail towards a visible error, never towards silence.

    Being wrong this way costs one spurious "already running" message. Being
    wrong the other way wedges the organisation for hours with no error at all.
    """
    assert tasks_doc_gen._incumbent_is_alive(payload) is False


def test_refusal_still_reports_failure_when_no_run_is_in_flight(monkeypatch):
    """The guard must not silence a genuine stale-lock refusal.

    With no live status key there is no incumbent to protect, and the caller
    still needs to be told why nothing happened.
    """
    statuses = []
    monkeypatch.setattr(tasks_doc_gen, "acquire_lock", lambda org: False)
    monkeypatch.setattr(tasks_doc_gen, "release_lock", lambda org: None)
    monkeypatch.setattr(tasks_doc_gen, "get_status", lambda org: None)
    monkeypatch.setattr(
        tasks_doc_gen,
        "_set_status",
        lambda org, status, **extra: statuses.append(status),
    )

    result = _run(5)

    assert result["error"] == "already_running"
    assert statuses == ["failed"]


# ---------------------------------------------------------------------------
# The model client's own timeout
# ---------------------------------------------------------------------------
#
# Separate from the task's limits, and only reachable because of them. Under
# the old 600s task limit an unbounded SDK call could never matter — the task
# died long before the client gave up. At a 3h limit the client's own ceiling
# becomes the thing standing between one stuck connection and a large slice of
# the batch budget.


def _tier2_inputs():
    """Minimal real objects for a Tier 2 call. No database, no key."""
    from services.doc_gen.context import (
        Domain,
        DomainWithControls,
        EnrichedControl,
        OrganisationContext,
    )
    from services.doc_gen.registry import get_generator

    controls = [
        EnrichedControl(
            scf_id="AAA-01",
            control_name="First Control",
            control_description="First Control description.",
            domain_identifier="GOV",
            implementation_status="implemented",
            maturity_level="L3",
            owner="Security Manager",
        )
    ]
    bundle = DomainWithControls(
        domain=Domain(
            identifier="GOV", name="Governance", principle="Be governed.", order=1
        ),
        controls=controls,
        maturity_breakdown={"L3": 1},
        status_breakdown={"implemented": 1},
    )
    ctx = OrganisationContext(
        organization_id="org-1",
        name="Acme Holdings",
        generated_at="2026-08-21T10:00:00+00:00",
        catalog_version="2026.2",
        domains=[bundle],
        all_controls=controls,
        maturity_distribution={"L3": 1},
        status_distribution={"implemented": 1},
        total_scoped_controls=1,
        total_domains=1,
    )
    return get_generator("policy"), ctx, bundle


def test_the_model_client_is_constructed_with_a_timeout(monkeypatch):
    """A stuck connection must not hold the worker for the SDK's default.

    Constructed for real and intercepted at the constructor, so this fails if
    the keyword is dropped, renamed, or the call site stops passing it —
    which reading the source for a substring would not catch.
    """
    import anthropic

    import services.doc_gen.tier2 as tier2

    spec, ctx, bundle = _tier2_inputs()
    seen = {}

    class _Sentinel(Exception):
        pass

    def _fake_anthropic(**kwargs):
        seen.update(kwargs)
        raise _Sentinel()

    monkeypatch.setattr(anthropic, "Anthropic", _fake_anthropic)
    monkeypatch.delenv("DOC_GEN_AI_MOCK", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key")

    # The broad handler wraps our sentinel; either way the constructor ran.
    with pytest.raises(Exception):
        tier2.generate_document(spec, ctx, bundle)

    assert "timeout" in seen, (
        "doc_gen's Anthropic client must pin a timeout like its siblings in "
        "recipe_generation_engine and vendor_assessment_engine; without one "
        "the SDK default of 600s per attempt applies"
    )
    assert seen["timeout"] == tier2.MODEL_CALL_TIMEOUT_SECONDS


def test_the_model_call_timeout_is_smaller_than_the_task_soft_limit():
    """Otherwise the client's ceiling is decorative — the task ends first."""
    import services.doc_gen.tier2 as tier2

    assert tier2.MODEL_CALL_TIMEOUT_SECONDS < tasks_doc_gen.DOCGEN_SOFT_TIME_LIMIT
