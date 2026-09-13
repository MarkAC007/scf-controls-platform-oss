"""Phase 4 — the storage choice, from the flag through to `.env`.

Criteria 31, 32, 37 live here; 33, 34 and 36 are asserted in
``tests/test_storage_phase4_installer.py`` because they are properties of the
compose file and of the seeding service rather than of the installer package.

The shape these tests pin, and why each half matters:

**The choice is a third tier, exactly like the other two.** ``DB_TYPES`` and
``IDP_TYPES`` already existed; ``STORAGE_TYPES`` is the same construct, is
validated the same way, and an unknown value raises in the same place. Anything
looser would let ``storage.type: "minio"`` — a plausible typo — provision a
stack with no object store at all and no error line.

**The AWS pair stops being the MinIO root pair.** That equality was the whole
of criterion 37: the application talked to the object store as its root
account, so a leaked application credential was root of every bucket. The pair
is now generated independently and `minio-init` creates a MinIO user for it
whose policy names one bucket.

**The `none` path writes all ten files, empty.** Not nine. The secrets overlay
declares ten sources and `docker compose config` fails on a missing one, so
"unused" has always meant "present and empty" here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from installer import app, writer
from installer.__main__ import main as installer_main
from installer.generate import (
    DEFAULT_STORAGE_TYPE,
    SECRET_FILE_NAMES,
    STORAGE_BUNDLED_MINIO,
    STORAGE_NONE,
    STORAGE_TYPES,
    generate_secrets,
)
from installer.validate import ValidationRejected
from installer.writer import SENTINEL_NAME, STORAGE_BOOTSTRAP_KEY


# ---------------------------------------------------------------------------
# Criterion 31 — the vocabulary
# ---------------------------------------------------------------------------


def test_the_storage_vocabulary_is_the_two_values_and_nothing_else():
    """Criterion 31. A closed tuple, mirroring DB_TYPES and IDP_TYPES."""
    assert STORAGE_TYPES == ("bundled_minio", "none")
    assert STORAGE_BUNDLED_MINIO == "bundled_minio"
    assert STORAGE_NONE == "none"
    assert DEFAULT_STORAGE_TYPE == STORAGE_BUNDLED_MINIO


@pytest.mark.parametrize("storage_type", ["minio", "bundled", "None", "", "s3"])
def test_an_unknown_storage_type_is_refused_by_name(storage_type):
    """The same refusal db_type and idp_type already get.

    ``"minio"`` and ``"bundled"`` are the two typos an operator copying the
    database stanza would actually make, and both would otherwise fall through
    to a default rather than being named.
    """
    with pytest.raises(ValueError) as caught:
        generate_secrets(db_type="bundled", idp_type="none", storage_type=storage_type)

    assert "storage" in str(caught.value)


@pytest.mark.parametrize("storage_type", STORAGE_TYPES)
def test_both_storage_types_still_produce_all_ten_names(storage_type):
    """ISA section 14.1. The overlay declares ten sources and `docker compose
    config` fails on a missing file, so the no-storage path writes empty files
    rather than fewer files.
    """
    values = generate_secrets(
        db_type="bundled", idp_type="none", storage_type=storage_type
    )

    assert set(values) == set(SECRET_FILE_NAMES)
    assert len(SECRET_FILE_NAMES) == 10


# ---------------------------------------------------------------------------
# Criterion 37 — the application credential is not the root credential
# ---------------------------------------------------------------------------


def test_the_bundled_path_generates_a_distinct_scoped_pair():
    """Criterion 37, stated as the inequality it is.

    Before this phase both assertions below were equalities, and a test asserted
    them as the contract. The application credential is now its own pair, which
    is what makes the scoped MinIO account (criterion 28) possible at all: a
    user cannot be created for a key that is already the root user's.
    """
    values = generate_secrets(
        db_type="bundled", idp_type="none", storage_type=STORAGE_BUNDLED_MINIO
    )

    assert values["MINIO_ROOT_USER"]
    assert values["MINIO_ROOT_PASSWORD"]
    assert values["AWS_ACCESS_KEY_ID"]
    assert values["AWS_SECRET_ACCESS_KEY"]

    assert values["AWS_ACCESS_KEY_ID"] != values["MINIO_ROOT_USER"]
    assert values["AWS_SECRET_ACCESS_KEY"] != values["MINIO_ROOT_PASSWORD"]


def test_the_scoped_access_key_is_shaped_like_a_minio_user():
    """MinIO user names must be alphanumeric; the root user already is."""
    values = generate_secrets(
        db_type="bundled", idp_type="none", storage_type=STORAGE_BUNDLED_MINIO
    )
    assert values["AWS_ACCESS_KEY_ID"].isalnum()
    assert values["AWS_ACCESS_KEY_ID"].islower() or values["AWS_ACCESS_KEY_ID"].isdigit()


def test_the_pairs_are_independent_across_runs():
    """Two provisioning runs share no storage credential at all."""
    first = generate_secrets(db_type="bundled", idp_type="none")
    second = generate_secrets(db_type="bundled", idp_type="none")

    for name in (
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    ):
        assert first[name] != second[name], name


def test_the_no_storage_path_writes_four_empty_storage_values():
    """ISA section 14.1 again, from the other end.

    ``minio``'s entrypoint guard refuses to boot on an empty ``MINIO_ROOT_USER``
    — which is *correct* here, because on this path the service is behind an
    inactive profile and never starts. Leaving the value populated would be the
    dangerous shape: a stack that starts an object store nobody asked for.
    """
    values = generate_secrets(db_type="bundled", idp_type="none", storage_type=STORAGE_NONE)

    assert values["MINIO_ROOT_USER"] == ""
    assert values["MINIO_ROOT_PASSWORD"] == ""
    assert values["AWS_ACCESS_KEY_ID"] == ""
    assert values["AWS_SECRET_ACCESS_KEY"] == ""


def test_the_no_storage_path_still_generates_every_other_credential():
    """The storage choice must not disturb the other six names."""
    values = generate_secrets(
        db_type="bundled", idp_type="bundled_keycloak", storage_type=STORAGE_NONE
    )

    for name in ("DB_PASSWORD", "SCF_SECRET_KEY", "API_KEY", "DOWNLOAD_TOKEN_SECRET",
                 "KC_ADMIN_PASSWORD", "OIDC_CLIENT_SECRET"):
        assert values[name], name


def test_the_default_is_the_bundled_store():
    """An omitted storage type provisions what every install got before."""
    values = generate_secrets(db_type="bundled", idp_type="none")
    assert values["MINIO_ROOT_USER"]
    assert values["AWS_ACCESS_KEY_ID"]


# ---------------------------------------------------------------------------
# Criterion 32 — the profile and the bootstrap signal reach `.env`
# ---------------------------------------------------------------------------


def _env(idp_type="none", storage_type=STORAGE_BUNDLED_MINIO):
    return writer.build_env(
        host_secrets_dir="/home/op/.scf/secrets",
        db={"type": "bundled"},
        idp={
            "type": idp_type,
            "kc_admin_user": "admin",
            "bootstrap_admin_email": "op@example.test",
            "oidc_issuer": "https://issuer.example.test/oauth2",
            "oidc_client_id": "scf",
        },
        storage={"type": storage_type},
    )


def test_the_bundled_store_adds_the_storage_profile():
    assert "COMPOSE_PROFILES=storage" in _env(storage_type=STORAGE_BUNDLED_MINIO)


def test_the_two_profiles_are_combined_into_one_value():
    """The compose variable is a single comma-separated list, not two lines.

    A second ``COMPOSE_PROFILES=`` line would silently win over the first and
    switch off whichever profile was written earlier — the failure mode this
    assertion exists to catch.
    """
    content = _env(idp_type="bundled_keycloak", storage_type=STORAGE_BUNDLED_MINIO)

    assert "COMPOSE_PROFILES=idp,storage" in content
    assert writer.env_keys(content).count("COMPOSE_PROFILES") == 1


def test_the_idp_alone_still_produces_the_bare_idp_profile():
    content = _env(idp_type="bundled_keycloak", storage_type=STORAGE_NONE)
    assert "COMPOSE_PROFILES=idp\n" in content


def test_neither_profile_means_no_profile_line_at_all():
    """An empty ``COMPOSE_PROFILES=`` is not the same as an absent one to every
    reader of a `.env`, so the key is omitted rather than emitted empty."""
    content = _env(idp_type="none", storage_type=STORAGE_NONE)
    assert "COMPOSE_PROFILES" not in writer.env_keys(content)


@pytest.mark.parametrize("storage_type", STORAGE_TYPES)
def test_the_bootstrap_signal_is_always_written_and_names_the_choice(storage_type):
    """The backend cannot see ``COMPOSE_PROFILES`` — compose consumes it on the
    host and never forwards it — so the storage choice needs its own key for the
    seeding step (criterion 36) to read.
    """
    content = _env(storage_type=storage_type)

    assert f"{STORAGE_BOOTSTRAP_KEY}={storage_type}" in content
    assert writer.env_keys(content).count(STORAGE_BOOTSTRAP_KEY) == 1


def test_the_env_key_order_puts_the_signal_with_the_other_platform_keys():
    keys = writer.env_keys(_env())
    assert keys.index(STORAGE_BOOTSTRAP_KEY) > keys.index("DB_SSLMODE")


def test_an_omitted_storage_argument_is_the_bundled_store():
    """Every existing caller of build_env passes no storage argument, and must
    keep producing the install they produced before."""
    content = writer.build_env(
        host_secrets_dir="/s", db={"type": "bundled"}, idp={"type": "none"}
    )
    assert "COMPOSE_PROFILES=storage" in content
    assert f"{STORAGE_BOOTSTRAP_KEY}=bundled_minio" in content


def test_an_unknown_storage_type_is_refused_by_the_env_writer_too():
    """Two doors, one rule: the credential generator is not the only way in."""
    with pytest.raises(ValueError):
        writer.build_env(
            host_secrets_dir="/s",
            db={"type": "bundled"},
            idp={"type": "none"},
            storage={"type": "minio"},
        )


def test_the_env_still_carries_no_credential_on_either_path():
    for storage_type in STORAGE_TYPES:
        values = generate_secrets(
            db_type="bundled", idp_type="bundled_keycloak", storage_type=storage_type
        )
        content = _env(idp_type="bundled_keycloak", storage_type=storage_type)
        for name, value in values.items():
            assert name not in writer.env_keys(content), name
            if value:
                assert value not in content, name


# ---------------------------------------------------------------------------
# The sentinel records the third choice
# ---------------------------------------------------------------------------


def test_the_sentinel_records_which_storage_was_provisioned(tmp_path):
    """The sentinel is the only durable record of what the installer chose; an
    operator debugging a stack with no object store should find it there."""
    writer.create_sentinel(tmp_path, db="bundled", idp="none", storage=STORAGE_NONE)
    payload = json.loads((tmp_path / SENTINEL_NAME).read_text())

    assert payload["storage"] == "none"
    assert payload["version"] == writer.SENTINEL_VERSION


# ---------------------------------------------------------------------------
# Criterion 32 — the flag, and its disagreement with the config file
# ---------------------------------------------------------------------------


@pytest.fixture
def no_storage_flag(monkeypatch):
    monkeypatch.delenv(app.STORAGE_TYPE_ENV, raising=False)


def test_nothing_supplied_is_the_bundled_store(no_storage_flag):
    assert app.resolve_storage_type({}) == STORAGE_BUNDLED_MINIO


def test_the_flag_alone_selects_the_no_storage_path(monkeypatch):
    monkeypatch.setenv(app.STORAGE_TYPE_ENV, STORAGE_NONE)
    assert app.resolve_storage_type({}) == STORAGE_NONE


def test_the_config_alone_selects_the_no_storage_path(no_storage_flag):
    assert app.resolve_storage_type({"type": STORAGE_NONE}) == STORAGE_NONE


def test_the_flag_and_the_config_agreeing_is_not_an_error(monkeypatch):
    monkeypatch.setenv(app.STORAGE_TYPE_ENV, STORAGE_NONE)
    assert app.resolve_storage_type({"type": STORAGE_NONE}) == STORAGE_NONE


def test_the_flag_and_the_config_disagreeing_is_refused(monkeypatch):
    """Not a precedence question — a contradiction.

    ``--no-minio`` alongside ``"type": "bundled_minio"`` in the config file is
    two operators' intentions, or one operator's mistake. Resolving it silently
    would provision a stack nobody asked for; the refusal names both inputs.
    """
    monkeypatch.setenv(app.STORAGE_TYPE_ENV, STORAGE_NONE)

    with pytest.raises(ValidationRejected) as caught:
        app.resolve_storage_type({"type": STORAGE_BUNDLED_MINIO})

    assert "disagrees" in caught.value.detail


@pytest.mark.parametrize("bad", ["minio", "bundled", "yes"])
def test_an_unknown_value_is_refused_from_either_input(bad, monkeypatch):
    monkeypatch.delenv(app.STORAGE_TYPE_ENV, raising=False)
    with pytest.raises(ValidationRejected):
        app.resolve_storage_type({"type": bad})

    monkeypatch.setenv(app.STORAGE_TYPE_ENV, bad)
    with pytest.raises(ValidationRejected):
        app.resolve_storage_type({})


def test_a_refused_storage_choice_writes_nothing_at_all(tmp_path, no_storage_flag):
    """The sentinel rule: validation happens before the first write, so a bad
    configuration leaves the secrets directory untouched and re-runnable."""
    secrets_path = tmp_path / "secrets"
    out_path = tmp_path / "out"
    secrets_path.mkdir()
    out_path.mkdir()

    with pytest.raises(ValidationRejected):
        app._provision(
            payload={"db": {"type": "bundled"}, "idp": {"type": "none"},
                     "storage": {"type": "minio"}},
            secrets_path=secrets_path,
            out_path=out_path,
            host_dir=str(secrets_path),
        )

    assert list(secrets_path.iterdir()) == []
    assert not (out_path / ".env").exists()


@pytest.mark.parametrize("storage_type", STORAGE_TYPES)
def test_provisioning_end_to_end_writes_the_matching_env_and_files(
    tmp_path, storage_type, no_storage_flag
):
    secrets_path = tmp_path / "secrets"
    out_path = tmp_path / "out"
    secrets_path.mkdir()
    out_path.mkdir()

    result = app._provision(
        payload={
            "db": {"type": "bundled"},
            "idp": {"type": "none"},
            "storage": {"type": storage_type},
        },
        secrets_path=secrets_path,
        out_path=out_path,
        host_dir=str(secrets_path),
    )

    assert result["storage"] == storage_type
    env_text = (out_path / ".env").read_text()
    assert f"{STORAGE_BOOTSTRAP_KEY}={storage_type}" in env_text

    # All ten files exist either way; only their contents differ.
    for name in SECRET_FILE_NAMES:
        assert (secrets_path / name).exists(), name

    root_user = (secrets_path / "MINIO_ROOT_USER").read_text()
    app_key = (secrets_path / "AWS_ACCESS_KEY_ID").read_text()
    if storage_type == STORAGE_BUNDLED_MINIO:
        assert "COMPOSE_PROFILES=storage" in env_text
        assert root_user and app_key and root_user != app_key
    else:
        assert "COMPOSE_PROFILES" not in env_text
        assert root_user == ""
        assert app_key == ""

    sentinel = json.loads((secrets_path / SENTINEL_NAME).read_text())
    assert sentinel["storage"] == storage_type


# ---------------------------------------------------------------------------
# import-env: an existing install must not lose its object store
# ---------------------------------------------------------------------------

BUNDLED_LEGACY_ENV = """# an existing bundled install
ENVIRONMENT=production
DB_PASSWORD=legacy-db-password
API_KEY=legacy-api-key
MINIO_ROOT_USER=areallyprovisioneduser
MINIO_ROOT_PASSWORD=a-real-root-password
AWS_ACCESS_KEY_ID=areallyprovisioneduser
AWS_SECRET_ACCESS_KEY=a-real-root-password
AWS_ENDPOINT_URL=http://minio:9000
APP_URL=http://localhost:5173
"""


def _import_env(tmp_path, env_text):
    secrets_path = tmp_path / "secrets"
    out_path = tmp_path / "out"
    secrets_path.mkdir()
    out_path.mkdir()
    (out_path / ".env").write_text(env_text)
    code = installer_main(
        ["import-env", "--secrets-dir", str(secrets_path), "--out-dir", str(out_path)]
    )
    assert code == 0
    return secrets_path, (out_path / ".env").read_text()


def test_import_env_adds_the_storage_profile_to_a_bundled_install(tmp_path):
    """**The upgrade trap this exists to close.**

    `minio` and `minio-init` now sit behind the `storage` profile. An existing
    install adopting the secrets overlay through import-env would come back up
    with no object store at all — every evidence read and write failing — unless
    the profile is written into its `.env` here.
    """
    _, rewritten = _import_env(tmp_path, BUNDLED_LEGACY_ENV)

    assert "COMPOSE_PROFILES=storage" in rewritten
    assert f"{STORAGE_BOOTSTRAP_KEY}=bundled_minio" in rewritten


def test_import_env_keeps_profiles_it_did_not_write(tmp_path):
    """Union, not replacement. An operator's own profile survives."""
    _, rewritten = _import_env(
        tmp_path, BUNDLED_LEGACY_ENV + "COMPOSE_PROFILES=idp\n"
    )

    assert "COMPOSE_PROFILES=idp,storage" in rewritten
    assert writer.env_keys(rewritten).count("COMPOSE_PROFILES") == 1


