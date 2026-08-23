"""Offline guards on the LLM model inventory (#782).

The liveness check in ``scripts/check_model_ids.py`` needs an API key and so
cannot run on every PR. These tests need nothing, run in the normal backend
suite, and cover the part a key would not catch anyway: that the inventory stays
the *only* place a model id is written down, that every priced role is priced,
and that nobody reintroduces a dated snapshot id — the exact shape that rotted.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from services import model_registry
from services.model_registry import (
    COST_TRACKED_ROLES,
    GLOBAL_DEFAULT_ROLES,
    GLOBAL_MODEL_ENV,
    MODELS,
    ROLES,
    anthropic_model_ids,
    cost_cents,
    resolve,
    spec,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _load_liveness_script():
    """Import scripts/check_model_ids.py by path — `scripts/` is not a package."""
    import importlib.util

    path = BACKEND_ROOT / "scripts" / "check_model_ids.py"
    spec = importlib.util.spec_from_file_location("check_model_ids", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

#: Ids that look like a provider model, for the families anyone here is likely
#: to reach for. It is a heuristic, and the honest limits are worth stating so
#: nobody reads a green run as a proof:
#:
#:   * a family not listed below (say ``cohere-command-r``) slips past;
#:   * so does implicit concatenation, ``"claude-" "sonnet-4-6"``, and an
#:     f-string with a placeholder.
#:
#: It catches the way a model id actually gets added — someone types the literal
#: into the service they are writing — which is how all nine instances in #782
#: got there. Add a family here when one appears; that is cheaper than the
#: alternative, which is not noticing.
MODEL_ID_PATTERN = re.compile(
    r"\b("
    r"claude-[a-z0-9.]+-[a-z0-9.]+"
    r"|gpt-\d[\w.]*"
    r"|o\d-(?:mini|preview|pro)[\w.]*"
    r"|gemini-[\w.]+"
    r"|llama-?\d[\w.-]*"
    r"|mistral-(?:large|small|medium|nemo)[\w.-]*"
    r"|mixtral-[\w.-]+"
    r"|grok-\d[\w.-]*"
    r"|deepseek-[\w.-]+"
    r"|command-r[\w.-]*"
    r"|titan-[\w.-]+"
    r")\b"
)

#: The registry is where ids live. The eval corpus and fixtures record which
#: model produced a row and legitimately contain historical ids.
ALLOWED_FILES = {
    "services/model_registry.py",
}
ALLOWED_DIRS = (
    "tests/",
    "scripts/cdm_eval/fixtures/",
    "alembic/",
)


class TestRegistryShape:
    def test_every_role_default_is_a_known_model(self):
        for role, (_env, default) in ROLES.items():
            assert default in MODELS, f"role {role!r} defaults to unregistered model {default!r}"

    def test_every_role_env_var_is_distinct(self):
        env_vars = [env for env, _ in ROLES.values()]
        assert len(env_vars) == len(set(env_vars)), "two roles share one override variable"

    def test_cost_tracked_roles_point_at_priced_models(self):
        """A repoint onto an unpriced model must fail here, not write NULL costs."""
        for role in COST_TRACKED_ROLES:
            assert role in ROLES
            model = MODELS[ROLES[role][1]]
            assert model.input_cost_per_mtok is not None, f"{role} -> {model.id} has no input price"
            assert model.output_cost_per_mtok is not None, f"{role} -> {model.id} has no output price"

    def test_no_dated_snapshot_ids(self):
        """`claude-sonnet-4-20250514` is the shape that rotted. Do not add another."""
        for model_id in MODELS:
            assert not re.search(r"-\d{8}$", model_id), (
                f"{model_id!r} pins a dated snapshot. Pin the undated alias so the id "
                "survives a point release."
            )

    def test_registry_is_stdlib_only(self):
        """Imported by Celery workers and a CLI script — must stay import-light."""
        source = (BACKEND_ROOT / "services" / "model_registry.py").read_text()
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                # A relative import is the easy way to break this contract
                # without tripping a stdlib check — `from .pricing_db import
                # rates` would pull the ORM in behind a dot. There is no
                # legitimate relative import here, so any is a failure.
                if node.level > 0:
                    imported.add(f"<relative: .{node.module or ''}>")
                elif node.module:
                    imported.add(node.module.split(".")[0])
        forbidden = imported - set(sys.stdlib_module_names)
        assert not forbidden, f"model_registry gained non-stdlib imports: {sorted(forbidden)}"


class TestResolve:
    @pytest.fixture(autouse=True)
    def _no_global(self, monkeypatch):
        """These assert per-role behaviour, so the global must not decide it.

        Without this, the outcome depends on whether the developer running the
        suite happens to have SCF_AI_MODEL exported.
        """
        monkeypatch.delenv(GLOBAL_MODEL_ENV, raising=False)

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("EVIDENCE_AI_MODEL", raising=False)
        assert resolve("evidence_assessment") == "claude-sonnet-4-6"

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("EVIDENCE_AI_MODEL", "claude-fable-5")
        assert resolve("evidence_assessment") == "claude-fable-5"

    def test_blank_env_falls_back(self, monkeypatch):
        """An empty variable is "unset", not "call the empty-string model"."""
        monkeypatch.setenv("EVIDENCE_AI_MODEL", "   ")
        assert resolve("evidence_assessment") == "claude-sonnet-4-6"

    def test_unregistered_override_is_honoured_but_warned(self, monkeypatch, caplog):
        """Production must be able to escape a bad default without a deploy."""
        monkeypatch.setenv("EVIDENCE_AI_MODEL", "claude-something-new")
        with caplog.at_level("WARNING"):
            assert resolve("evidence_assessment") == "claude-something-new"
        assert "not in the model registry" in caplog.text

    def test_override_with_a_path_separator_is_refused(self, monkeypatch, caplog):
        """A model id reaches a request URL. `../` must not survive to get there."""
        monkeypatch.setenv("CDM_INTENT_GEMINI_MODEL", "../../v1beta/models/other")
        with caplog.at_level("ERROR"):
            assert resolve("cdm_intent_gemini") == "gemini-3.7-flash"
        assert "not a syntactically valid model id" in caplog.text

    @pytest.mark.parametrize("bad", ["mo del", "model/../x", "x" * 200, "-leading-dash"])
    def test_malformed_overrides_fall_back(self, monkeypatch, bad):
        monkeypatch.setenv("EVIDENCE_AI_MODEL", bad)
        assert resolve("evidence_assessment") == "claude-sonnet-4-6"

    def test_unknown_role_raises(self):
        with pytest.raises(KeyError, match="Unknown model role"):
            resolve("no_such_role")


class TestOneVariableMovesThePlatform:
    """SCF_AI_MODEL — the one place to set the model.

    Asked for on the PR: "Models change, one place to set model name please."
    Eight per-role variables answer "hold this one service back"; nobody wants
    to answer "move to the new model" eight times, and doing it seven times is
    how half the platform ends up on a retired id.
    """

    OTHER = "claude-fable-5"  # in the registry, and not any role's default

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        for env_var, _default in ROLES.values():
            monkeypatch.delenv(env_var, raising=False)
        monkeypatch.delenv(GLOBAL_MODEL_ENV, raising=False)

    def test_it_names_roles_that_exist(self):
        assert set(GLOBAL_DEFAULT_ROLES) <= set(ROLES)

    @pytest.mark.parametrize("role", GLOBAL_DEFAULT_ROLES)
    def test_it_moves_every_platform_role(self, role, monkeypatch):
        monkeypatch.setenv(GLOBAL_MODEL_ENV, self.OTHER)
        assert resolve(role) == self.OTHER

    @pytest.mark.parametrize(
        "role", [r for r in ROLES if r not in GLOBAL_DEFAULT_ROLES]
    )
    def test_provider_pinned_roles_ignore_it(self, role, monkeypatch):
        """A Claude id sent to the Gemini endpoint is a 404, not a repoint."""
        monkeypatch.setenv(GLOBAL_MODEL_ENV, self.OTHER)
        assert resolve(role) == ROLES[role][1]

    def test_a_role_variable_beats_it(self, monkeypatch):
        """'Everything on the new model except doc-gen, which regressed.'"""
        monkeypatch.setenv(GLOBAL_MODEL_ENV, self.OTHER)
        monkeypatch.setenv("DOC_GEN_AI_MODEL", "claude-sonnet-4-6")
        assert resolve("doc_gen") == "claude-sonnet-4-6"
        assert resolve("vendor_assessment") == self.OTHER

    def test_blank_is_not_a_setting(self, monkeypatch):
        monkeypatch.setenv(GLOBAL_MODEL_ENV, "   ")
        assert resolve("doc_gen") == ROLES["doc_gen"][1]

    def test_a_malformed_global_does_not_take_the_platform_with_it(
        self, monkeypatch, caplog
    ):
        monkeypatch.setenv(GLOBAL_MODEL_ENV, "../../v1beta/models/other")
        with caplog.at_level("ERROR"):
            for role in GLOBAL_DEFAULT_ROLES:
                assert resolve(role) == ROLES[role][1]
        assert any(GLOBAL_MODEL_ENV in r.message for r in caplog.records)

    def test_it_reaches_both_model_calling_containers(self):
        compose = (Path(BACKEND_ROOT).parent / "docker-compose.yml").read_text()
        assert compose.count(f"{GLOBAL_MODEL_ENV}: ${{{GLOBAL_MODEL_ENV}:-}}") >= 2, (
            f"{GLOBAL_MODEL_ENV} is not forwarded to both the backend and "
            "celery-worker services, so setting it would do nothing."
        )

    def test_it_is_documented(self):
        env_example = (Path(BACKEND_ROOT).parent / ".env.example").read_text()
        assert GLOBAL_MODEL_ENV in env_example


class TestCost:
    def test_matches_the_constants_it_replaced(self):
        """$3/M in, $15/M out — byte-for-byte the pre-#782 Sonnet rate card.

        Consolidating four copies of a rate card must not re-tune anybody's
        reported spend.
        """
        assert cost_cents("claude-sonnet-4-6", 1_000_000, 0) == pytest.approx(300.0)
        assert cost_cents("claude-sonnet-4-6", 0, 1_000_000) == pytest.approx(1500.0)

    def test_dated_response_id_still_prices(self):
        """The API may answer with a dated id even when an alias was requested."""
        assert cost_cents("claude-sonnet-4-6-20260101", 1_000_000, 0) == pytest.approx(300.0)

    def test_unpriced_model_returns_none_not_zero(self, caplog):
        """NULL reads as "unknown". 0.0 reads as "free", and would be a lie."""
        with caplog.at_level("WARNING"):
            assert cost_cents("claude-fable-5", 1000, 1000) is None
        assert "No declared price" in caplog.text

    def test_unknown_model_returns_none(self):
        assert cost_cents("some-model-nobody-registered", 1000, 1000) is None

    def test_none_model_returns_none(self):
        assert cost_cents(None, 1000, 1000) is None

    def test_spec_tolerates_missing(self):
        assert spec(None) is None
        assert spec("") is None
        assert spec("claude-sonnet-4-6").id == "claude-sonnet-4-6"


class TestNoIdsOutsideTheRegistry:
    """The class sweep, enforced.

    #782's cause was not a missing env knob — it was that a model id is chosen
    once, at authoring time, in whichever file needs it, and nothing revisits
    it. Repointing four services fixes the instances; this test closes the class
    by making a fifth impossible to merge.
    """

    @staticmethod
    def _string_constants(path: Path):
        """Every string literal in a module, excluding docstrings and comments.

        Comments are not in the AST at all. Docstrings are excluded so prose
        explaining an id (including this file's own module docstring) does not
        trip the check — only a literal a call site could actually *use*.
        """
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, "body", None)
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    docstrings.add(id(body[0].value))
        return [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ]

    def test_no_module_pins_a_model_id(self):
        offenders = []
        for path in sorted(BACKEND_ROOT.rglob("*.py")):
            rel = path.relative_to(BACKEND_ROOT).as_posix()
            if rel in ALLOWED_FILES or rel.startswith(ALLOWED_DIRS):
                continue
            if "/.venv/" in f"/{rel}" or rel.startswith(("venv/", ".venv/", "node_modules/")):
                continue
            try:
                constants = self._string_constants(path)
            except SyntaxError:
                continue
            for value in constants:
                match = MODEL_ID_PATTERN.search(value)
                if match:
                    offenders.append(f"{rel}: {match.group(0)!r}")

        assert not offenders, (
            "Model ids must live only in services/model_registry.py, so one "
            "inventory can be checked against provider liveness (#782). Found:\n  "
            + "\n  ".join(offenders)
        )


class TestLivenessScript:
    def test_runs_and_is_honest_about_being_inert_without_a_key(self):
        """No key must mean a loud no-op, never a quiet pass."""
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)
        env["GITHUB_ACTIONS"] = "true"
        result = subprocess.run(
            [sys.executable, str(BACKEND_ROOT / "scripts" / "check_model_ids.py")],
            capture_output=True, text=True, env=env, timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert "::warning::" in result.stdout
        assert "was NOT checked" in result.stdout

    def test_unarmed_run_fails_when_a_key_is_required(self):
        """The scheduled run sets MODEL_LIVENESS_REQUIRE_KEY=1.

        A green tick nobody is notified about is not disclosure — an unarmed
        check has to look broken, or it sits inert forever.
        """
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)
        env["GITHUB_ACTIONS"] = "true"
        env["MODEL_LIVENESS_REQUIRE_KEY"] = "1"
        result = subprocess.run(
            [sys.executable, str(BACKEND_ROOT / "scripts" / "check_model_ids.py")],
            capture_output=True, text=True, env=env, timeout=60,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "::error::" in result.stdout

    def test_snapshot_matching_is_exact_not_prefix(self):
        """`claude-haiku-4-5-20260101` must not vouch for a retired `claude-haiku-4`."""
        check = _load_liveness_script()
        # Listed outright, and listed only as a dated snapshot: both are alive.
        assert check.is_live("claude-sonnet-4-6", {"claude-sonnet-4-6"})
        assert check.is_live("claude-sonnet-4-6", {"claude-sonnet-4-6-20260101"})
        # A longer id that merely shares the prefix is a DIFFERENT model.
        assert not check.is_live("claude-haiku-4", {"claude-haiku-4-5-20260101"})
        assert not check.is_live("claude-haiku-4", {"claude-haiku-4-5"})
        assert not check.is_live("claude-sonnet-4-6", set())

    def test_checks_every_anthropic_id_in_the_registry(self):
        ids = anthropic_model_ids()
        assert ids, "the liveness check would have nothing to check"
        assert set(ids) == {m.id for m in MODELS.values() if m.provider == "anthropic"}


class TestOverridesReachTheContainers:
    """Every role's override variable must be forwarded by docker-compose.

    Found in review: none of the eight were. `docker-compose.yml` uses an
    explicit allow-list with no `env_file:`, so a variable documented in
    `.env.example` and read by the code still never reaches the process. An
    operator doing exactly what the docs say — set the variable, restart —
    would have watched the retired default get called anyway, with nothing to
    explain why.

    That is the same defect as #781 in a different costume: a control surface
    wired to nothing. Asserting it here means the next role added cannot repeat
    it.
    """

    COMPOSE = Path(BACKEND_ROOT).parent / "docker-compose.yml"

    @pytest.mark.parametrize("role", sorted(ROLES))
    def test_env_var_is_forwarded_to_both_model_calling_services(self, role):
        env_var = ROLES[role][0]
        compose = self.COMPOSE.read_text()
        # backend serves the API paths, celery-worker runs assessment and
        # generation. Both call models, so both need every override.
        assert compose.count(f"{env_var}: ${{{env_var}:-}}") >= 2, (
            f"{env_var} is not forwarded to both the backend and celery-worker "
            "services in docker-compose.yml, so setting it in .env would do "
            "nothing."
        )

    def test_env_example_documents_every_override(self):
        env_example = (Path(BACKEND_ROOT).parent / ".env.example").read_text()
        for role, (env_var, _default) in ROLES.items():
            assert env_var in env_example, f"{env_var} ({role}) is undocumented"
