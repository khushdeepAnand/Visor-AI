import pytest
from services.field_encryption import encrypt_field, decrypt_field


def test_fields_are_randomized_authenticated_and_owner_bound(monkeypatch):
    monkeypatch.setenv("STOCKPILOT_FIELD_ENCRYPTION_KEY", "independent-test-field-key-material-32bytes")
    for field, plain in [("users.email", "person@example.invalid"), ("users.date_of_birth", "1990-01-01"), ("broker.access_token", "private-broker-value")]:
        encrypted = encrypt_field(plain, field=field, owner="1")
        assert plain not in encrypted
        assert encrypted != encrypt_field(plain, field=field, owner="1")
        assert decrypt_field(encrypted, field=field, owner="1") == plain
        with pytest.raises(ValueError, match="authentication"):
            decrypt_field(encrypted, field=field, owner="2")
        with pytest.raises(ValueError, match="authentication"):
            decrypt_field(encrypted, field="other", owner="1")
    monkeypatch.setenv("STOCKPILOT_FIELD_ENCRYPTION_KEY", "different-test-field-key-material-32bytes")
    with pytest.raises(ValueError, match="authentication"):
        decrypt_field(encrypted, field=field, owner="1")
    monkeypatch.delenv("STOCKPILOT_FIELD_ENCRYPTION_KEY")
    with pytest.raises(RuntimeError, match="persistent"):
        decrypt_field(encrypted, field=field, owner="1")