def test_import_env_does_not_add_the_profile_twice(tmp_path):
    _, rewritten = _import_env(
        tmp_path, BUNDLED_LEGACY_ENV + "COMPOSE_PROFILES=storage\n"
    )
    assert "COMPOSE_PROFILES=storage\n" in rewritten
    assert rewritten.count("storage,storage") == 0


def test_import_env_of_an_install_with_no_usable_root_credential_says_none(tmp_path):
    """``MINIO_ROOT_USER=minioadmin`` is a placeholder, and the minio entrypoint
    guard refuses to boot on one — so such an install has not been starting its
    object store, and calling it bundled would be a lie."""
    _, rewritten = _import_env(
        tmp_path, BUNDLED_LEGACY_ENV.replace("areallyprovisioneduser", "minioadmin")
    )

    assert f"{STORAGE_BOOTSTRAP_KEY}=none" in rewritten
    assert "COMPOSE_PROFILES" not in writer.env_keys(rewritten)


# ---------------------------------------------------------------------------
# Criterion 32 — the launcher
# ---------------------------------------------------------------------------

INSTALL_SH = Path(__file__).resolve().parents[3] / "scripts" / "install.sh"


@pytest.mark.skipif(not INSTALL_SH.exists(), reason="scripts/install.sh not present")
class TestLauncherStorageFlag:
    def script(self) -> str:
        return INSTALL_SH.read_text()

    def test_the_flag_exists_and_is_documented(self):
        script = self.script()
        assert "--no-minio" in script
        # Named in the --help text, not only in the case statement.
        assert script.count("--no-minio") >= 2

    def test_the_flag_is_validated_against_the_same_vocabulary(self):
        assert "bundled_minio" in self.script()

    def test_the_flag_reaches_the_wizard_container(self):
        """The browser wizard has no storage field, so the flag's only transport
        into the provisioning code is this environment variable."""
        assert f"{app.STORAGE_TYPE_ENV}=" in self.script()

    def test_the_profile_grep_matches_a_combined_value(self):
        """``COMPOSE_PROFILES=idp,storage`` must still be recognised as having
        the idp profile, or the post-install Keycloak password is never printed.
        """
        import re
        import subprocess

        pattern = None
        for line in self.script().splitlines():
            match = re.search(r"grep -qE '(\^COMPOSE_PROFILES[^']*)'", line)
            if match:
                pattern = match.group(1)
                break
        assert pattern, "no COMPOSE_PROFILES grep found in install.sh"

        def matches(value: str) -> bool:
            return (
                subprocess.run(
                    ["grep", "-qE", pattern],
                    input=f"COMPOSE_PROFILES={value}\n",
                    text=True,
                ).returncode
                == 0
            )

        assert matches("idp")
        assert matches("idp,storage")
        assert matches("storage,idp")
        assert not matches("storage")
        assert not matches("")

    def test_the_script_still_parses(self):
        import subprocess

        result = subprocess.run(
            ["bash", "-n", str(INSTALL_SH)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr


def test_the_no_storage_path_empties_the_three_variables_that_would_lie(tmp_path):
    """The other half of `--no-minio`, and the one that is easy to miss.

    Compose defaults ``AWS_ENDPOINT_URL`` to ``http://minio:9000`` and
    ``EVIDENCE_BUCKET`` to ``evidence``. On a stack with no object store those
    defaults describe somewhere that does not exist: the backend would report
    itself as configured, refuse to start in production because the matching
    credential is absent, and fail every upload at the socket rather than with
    an answer. All three are read as ``${VAR-default}`` — no colon — so an
    explicit empty is honoured, which is what makes this work at all.
    """
    content = _env(storage_type=STORAGE_NONE)
    keys = writer.env_keys(content)

    for name in ("AWS_ENDPOINT_URL", "EVIDENCE_BUCKET", "EVIDENCE_PUBLIC_ENDPOINT"):
        assert name in keys, name
        assert f"{name}=\n" in content or content.endswith(f"{name}="), name


def test_the_bundled_path_leaves_those_three_to_the_compose_defaults():
    """The inverse. A bundled install must NOT pin them, or remapping
    MINIO_PORT would stop moving EVIDENCE_PUBLIC_ENDPOINT with it."""
    keys = writer.env_keys(_env(storage_type=STORAGE_BUNDLED_MINIO))

    for name in ("AWS_ENDPOINT_URL", "EVIDENCE_BUCKET", "EVIDENCE_PUBLIC_ENDPOINT"):
        assert name not in keys, name
