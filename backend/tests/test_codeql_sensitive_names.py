"""Identifiers passed into the credential accessors must not look like secrets.

`services.secrets` resolves every credential through `get_secret(name)` /
`_resolve(name)` / `_file_value(name)`, and `_file_value` logs the *name* of
the `{NAME}_FILE` variable it could not read. CodeQL's sensitive-data heuristic
(github/codeql, `SensitiveDataHeuristics.qll`) classifies a value by the
identifier that holds it, so a constant called `SECRET_NAME` flowing into that
log line is reported as `py/clear-text-logging-sensitive-data` (high) — even
though the value is an environment-variable name. That alert blocked the
v0.35.0 OSS release PR; the private repo cannot run CodeQL (no GHAS), so this
test is the pre-merge oracle for the class.

The regexes below are CodeQL's, with Java-style alternation lookbehinds
rewritten as stacked fixed-width lookbehinds for Python's `re`.
"""
import re
from pathlib import Path

import pytest

SERVICES = Path(__file__).resolve().parents[1] / "services"

CODEQL_SENSITIVE = {
    "secret": re.compile(
        r"(?is).*((?<!is)(?<!is_)secret"
        r"|(?<!un)(?<!un_)(?<!is)(?<!is_)trusted(?!_iter)"
        r"|confidential).*"
    ),
    "password": re.compile(
        r"(?is).*(pass(wd|word|code|.?phrase)(?!.*question)"
        r"|(auth(entication|ori[sz]ation)?).?key|oauth"
        r"|api.?(key|tok)|([_-]|\b)mfa([_-]|\b)).*"
    ),
    "certificate": re.compile(r"(?is).*(cert)(?!.*(format|name|ification)).*"),
    "account": re.compile(
        r"(?is).*(acc(ou)?nt|puid|user.?(name|id)|session.?(id|key)).*"
    ),
}

# `get_secret(KEY_ENV)`, `source_of(name)`, `_resolve(_KEY_NAME)` … — an
# identifier argument, not a string literal.
ACCESSOR_CALL = re.compile(
    r"\b(?:get_secret|source_of|_resolve|_file_value)\(\s*"
    r"([A-Za-z_][A-Za-z0-9_.]*)\s*[,)]"
)


def _identifier_args():
    for path in sorted(SERVICES.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in ACCESSOR_CALL.finditer(text):
            ident = match.group(1)
            if ident in ("self", "cls"):
                continue
            line = text.count("\n", 0, match.start()) + 1
            yield f"{path.relative_to(SERVICES.parent)}:{line}", ident


def _classify(ident: str):
    leaf = ident.rsplit(".", 1)[-1]
    return [k for k, rx in CODEQL_SENSITIVE.items() if rx.fullmatch(leaf)]


def test_regex_model_reproduces_the_v0_35_0_alert():
    """The model must agree with what CodeQL actually did on OSS PR #104."""
    assert _classify("SECRET_NAME") == ["secret"]   # flagged
    assert _classify("_KEY_NAME") == []              # same path, not flagged
    assert _classify("KEY_ENV") == []                # the replacement
    assert _classify("API_KEY_ENV") == ["password"]  # the tempting wrong fix


def test_no_identifier_into_the_secret_accessors_looks_sensitive():
    calls = list(_identifier_args())
    assert calls, "expected at least one identifier argument into the accessors"
    offenders = [
        f"{where}: {ident} matches CodeQL {families}"
        for where, ident in calls
        if (families := _classify(ident))
    ]
    assert not offenders, (
        "Rename these — CodeQL reads the identifier as credential material and "
        "reports clear-text logging at services/secrets.py _file_value, which "
        "blocks the OSS release PR:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(
    "ident",
    ["AUTH_KEY_ENV", "OAUTH_ENV", "PASSWORD_ENV", "SESSION_KEY_ENV",
     "CERT_ENV", "CONFIDENTIAL_ENV", "MFA_ENV", "API_TOKEN_ENV"],
)
def test_model_flags_the_names_a_maintainer_might_reach_for(ident):
    assert _classify(ident), f"{ident} should be caught by the model"
