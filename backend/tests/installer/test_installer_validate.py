"""DSN parse/rebuild and the reject matrix.

The rule under test: an operator string is never handed to the driver.  It is
parsed into discrete parts, every libpq keyword that would change *where* or
*how* we connect is refused, and the connection is rebuilt from the parts.
"""

from __future__ import annotations

import pytest

from installer.validate import (
    ALLOWED_SSLMODES,
    FORBIDDEN_DSN_PARAMS,
    DbParts,
    ValidationRejected,
    guard_addresses,
    parse_dsn,
    parts_from_payload,
    sanitise_detail,
)


def ip(*octets: int) -> str:
    """Build address literals at runtime rather than writing them out."""
    return ".".join(str(o) for o in octets)


LINK_LOCAL = ip(169, 254, 169, 254)
LOOPBACK = ip(127, 0, 0, 1)
PRIVATE = ip(10, 4, 5, 6)
DOC_RANGE = ip(203, 0, 113, 9)


def test_a_plain_dsn_is_parsed_into_parts():
    parts = parse_dsn("postgresql://cg:hunter2@db.example.test:6543/scf_prod?sslmode=verify-full")
    assert parts.host == "db.example.test"
    assert parts.port == 6543
    assert parts.dbname == "scf_prod"
    assert parts.user == "cg"
    assert parts.password == "hunter2"
    assert parts.sslmode == "verify-full"


def test_defaults_fill_in_the_missing_parts():
    parts = parse_dsn("postgresql://db.example.test/")
    assert (parts.port, parts.dbname, parts.user) == (5432, "cg_scf", "cg")


@pytest.mark.parametrize("param", sorted(FORBIDDEN_DSN_PARAMS))
def test_every_forbidden_libpq_parameter_is_refused(param):
    with pytest.raises(ValidationRejected) as excinfo:
        parse_dsn(f"postgresql://u:p@db.example.test/x?{param}=whatever")
    assert excinfo.value.error == "unsupported connection parameter"


def test_the_unix_socket_smuggle_is_refused():
    """The red-team payload: a socket host hidden in a query parameter."""
    with pytest.raises(ValidationRejected) as excinfo:
        parse_dsn("postgresql://u:p@/x?host=/var/run/postgresql")
    assert excinfo.value.error == "unsupported connection parameter"


def test_options_injection_is_refused_before_the_socket_check():
    with pytest.raises(ValidationRejected) as excinfo:
        parse_dsn(
            "postgresql://u:p@/x?host=/var/run/postgresql&options=-c%20log_statement%3Dall"
        )
    assert excinfo.value.error == "unsupported connection parameter"


def test_a_socket_host_in_the_discrete_form_is_refused():
    with pytest.raises(ValidationRejected):
        parts_from_payload({"host": "/var/run/postgresql"})


def test_a_forbidden_parameter_as_a_discrete_field_is_refused():
    with pytest.raises(ValidationRejected):
        parts_from_payload({"host": "db.example.test", "options": "-c log_statement=all"})


def test_a_non_postgres_scheme_is_refused():
    with pytest.raises(ValidationRejected):
        parse_dsn("mysql://u:p@db.example.test/x")


@pytest.mark.parametrize("mode", ALLOWED_SSLMODES)
def test_allowed_sslmodes_pass(mode):
    payload = {"host": "db.example.test", "sslmode": mode}
    if mode == "disable":
        payload["allow_plaintext"] = True
    parts, _ = parts_from_payload(payload)
    assert parts.sslmode == mode


def test_an_unknown_sslmode_is_refused():
    with pytest.raises(ValidationRejected):
        parts_from_payload({"host": "db.example.test", "sslmode": "prefer"})


def test_sslmode_disable_needs_an_explicit_acceptance():
    with pytest.raises(ValidationRejected) as excinfo:
        parts_from_payload({"host": "db.example.test", "sslmode": "disable"})
    assert excinfo.value.error == "plaintext_refused"


def test_sslmode_defaults_to_require():
    parts, _ = parts_from_payload({"host": "db.example.test"})
    assert parts.sslmode == "require"


def test_the_link_local_metadata_range_is_refused():
    with pytest.raises(ValidationRejected) as excinfo:
        guard_addresses([LINK_LOCAL])
    assert excinfo.value.error == "address_refused"


def test_in_container_loopback_is_refused():
    with pytest.raises(ValidationRejected):
        guard_addresses([LOOPBACK])
    with pytest.raises(ValidationRejected):
        guard_addresses(["::1"])


def test_a_routable_address_is_allowed():
    guard_addresses([PRIVATE, DOC_RANGE])


def test_a_bad_port_is_refused():
    with pytest.raises(ValidationRejected):
        parts_from_payload({"host": "db.example.test", "port": "notanumber"})
    with pytest.raises(ValidationRejected):
        parts_from_payload({"host": "db.example.test", "port": "99999"})


def test_parts_never_repr_the_password():
    parts = DbParts(host="db.example.test", password="hunter2")
    assert "hunter2" not in repr(parts)
    assert "hunter2" not in str(parts.safe_dict())


def test_detail_sanitising_strips_credentials_and_control_characters():
    dirty = "failed: postgresql://cg:hunter2@db.example.test/x\npassword=hunter2"
    clean = sanitise_detail(dirty)
    assert "hunter2" not in clean
    assert "\n" not in clean


def test_an_ssl_refusal_is_classified_as_tls_not_as_a_connect_failure():
    """asyncpg reports a refused SSLRequest as a bare ConnectionError, and
    ssl.SSLError is itself an OSError — both must reach the TLS branch so the
    managed-database hint fires."""
    import ssl

    from installer.validate import _looks_like_tls_failure

    assert _looks_like_tls_failure(
        ConnectionError("server does not support SSL, but SSL was required")
    )
    assert _looks_like_tls_failure(ssl.SSLError("handshake failure"))
    assert not _looks_like_tls_failure(ConnectionRefusedError("connection refused"))
    assert not _looks_like_tls_failure(TimeoutError("timed out"))
