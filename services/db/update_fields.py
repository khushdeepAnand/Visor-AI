"""Shared identifier allowlists for both persistence backends."""

USER_FIELDS = frozenset({"name", "email", "password", "auth_provider", "google_id", "last_login_at", "date_of_birth", "account_status", "role", "token_version"})
SETTINGS_FIELDS = frozenset({"theme", "currency", "default_period"})


def validate_fields(fields: dict[str, object], allowed: frozenset[str]) -> None:
    if set(fields) - allowed:
        raise ValueError("Unsupported database update field.")
