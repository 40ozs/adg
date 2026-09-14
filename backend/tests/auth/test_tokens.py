"""Token verification, including the attacks it exists to stop.

The OIDC verifier is exercised against a locally generated RSA key with its JWKS client
replaced, because a test that reached a tenant would be a test that fails when the network
does. What is *not* replaced is :func:`jwt.decode` and its options — the signature check,
the issuer and audience checks, and the algorithm allow-list are the behavior under test.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth.tokens import (
    DEVELOPMENT_AUDIENCE,
    DEVELOPMENT_ISSUER,
    DevelopmentTokenVerifier,
    OidcTokenVerifier,
    TokenError,
    build_verifier,
    issue_development_token,
)
from app.config import build_settings

ISSUER = "https://login.microsoftonline.com/tenant/v2.0"
AUDIENCE = "api://adg"
SECRET = "a-development-secret-that-is-long-enough"


# --------------------------------------------------------------------- development mode


class TestTheDevelopmentVerifier:
    def test_it_accepts_a_token_this_process_issued(self) -> None:
        token, expires_at = issue_development_token(
            secret=SECRET,
            subject="dev:admin",
            roles=["admin"],
            display_name="Development Administrator",
            email="admin@development.invalid",
            lifetime=dt.timedelta(minutes=5),
        )

        claims = DevelopmentTokenVerifier(secret=SECRET).verify(token)

        assert claims["sub"] == "dev:admin"
        assert claims["roles"] == ["admin"]
        assert claims["iss"] == DEVELOPMENT_ISSUER
        assert claims["aud"] == DEVELOPMENT_AUDIENCE
        assert expires_at > dt.datetime.now(tz=dt.UTC)

    def test_it_refuses_a_token_signed_with_another_secret(self) -> None:
        token, _ = issue_development_token(
            secret="a-different-secret-entirely-and-long",
            subject="dev:admin",
            roles=["admin"],
            display_name="x",
            email=None,
            lifetime=dt.timedelta(minutes=5),
        )

        with pytest.raises(TokenError, match="could not be verified"):
            DevelopmentTokenVerifier(secret=SECRET).verify(token)

    def test_it_refuses_an_expired_token_and_says_how_to_recover(self) -> None:
        token, _ = issue_development_token(
            secret=SECRET,
            subject="dev:admin",
            roles=["admin"],
            display_name="x",
            email=None,
            lifetime=dt.timedelta(minutes=-120),
        )

        with pytest.raises(TokenError, match="expired"):
            DevelopmentTokenVerifier(secret=SECRET).verify(token)

    def test_it_refuses_a_tenant_token(self) -> None:
        """A real token must not be accepted by the development verifier either: the two
        modes share no trust, in both directions."""
        token = jwt.encode(
            {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "sub": "x",
                "exp": _in(dt.timedelta(minutes=5)),
            },
            SECRET,
            algorithm="HS256",
        )

        with pytest.raises(TokenError):
            DevelopmentTokenVerifier(secret=SECRET).verify(token)

    def test_it_refuses_a_token_with_no_expiry(self) -> None:
        token = jwt.encode(
            {"iss": DEVELOPMENT_ISSUER, "aud": DEVELOPMENT_AUDIENCE, "sub": "x"},
            SECRET,
            algorithm="HS256",
        )

        with pytest.raises(TokenError):
            DevelopmentTokenVerifier(secret=SECRET).verify(token)

    def test_it_cannot_be_built_without_a_secret(self) -> None:
        with pytest.raises(ValueError, match="secret is required"):
            DevelopmentTokenVerifier(secret="")

    def test_it_refuses_a_token_signed_with_none(self) -> None:
        """The oldest JWT attack: alg=none. Passing an explicit algorithm list is what
        forecloses it, and this is the test that proves the list is passed."""
        unsigned = jwt.encode(
            {
                "iss": DEVELOPMENT_ISSUER,
                "aud": DEVELOPMENT_AUDIENCE,
                "sub": "dev:admin",
                "roles": ["admin"],
                "exp": _in(dt.timedelta(minutes=5)),
            },
            key="",
            algorithm="none",
        )

        with pytest.raises(TokenError):
            DevelopmentTokenVerifier(secret=SECRET).verify(unsigned)


# ---------------------------------------------------------------------------- OIDC mode


@pytest.fixture(scope="module")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def oidc_verifier(
    signing_key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> OidcTokenVerifier:
    """A verifier whose JWKS endpoint is this test's key, and nothing else replaced."""
    verifier = OidcTokenVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_url="https://login.microsoftonline.com/tenant/discovery/v2.0/keys",
    )

    class LocalJwks:
        def get_signing_key_from_jwt(self, token: str) -> Any:
            class Key:
                key = signing_key.public_key()

            return Key()

    monkeypatch.setattr(verifier, "_jwks", LocalJwks())
    return verifier


def rs256(signing_key: rsa.RSAPrivateKey, **overrides: Any) -> str:
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "00000000-0000-0000-0000-000000000001",
        "roles": ["viewer"],
        "exp": _in(dt.timedelta(minutes=10)),
    }
    claims.update(overrides)
    return jwt.encode(claims, signing_key, algorithm="RS256")


