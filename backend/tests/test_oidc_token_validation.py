# SPDX-License-Identifier: AGPL-3.0-only
"""Behavioural contract for OIDC id_token verification (``oidc._decode_with_jwks``).

``backend/oidc.py`` is the only consumer of Authlib, through ``authlib.jose``.
Until this file nothing in the suite drove a token through that path, so an
Authlib upgrade could change what the backend accepts and CI would stay green.

The tokens here are built with ``cryptography`` alone, not with Authlib, so the
library under test does not also write its own exam. The tests pin three
things an upgrade must not move:

* a correctly signed token from the issuer is accepted,
* every forgery or stale token is rejected, and
* the rejection is an ``authlib.jose.errors.JoseError``, because that is the
  exception ``validate_oidc_token`` turns into a 401. A different exception
  type would surface as a 500.
"""

import base64
import hashlib
import hmac
import json
import time

import pytest
from authlib.jose.errors import JoseError
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from fastapi import HTTPException

import oidc

ISSUER = "https://idp.example.test/realms/scf"
CLIENT_ID = "scf-web"
RSA_KID = "rsa-key-1"
EC_KID = "ec-key-1"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64_uint(value: int, length: int | None = None) -> str:
    size = length or (value.bit_length() + 7) // 8
    return _b64(value.to_bytes(size, "big"))


def _segment(obj: dict) -> str:
    return _b64(json.dumps(obj, separators=(",", ":")).encode("utf-8"))


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def ec_key():
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture(scope="module")
def jwks(rsa_key, ec_key):
    rsa_pub = rsa_key.public_key().public_numbers()
    ec_pub = ec_key.public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": RSA_KID,
                "use": "sig",
                "n": _b64_uint(rsa_pub.n),
                "e": _b64_uint(rsa_pub.e),
            },
            {
                "kty": "EC",
                "kid": EC_KID,
                "use": "sig",
                "crv": "P-256",
                "x": _b64_uint(ec_pub.x, 32),
                "y": _b64_uint(ec_pub.y, 32),
            },
        ]
    }


@pytest.fixture(autouse=True)
def _oidc_configured(monkeypatch):
    monkeypatch.setattr(oidc, "OIDC_ISSUER", ISSUER)
    monkeypatch.setattr(oidc, "OIDC_CLIENT_ID", CLIENT_ID)


def _claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "user-123",
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def _rs256(key, claims: dict, kid: str = RSA_KID) -> str:
    signing_input = f"{_segment({'alg': 'RS256', 'typ': 'JWT', 'kid': kid})}.{_segment(claims)}"
    signature = key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64(signature)}"


def _es256(key, claims: dict, kid: str = EC_KID) -> str:
    signing_input = f"{_segment({'alg': 'ES256', 'typ': 'JWT', 'kid': kid})}.{_segment(claims)}"
    der = key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return f"{signing_input}.{_b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


# ---------------------------------------------------------------------------
# Accepted
# ---------------------------------------------------------------------------


def test_valid_rs256_token_is_accepted(rsa_key, jwks):
    claims = oidc._decode_with_jwks(_rs256(rsa_key, _claims()), jwks)
    assert claims["sub"] == "user-123"
    assert claims["iss"] == ISSUER
    assert isinstance(claims, dict)


def test_valid_es256_token_is_accepted(ec_key, jwks):
    claims = oidc._decode_with_jwks(_es256(ec_key, _claims()), jwks)
    assert claims["sub"] == "user-123"


def test_expiry_inside_clock_leeway_is_accepted(rsa_key, jwks):
    now = int(time.time())
    token = _rs256(rsa_key, _claims(iat=now - 600, exp=now - 5))
    assert oidc._decode_with_jwks(token, jwks)["sub"] == "user-123"


# ---------------------------------------------------------------------------
# Rejected, and rejected as JoseError
# ---------------------------------------------------------------------------


def test_tampered_payload_is_rejected(rsa_key, jwks):
    header, _payload, signature = _rs256(rsa_key, _claims()).split(".")
    forged = f"{header}.{_segment(_claims(sub='admin'))}.{signature}"
    with pytest.raises(JoseError):
        oidc._decode_with_jwks(forged, jwks)


def test_token_signed_by_another_key_is_rejected(other_rsa_key, jwks):
    with pytest.raises(JoseError):
        oidc._decode_with_jwks(_rs256(other_rsa_key, _claims()), jwks)


def test_empty_signature_is_rejected(rsa_key, jwks):
    header, payload, _signature = _rs256(rsa_key, _claims()).split(".")
    with pytest.raises(JoseError):
        oidc._decode_with_jwks(f"{header}.{payload}.", jwks)


def test_alg_none_is_rejected(jwks):
    token = f"{_segment({'alg': 'none', 'typ': 'JWT', 'kid': RSA_KID})}.{_segment(_claims())}."
    with pytest.raises(JoseError):
        oidc._decode_with_jwks(token, jwks)


def test_hmac_signed_with_the_public_key_is_rejected(rsa_key, jwks):
    """Algorithm confusion: HS256 keyed with the issuer's public key."""
    public_pem = rsa_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    signing_input = f"{_segment({'alg': 'HS256', 'typ': 'JWT', 'kid': RSA_KID})}.{_segment(_claims())}"
    signature = hmac.new(public_pem, signing_input.encode("ascii"), hashlib.sha256).digest()
    with pytest.raises(JoseError):
        oidc._decode_with_jwks(f"{signing_input}.{_b64(signature)}", jwks)


def test_expired_token_is_rejected(rsa_key, jwks):
    now = int(time.time())
    token = _rs256(rsa_key, _claims(iat=now - 7200, exp=now - 3600))
    with pytest.raises(JoseError):
        oidc._decode_with_jwks(token, jwks)


def test_wrong_issuer_is_rejected(rsa_key, jwks):
    token = _rs256(rsa_key, _claims(iss="https://attacker.example.test/realms/scf"))
    with pytest.raises(JoseError):
        oidc._decode_with_jwks(token, jwks)


def test_missing_issuer_is_rejected(rsa_key, jwks):
    token = _rs256(rsa_key, _claims(iss=None))
    with pytest.raises(JoseError):
        oidc._decode_with_jwks(token, jwks)


# ---------------------------------------------------------------------------
# The caller: a rejection must reach the client as 401, never as a 500
# ---------------------------------------------------------------------------


@pytest.fixture
def _served_jwks(monkeypatch, jwks):
    async def _get_jwks(force_refresh: bool = False):
        return jwks

    monkeypatch.setattr(oidc, "get_jwks", _get_jwks)


@pytest.mark.asyncio
async def test_validate_oidc_token_returns_claims(rsa_key, _served_jwks):
    claims = await oidc.validate_oidc_token(_rs256(rsa_key, _claims()))
    assert claims["sub"] == "user-123"


@pytest.mark.asyncio
async def test_validate_oidc_token_maps_forgery_to_401(rsa_key, _served_jwks):
    header, _payload, signature = _rs256(rsa_key, _claims()).split(".")
    forged = f"{header}.{_segment(_claims(sub='admin'))}.{signature}"
    with pytest.raises(HTTPException) as excinfo:
        await oidc.validate_oidc_token(forged)
    assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_validate_oidc_token_maps_expiry_to_401(rsa_key, _served_jwks):
    now = int(time.time())
    token = _rs256(rsa_key, _claims(iat=now - 7200, exp=now - 3600))
    with pytest.raises(HTTPException) as excinfo:
        await oidc.validate_oidc_token(token)
    assert excinfo.value.status_code == 401
