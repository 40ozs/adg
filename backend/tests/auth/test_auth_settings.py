"""Startup refusals around authentication.

The most valuable behavior in this phase is a process that does not start. A production
deployment that came up with development authentication would be an open front door onto a
map of every weak permission in the estate, so the refusal is asserted from several
directions here rather than trusted to one validator.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.auth.roles import Role
from app.config import MIN_COLLECTOR_KEY_LENGTH, build_settings

PRODUCTION = {
    "environment": "production",
    "database_url": "postgresql+psycopg://adg:secret@db.internal:5432/adg",
}
TENANT = {
    "oidc_issuer": "https://login.microsoftonline.com/tenant/v2.0",
    "oidc_audience": "api://adg",
    "oidc_jwks_url": "https://login.microsoftonline.com/tenant/discovery/v2.0/keys",
}


class TestProductionRefusesDevelopmentAuthentication:
    def test_the_process_does_not_start(self) -> None:
        with pytest.raises(ValidationError, match="ADG_AUTH_MODE=development cannot be used"):
            build_settings(**PRODUCTION)

    def test_the_message_names_what_to_set_instead(self) -> None:
        with pytest.raises(ValidationError) as caught:
            build_settings(**PRODUCTION, auth_mode="development")

        message = str(caught.value)
        assert "ADG_OIDC_ISSUER" in message
        assert "ADG_OIDC_AUDIENCE" in message
        assert "ADG_OIDC_JWKS_URL" in message

    def test_production_with_a_configured_tenant_starts(self) -> None:
        settings = build_settings(**PRODUCTION, auth_mode="oidc", **TENANT)

        assert settings.is_development_auth is False


class TestOidcConfiguration:
    def test_every_missing_value_is_named_at_once(self) -> None:
        with pytest.raises(ValidationError) as caught:
            build_settings(auth_mode="oidc")

        message = str(caught.value)
        assert "ADG_OIDC_ISSUER" in message
        assert "ADG_OIDC_AUDIENCE" in message
        assert "ADG_OIDC_JWKS_URL" in message

    @pytest.mark.parametrize("field", ["oidc_issuer", "oidc_jwks_url"])
    def test_plain_http_is_refused(self, field: str) -> None:
        """Verifying signatures over http would let anything on the path mint identities."""
        values = {**TENANT, field: "http://login.microsoftonline.com/tenant/v2.0"}

        with pytest.raises(ValidationError, match="must be an https URL"):
            build_settings(auth_mode="oidc", **values)

    def test_scopes_are_split_on_whitespace(self) -> None:
        settings = build_settings(oidc_scopes="openid  profile\temail")

        assert settings.oidc_scope_list == ["openid", "profile", "email"]


class TestTheGroupRoleMap:
    def test_pairs_are_parsed_and_case_folded(self) -> None:
        settings = build_settings(
            oidc_group_role_map="11111111-1111-1111-1111-111111111111:auditor"
        )

        assert settings.group_role_map == {"11111111-1111-1111-1111-111111111111": Role.AUDITOR}

    def test_an_unknown_role_is_refused_at_startup(self) -> None:
        """Not on the first sign-in attempt. An error at startup names the entry; the same
        error later is a user reporting a blank screen."""
        with pytest.raises(ValidationError, match="unknown role"):
            build_settings(oidc_group_role_map="11111111:not-a-role")

    def test_a_malformed_entry_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="is not '<group object id>:<role>'"):
            build_settings(oidc_group_role_map="just-a-group-id")


class TestCollectorKeys:
    SECRET = "k" * MIN_COLLECTOR_KEY_LENGTH

    def test_keys_are_parsed_by_id(self) -> None:
        settings = build_settings(collector_api_keys=f"fs01:{self.SECRET}")

        assert settings.collector_key_map == {"fs01": self.SECRET}

    def test_no_keys_configured_is_an_empty_map_rather_than_an_error(self) -> None:
        """Ingestion then requires an administrator's token. It is never anonymous."""
        assert build_settings().collector_key_map == {}

    def test_a_short_key_is_refused_with_a_way_to_generate_one(self) -> None:
        with pytest.raises(ValidationError) as caught:
            build_settings(collector_api_keys="fs01:tooshort")

        assert "Get-Random" in str(caught.value)

    def test_a_duplicate_key_id_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="more than once"):
            build_settings(collector_api_keys=f"fs01:{self.SECRET},fs01:{'j' * 40}")

    def test_a_malformed_entry_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="is not '<key id>:<secret>'"):
            build_settings(collector_api_keys="no-separator-here")


class TestTheDevelopmentSigningSecret:
    def test_one_is_generated_when_none_is_configured(self) -> None:
        settings = build_settings()

        assert len(settings.resolved_dev_auth_secret) >= 32

    def test_two_processes_do_not_share_it(self) -> None:
        """Generated rather than defaulted to a constant: a constant in the repository is a
        secret in the repository, and somebody would eventually verify a real token with
        it."""
        assert build_settings().resolved_dev_auth_secret != (
            build_settings().resolved_dev_auth_secret
        )

    def test_a_configured_secret_is_used_as_given(self) -> None:
        settings = build_settings(dev_auth_secret="a-secret-chosen-by-the-developer")

        assert settings.resolved_dev_auth_secret == "a-secret-chosen-by-the-developer"

    def test_no_secret_is_generated_for_a_tenant_deployment(self) -> None:
        settings = build_settings(auth_mode="oidc", **TENANT)

        assert settings.resolved_dev_auth_secret == ""


class TestTheDevelopmentAccountList:
    def test_it_is_validated_at_startup(self) -> None:
        with pytest.raises(ValidationError, match="unknown role"):
            build_settings(dev_auth_users="dana:superuser")

    def test_it_is_not_validated_when_the_mode_cannot_use_it(self) -> None:
        """A tenant deployment with a stale ADG_DEV_AUTH_USERS in its environment must
        still start; the value is unreachable, so it cannot be a startup failure."""
        settings = build_settings(auth_mode="oidc", dev_auth_users="dana:superuser", **TENANT)

        assert settings.is_development_auth is False

    def test_the_token_lifetime_is_bounded(self) -> None:
        with pytest.raises(ValidationError):
            build_settings(dev_auth_token_lifetime_minutes=0)
        with pytest.raises(ValidationError):
            build_settings(dev_auth_token_lifetime_minutes=10_000)
