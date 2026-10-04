import pytest

from services.db.sqlite_impl import SQLiteUserDAO, SQLiteSettingsDAO
from services.db.postgres_impl import PostgresUserDAO, PostgresSettingsDAO


@pytest.mark.parametrize("dao", [SQLiteUserDAO, PostgresUserDAO])
def test_user_updates_reject_identifier_injection_before_database_access(dao):
    with pytest.raises(ValueError, match="Unsupported"):
        dao(None).update_user(1, **{"name = 'owned' --": "value"})


@pytest.mark.parametrize("dao", [SQLiteSettingsDAO, PostgresSettingsDAO])
def test_setting_updates_reject_identifier_injection_before_database_access(dao):
    with pytest.raises(ValueError, match="Unsupported"):
        dao(None).update_settings(1, **{"user_id = 2 --": "value"})