class TestTheOidcVerifier:
    def test_it_accepts_a_correctly_issued_token(
        self, oidc_verifier: OidcTokenVerifier, signing_key: rsa.RSAPrivateKey
    ) -> None:
        claims = oidc_verifier.verify(rs256(signing_key))

        assert claims["sub"] == "00000000-0000-0000-0000-000000000001"
        assert claims["roles"] == ["viewer"]

    def test_it_refuses_a_token_for_another_audience(
        self, oidc_verifier: OidcTokenVerifier, signing_key: rsa.RSAPrivateKey
    ) -> None:
        with pytest.raises(TokenError, match="different audience"):
            oidc_verifier.verify(rs256(signing_key, aud="api://something-else"))

    def test_it_refuses_a_token_from_another_issuer(
        self, oidc_verifier: OidcTokenVerifier, signing_key: rsa.RSAPrivateKey
    ) -> None:
        """A token from another tenant is signed by a key ADG never fetched, but the issuer
        check has to bite on its own: a multi-tenant JWKS would otherwise validate it."""
        with pytest.raises(TokenError, match="different issuer"):
            oidc_verifier.verify(
                rs256(signing_key, iss="https://login.microsoftonline.com/other/v2.0")
            )

    def test_it_refuses_an_expired_token(
        self, oidc_verifier: OidcTokenVerifier, signing_key: rsa.RSAPrivateKey
    ) -> None:
        with pytest.raises(TokenError, match="expired"):
            oidc_verifier.verify(rs256(signing_key, exp=_in(dt.timedelta(minutes=-10))))

    def test_it_refuses_a_token_with_no_subject(
        self, oidc_verifier: OidcTokenVerifier, signing_key: rsa.RSAPrivateKey
    ) -> None:
        token = jwt.encode(
            {"iss": ISSUER, "aud": AUDIENCE, "exp": _in(dt.timedelta(minutes=5))},
            signing_key,
            algorithm="RS256",
        )

        with pytest.raises(TokenError):
            oidc_verifier.verify(token)

    def test_it_refuses_a_symmetric_token_signed_with_the_public_key(
        self, oidc_verifier: OidcTokenVerifier, signing_key: rsa.RSAPrivateKey
    ) -> None:
        """Algorithm confusion, the attack that makes asymmetric verification worth having.

        The public key is public. If the verifier accepted HS256, anyone could sign a token
        with the published key material and be believed. The allow-list is what stops it.
        """
        public_pem = signing_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        # Assembled by hand rather than with jwt.encode, which refuses to sign an HMAC
        # token with PEM key material. An attacker has no such scruples, so the test does
        # not either: this is the wire format a forgery would arrive in.
        forged = _hs256_by_hand(
            {"alg": "HS256", "typ": "JWT"},
            {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "sub": "attacker",
                "roles": ["admin"],
                "exp": _in(dt.timedelta(minutes=10)),
            },
            key=public_pem,
        )

        with pytest.raises(TokenError):
            oidc_verifier.verify(forged)

    def test_it_refuses_an_unsigned_token(
        self, oidc_verifier: OidcTokenVerifier, signing_key: rsa.RSAPrivateKey
    ) -> None:
        unsigned = jwt.encode(
            {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "sub": "attacker",
                "roles": ["admin"],
                "exp": _in(dt.timedelta(minutes=10)),
            },
            key="",
            algorithm="none",
        )

        with pytest.raises(TokenError):
            oidc_verifier.verify(unsigned)

    def test_a_malformed_token_fails_as_a_token_problem(
        self, oidc_verifier: OidcTokenVerifier
    ) -> None:
        with pytest.raises(TokenError):
            oidc_verifier.verify("not-a-jwt")


class TestChoosingTheVerifier:
    def test_development_settings_select_the_development_verifier(self) -> None:
        verifier = build_verifier(build_settings(environment="development"))

        assert isinstance(verifier, DevelopmentTokenVerifier)
        assert verifier.source == "development"

    def test_oidc_settings_select_the_oidc_verifier(self) -> None:
        verifier = build_verifier(
            build_settings(
                environment="production",
                database_url="postgresql+psycopg://adg:secret@db.internal:5432/adg",
                auth_mode="oidc",
                oidc_issuer=ISSUER,
                oidc_audience=AUDIENCE,
                oidc_jwks_url="https://login.microsoftonline.com/tenant/discovery/v2.0/keys",
            )
        )

        assert isinstance(verifier, OidcTokenVerifier)
        assert verifier.source == "oidc"


def _hs256_by_hand(header: dict[str, Any], payload: dict[str, Any], *, key: bytes) -> str:
    signing_input = b".".join(_segment(header) for header in (header, payload))
    signature = hmac.new(key, signing_input, hashlib.sha256).digest()
    return b".".join([signing_input, base64.urlsafe_b64encode(signature).rstrip(b"=")]).decode(
        "ascii"
    )


def _segment(payload: dict[str, Any]) -> bytes:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def _in(delta: dt.timedelta) -> int:
    return int((dt.datetime.now(tz=dt.UTC) + delta).timestamp())
