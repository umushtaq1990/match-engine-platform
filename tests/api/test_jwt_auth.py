"""Unit tests for app/api/jwt_auth.py's get_verified_claims (issue #26) --
the real signature-verification logic, tested with a throwaway RSA keypair
rather than a real Keycloak round-trip (route-level tests elsewhere
instead override get_verified_claims directly, same as they already
override get_db).
"""
import time

import pytest
from cryptography.hazmat.primitives import serialization
from fastapi import HTTPException
from jose import jwt
from jose.utils import long_to_base64

from app.api import jwt_auth

_KID = "test-key-1"


@pytest.fixture
def rsa_keypair():
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key


def _jwk_for(private_key) -> dict:
    public_numbers = private_key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": _KID,
        "use": "sig",
        "alg": "RS256",
        "n": long_to_base64(public_numbers.n).decode("ascii"),
        "e": long_to_base64(public_numbers.e).decode("ascii"),
    }


def _sign(private_key, claims: dict) -> str:
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": _KID})


def _base_claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "sub": "user-abc", "iss": jwt_auth.OIDC_INTERNAL_ISSUER, "azp": jwt_auth.OIDC_CLIENT_ID,
        "iat": now, "exp": now + 300,
    }
    claims.update(overrides)
    return claims


def _patch_jwks(monkeypatch, jwks: dict, fail_on_call: bool = False):
    calls = {"count": 0}

    def _fake_fetch(force: bool = False):
        calls["count"] += 1
        if fail_on_call:
            raise AssertionError(
                "JWKS was fetched but the request should have failed before any network call"
            )
        return jwks

    monkeypatch.setattr(jwt_auth, "_fetch_jwks", _fake_fetch)
    return calls


def test_valid_token_returns_correct_claims(rsa_keypair, monkeypatch):
    _patch_jwks(monkeypatch, {"keys": [_jwk_for(rsa_keypair)]})
    token = _sign(rsa_keypair, _base_claims(sub="user-real"))

    claims = jwt_auth.get_verified_claims(authorization=f"Bearer {token}")

    assert claims["sub"] == "user-real"


def test_expired_token_is_rejected(rsa_keypair, monkeypatch):
    _patch_jwks(monkeypatch, {"keys": [_jwk_for(rsa_keypair)]})
    now = int(time.time())
    token = _sign(rsa_keypair, _base_claims(iat=now - 600, exp=now - 300))

    with pytest.raises(HTTPException) as exc_info:
        jwt_auth.get_verified_claims(authorization=f"Bearer {token}")
    assert exc_info.value.status_code == 401


def test_wrong_issuer_is_rejected(rsa_keypair, monkeypatch):
    _patch_jwks(monkeypatch, {"keys": [_jwk_for(rsa_keypair)]})
    token = _sign(rsa_keypair, _base_claims(iss="http://evil.example/realms/jme"))

    with pytest.raises(HTTPException) as exc_info:
        jwt_auth.get_verified_claims(authorization=f"Bearer {token}")
    assert exc_info.value.status_code == 401


def test_wrong_client_azp_is_rejected(rsa_keypair, monkeypatch):
    _patch_jwks(monkeypatch, {"keys": [_jwk_for(rsa_keypair)]})
    token = _sign(rsa_keypair, _base_claims(azp="some-other-client"))

    with pytest.raises(HTTPException) as exc_info:
        jwt_auth.get_verified_claims(authorization=f"Bearer {token}")
    assert exc_info.value.status_code == 401


def test_token_signed_by_an_untrusted_key_is_rejected(rsa_keypair, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import rsa as rsa_mod

    untrusted_key = rsa_mod.generate_private_key(public_exponent=65537, key_size=2048)
    # JWKS only knows about rsa_keypair's public key -- the token is signed
    # by a different, untrusted private key.
    _patch_jwks(monkeypatch, {"keys": [_jwk_for(rsa_keypair)]})
    token = _sign(untrusted_key, _base_claims())

    with pytest.raises(HTTPException) as exc_info:
        jwt_auth.get_verified_claims(authorization=f"Bearer {token}")
    assert exc_info.value.status_code == 401


def test_missing_header_is_rejected_without_any_network_call(monkeypatch):
    calls = _patch_jwks(monkeypatch, {"keys": []}, fail_on_call=True)

    with pytest.raises(HTTPException) as exc_info:
        jwt_auth.get_verified_claims(authorization=None)
    assert exc_info.value.status_code == 401
    assert calls["count"] == 0


def test_malformed_header_is_rejected_without_any_network_call(monkeypatch):
    calls = _patch_jwks(monkeypatch, {"keys": []}, fail_on_call=True)

    with pytest.raises(HTTPException) as exc_info:
        jwt_auth.get_verified_claims(authorization="not-a-bearer-token")
    assert exc_info.value.status_code == 401
    assert calls["count"] == 0
