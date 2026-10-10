"""Inspect DAO API parity without constructing factories or opening connections.

Checks public methods/properties, parameter names/kinds/defaults/annotations and
return annotations. Backend-specific constructors and private helpers are not a
shared API. Driver return types declared Any in DatabaseInterface and the factory
db property's concrete wrapper types are deliberately normalized to that contract.
This is signature evidence, not SQL correctness or behavioral-parity evidence.
"""
from __future__ import annotations

import inspect
import argparse
import os
import sys
import tempfile
import uuid
import json
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.db.base import DatabaseInterface


def _surface(cls: type) -> dict[str, Any]:
    return {name: member for name, member in inspect.getmembers(cls)
            if not name.startswith("_") and (inspect.isfunction(member) or isinstance(member, property))}


def _signature(member: Any, contract: Any = None) -> inspect.Signature:
    function = member.fget if isinstance(member, property) else member
    if function is None:
        raise ValueError("DAO properties must have a readable getter")
    signature = inspect.signature(function, eval_str=True)
    annotation = signature.return_annotation
    if contract is not None and inspect.signature(contract, eval_str=True).return_annotation is Any:
        annotation = Any  # DB-API connections/cursors differ by driver, by design.
    elif isinstance(annotation, type) and issubclass(annotation, DatabaseInterface):
        annotation = DatabaseInterface  # factory.db is the corresponding wrapper.
    return signature.replace(return_annotation=annotation)


def compare_classes(left: type, right: type, *, interface: type | None = None) -> list[str]:
    issues = []
    first, second = _surface(left), _surface(right)
    for name in sorted(first.keys() | second.keys()):
        label = f"{left.__name__}/{right.__name__}.{name}"
        if name not in first or name not in second:
            issues.append(f"{label}: missing from {left.__name__ if name not in first else right.__name__}")
            continue
        if getattr(first[name], "__isabstractmethod__", False) or getattr(second[name], "__isabstractmethod__", False):
            issues.append(f"{label}: inherited abstract method is not an implementation")
            continue
        if isinstance(first[name], property) != isinstance(second[name], property):
            issues.append(f"{label}: property/method mismatch")
            continue
        contract = getattr(interface, name, None) if interface else None
        expected, actual = _signature(first[name], contract), _signature(second[name], contract)
        if expected != actual:
            issues.append(f"{label}: signature mismatch: {expected} != {actual}")
    return issues


def check_parity(sqlite_module: ModuleType | None = None, postgres_module: ModuleType | None = None) -> list[str]:
    if sqlite_module is None or postgres_module is None:
        from services.db import sqlite_impl, postgres_impl
        sqlite_module = sqlite_module or sqlite_impl
        postgres_module = postgres_module or postgres_impl
    left = {name.removeprefix("SQLite"): cls for name, cls in inspect.getmembers(sqlite_module, inspect.isclass)
            if name.startswith("SQLite") and cls.__module__ == sqlite_module.__name__}
    right = {name.removeprefix("Postgres"): cls for name, cls in inspect.getmembers(postgres_module, inspect.isclass)
             if name.startswith("Postgres") and cls.__module__ == postgres_module.__name__}
    issues = []
    if not left or not right:
        issues.append("No concrete backend classes discovered")
    for name in sorted(left.keys() | right.keys()):
        if name not in left or name not in right:
            issues.append(f"{name}: missing backend class")
            continue
        issues.extend(compare_classes(left[name], right[name], interface=DatabaseInterface if name == "Database" else None))
    return issues


def check_live_parity(url: str) -> list[str]:
    """Exercise real DAOs in a unique schema on a loopback disposable database.

    Never uses the user's SQLite source or a remote production database. The
    generated test schema is the only PostgreSQL state removed on completion.
    """
    import database
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import parse_dsn, make_dsn
    from sqlalchemy.engine import make_url
    from alembic import command
    from alembic.config import Config
    from services.db.postgres_impl import PostgresDAOFactory
    from services.db.sqlite_impl import SQLiteDAOFactory

    connection_info = parse_dsn(url)
    if connection_info.get("host") not in {"127.0.0.1", "localhost", "::1"} or not connection_info.get("dbname", "").startswith("stockpilot_test"):
        raise ValueError("Live parity requires a loopback disposable database named stockpilot_test*")
    schema = "parity_" + uuid.uuid4().hex
    admin = psycopg2.connect(url)
    admin.autocommit = True
    postgres = None
    sqlite = None
    saved = (database.DATABASE, database.DATABASE_DIR, database.SQLCIPHER_KEY, database.SQLCIPHER_KEY_FILE)
    completed = []
    try:
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        scoped_url = make_url(url).update_query_dict({"options": "-csearch_path=" + schema})
        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "alembic"))
        cfg.set_main_option("sqlalchemy.url", scoped_url.render_as_string(hide_password=False).replace("%", "%%"))
        command.upgrade(cfg, "head")
        postgres = PostgresDAOFactory(make_dsn(url, options="-csearch_path=" + schema), pool_size=10)
        version = postgres.db.fetchone("SELECT version_num FROM alembic_version")
        assert version is not None and version["version_num"] == "20261010_02"
        completed.append("clean PostgreSQL Alembic upgrade and inspected revision")
        with tempfile.TemporaryDirectory(prefix="stockpilot-parity-") as temporary:
            database.DATABASE = str(Path(temporary) / "parity.db")
            database.DATABASE_DIR = temporary
            database.SQLCIPHER_KEY = database.SQLCIPHER_KEY_FILE = None
            database.create_tables()
            from services.webauthn import _ensure_table
            from services.login_anomaly import _ensure_tables
            from services.admin_registry import _ensure_settings_table
            from services.admin_step_up import STEP_UP
            from services.forecast_guardrails import GUARDRAILS
            from services.chart_layouts import CHART_LAYOUTS
            from services.screener import SCREENS
            from services.strategy_builder import STRATEGIES
            from services.forward_test import FORWARD_TESTS
            _ensure_table()
            _ensure_tables()
            connection = database.get_connection()
            try:
                _ensure_settings_table(connection)
                connection.commit()
                STEP_UP._ensure_schema(connection)
                GUARDRAILS._ensure_schema(connection)
            finally:
                connection.close()
            CHART_LAYOUTS._connect().close()
            SCREENS._connect().close()
            STRATEGIES.ensure_schema()
            FORWARD_TESTS.ensure_schema()
            sqlite = SQLiteDAOFactory()
            sqlite_tables = {row["name"] for row in sqlite.db.fetchall("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            pg_tables = {row["table_name"] for row in postgres.db.fetchall("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_name <> 'alembic_version'")}
            assert sqlite_tables == pg_tables, "runtime tables differ from migrated schema"
            for table in sorted(sqlite_tables):
                sqlite_columns = {row["name"] for row in sqlite.db.fetchall('PRAGMA table_info("' + table + '")')}
                pg_columns = {row["column_name"] for row in postgres.db.fetchall("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s", (table,))}
                assert sqlite_columns == pg_columns, "runtime columns differ: " + table
            completed.append(f"all {len(sqlite_tables)} eagerly/lazily created runtime tables and columns match migrated PostgreSQL schema")
            unprotected = postgres.db.fetchone("SELECT count(*) AS n FROM pg_class WHERE relnamespace=current_schema()::regnamespace AND relkind='r' AND relname <> 'alembic_version' AND NOT relrowsecurity")
            assert unprotected is not None and unprotected["n"] == 0
            completed.append("RLS enabled on every application table; no public policies introduced")
            outcomes = [_probe_factory(factory) for factory in (sqlite, postgres)]
            assert outcomes[0] == outcomes[1], "DAO result/behavior divergence"
            completed.append("real SQLite/PostgreSQL behavior for all eight DAOs, ownership, provenance, official eligibility, duplicates and recovery consumption")
            _probe_runtime_helpers(make_dsn(url, options="-csearch_path=" + schema), postgres, sqlite)
            completed.append("explicit PostgreSQL selection routes portfolio/watchlist/prediction/audit and password/OIDC authentication through shared persistence with no SQLite writes")
            completed.append("real PostgreSQL JWT sessions, revocation/ownership, encrypted TOTP, device-bound challenge caps/replay, concurrent recovery consumption and real P-256 WebAuthn verification")
            _probe_rls(admin, schema)
            completed.append("unprivileged role denied by grants; granting SELECT still returns zero rows under policy-free RLS; owner connection bypass identified")
            _probe_concurrency(postgres)
            completed.append("eight concurrent holding sales/watchlist inserts/recovery consumers with atomic ledger checks")
            for factory in (sqlite, postgres):
                placeholder = "%s" if factory is postgres else "?"
                original = factory.db.fetchone("SELECT count(*) AS n FROM users")
                assert original is not None
                count = original["n"]
                factory.db.execute(_probe_sql("INSERT INTO users(name,email,password) VALUES(?,?,?)", placeholder), ("Rollback", "rollback@example.test", "disabled"))
                factory.db.rollback()
                restored_count = factory.db.fetchone("SELECT count(*) AS n FROM users")
                assert restored_count is not None and restored_count["n"] == count
            completed.append("explicit transaction rollback on both real backends")
            sqlite.db.close()
            sqlite = None
            _probe_migration(admin, url, cfg, Path(database.DATABASE), Path(temporary), schema + "_transfer")
            completed.append("real SQLite-to-PostgreSQL transfer, authenticated backup restore, full-content reconciliation and idempotent retry on disposable data")
    finally:
        if sqlite is not None:
            sqlite.db.close()
        database.DATABASE, database.DATABASE_DIR, database.SQLCIPHER_KEY, database.SQLCIPHER_KEY_FILE = saved
        if postgres is not None:
            postgres.db._shutdown_pool()
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        admin.close()
    return completed


def _probe_runtime_helpers(url: str, postgres: Any, sqlite: Any) -> None:
    import database
    import authentication
    from services.db.factory import close_dao_pools
    names = ("DB_BACKEND", "DATABASE_URL")
    saved = {name: os.environ.get(name) for name in names}
    original = sqlite.db.fetchone("SELECT count(*) AS n FROM portfolio")["n"]
    uid = postgres.create_user_dao().create_user("Runtime", "runtime@example.test", "disabled")
    try:
        os.environ["DB_BACKEND"] = "postgresql"
        os.environ["DATABASE_URL"] = url
        database.buy_stock(uid, "TCS", "TCS", 4, 100)
        holding = database.get_portfolio(uid)[0]
        assert holding[3] == 4 and len(database.get_transactions(uid)) == 1
        for amount in (float("nan"), float("inf"), float("-inf")):
            operations: tuple[Callable[[], Any], ...] = (
                lambda: database.buy_stock(uid, "TCS", "TCS", amount, 100),
                lambda: database.buy_stock(uid, "TCS", "TCS", 1, amount),
                lambda: database.update_stock(holding[0], uid, amount, 100),
                lambda: database.update_stock(holding[0], uid, 4, amount),
                lambda: database.sell_stock(holding[0], uid, amount, 100),
                lambda: database.sell_stock(holding[0], uid, 1, amount),
            )
            for operation in operations:
                try:
                    operation()
                except ValueError:
                    pass
                else:
                    raise AssertionError("nonfinite financial value reached persistence")
        assert database.get_portfolio(uid)[0][3:5] == (4., 100.)
        assert len(database.get_transactions(uid)) == 1
        assert not database.update_stock(holding[0], uid + 10000, 5, 100)
        assert database.update_stock(holding[0], uid, 5, 100)
        assert database.sell_stock(holding[0], uid, 2, 101)
        assert database.get_portfolio(uid)[0][3] == 3
        assert len(database.get_transactions(uid)) == 2
        assert database.add_to_watchlist(uid, "tcs") and not database.add_to_watchlist(uid, "TCS")
        watch = database.get_watchlist(uid)[0]
        assert not database.remove_from_watchlist(watch[0], uid + 10000)
        assert database.remove_from_watchlist(watch[0], uid)
        assert database.delete_stock(holding[0], uid)
        prediction_count = sqlite.db.fetchone("SELECT count(*) AS n FROM prediction_history")["n"]
        database.save_prediction(uid, " tcs ", 100., None, None, payload={"source": "runtime-fixture"})
        assert database.get_prediction_history(uid)[0][1] == "TCS"
        assert database.get_prediction_details(uid, " tcs ")[0]["payload"] == {"source": "runtime-fixture"}
        payload = {"generated_at": "2026-10-08T10:00:00+00:00", "forecast": {"low": 90., "median": 100., "high": 110., "confidence_level": .8},
                   "training": {"timeframe": "1D"}, "context": {"provider": "upstox"}, "horizon": {"sessions": 1}}
        fid = database.save_range_forecast(uid, "TCS", payload)
        details = database.get_prediction_details(uid)
        assert any(row["id"] == fid and row["snapshot_hash"] for row in details)
        assert database.get_settled_range_forecasts(uid) == []
        event = database.record_audit_event(uid, "runtime", details={"at": datetime(2026, 10, 8, tzinfo=timezone.utc)})
        assert database.get_audit_events(uid)[0]["id"] == event
        assert sqlite.db.fetchone("SELECT count(*) AS n FROM prediction_history")["n"] == prediction_count
        assert sqlite.db.fetchone("SELECT count(*) AS n FROM portfolio")["n"] == original
        _probe_password_authentication(postgres)
    finally:
        close_dao_pools()
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _probe_password_authentication(postgres: Any) -> None:
    import authentication
    password = "StrongPass9!x"
    email = "password-parity@example.test"
    ok, _ = authentication.register_user("Password Parity", email, password, "1990-01-01")
    assert ok
    assert not authentication.register_user("Duplicate", email.upper(), password, "1990-01-01")[0]
    ok, user = authentication.login_user(email, password)
    assert ok and isinstance(user, dict)
    details = authentication.get_user_by_id(user["id"])
    assert details and details["email"] == email and "password" not in details
    for _ in range(authentication.MAX_FAILED_ATTEMPTS):
        assert not authentication.login_user(email, "WrongPass9!x")[0]
    assert not authentication.login_user(email, password)[0], "login lockout did not apply"
    reset = authentication.issue_password_reset_token(email)
    assert reset
    assert authentication.reset_password(reset, "ReplacementPass9!x")[0]
    assert not authentication.reset_password(reset, password)[0], "reset token replay accepted"
    assert authentication.login_user(email, "ReplacementPass9!x")[0]
    assert not authentication.login_or_register_oauth_user(provider="google", name="Unverified", email=email,
        provider_subject="rejected-subject", email_verified=False)[0]
    result = authentication.login_or_register_oauth_user(provider="google", name="Password Parity", email=email,
        provider_subject="verified-subject", email_verified=True)
    assert result[0] and result[2] == "linked"
    existing = authentication.login_or_register_oauth_user(provider="google", name="Changed", email="changed@example.test",
        provider_subject="verified-subject", email_verified=True)
    assert existing[0] and existing[1]["id"] == user["id"] and existing[2] == "existing"
    assert not authentication.login_or_register_oauth_user(provider="google", name="Different", email=email,
        provider_subject="different-subject", email_verified=True)[0]
    assert authentication.get_user_by_id(user["id"])["connected_providers"] == ["google"]
    assert authentication.login_user(email, "ReplacementPass9!x")[0], "OIDC link destroyed password auth"
    oauth = authentication.login_or_register_oauth_user(provider="apple", name="OAuth Only", email="oauth-only@example.test",
        provider_subject="apple-subject", email_verified=True)
    assert oauth[0] and oauth[2] == "created"
    assert not authentication.login_user("oauth-only@example.test", password)[0]
    concurrent_reset = authentication.issue_password_reset_token(email)
    assert concurrent_reset
    with ThreadPoolExecutor(max_workers=4) as executor:
        resets = list(executor.map(lambda _: authentication.reset_password(concurrent_reset, "ConcurrentPass9!x"), range(4)))
    assert sum(bool(result[0]) for result in resets) == 1, "concurrent reset replay accepted"
    with ThreadPoolExecutor(max_workers=4) as executor:
        registrations = list(executor.map(lambda _: authentication.register_user("Concurrent Registration", "registration-race@example.test", password, "1990-01-01"), range(4)))
    assert sum(bool(result[0]) for result in registrations) == 1, "concurrent registration created duplicate accounts"
    assert postgres.db.fetchone("SELECT count(*) AS n FROM users WHERE email='registration-race@example.test'")["n"] == 1
    # DB-API marker compilation keeps question marks in literals/comments and
    # percent literals intact, rather than translating arbitrary SQL dialects.
    statement = postgres.db.sql("SELECT '?' AS literal, ?::text AS bound, '100%' AS percent -- ? in comment\n")
    row = postgres.db.fetchone(statement, ("safe",))
    assert row == {"literal": "?", "bound": "safe", "percent": "100%"}
    _probe_session_and_mfa(postgres, user["id"])


def _probe_session_and_mfa(postgres: Any, uid: int) -> None:
    import secrets
    import pyotp
    import authentication
    from services import auth_api
    names = ("STOCKPILOT_JWT_SECRET", "STOCKPILOT_MFA_SECRET")
    saved = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            os.environ[name] = secrets.token_urlsafe(48)
        user = authentication.get_user_by_id(uid)
        try:
            auth_api.create_access_token({"id": uid + 10000, "email": "missing@example.test"})
        except RuntimeError:
            pass
        else:
            raise AssertionError("session minted without an authoritative account")
        token = auth_api.create_access_token(user)
        second = auth_api.create_access_token(user)
        authenticated = auth_api.user_from_token(token)
        assert authenticated is not None and authenticated["id"] == uid
        sessions = auth_api.list_sessions(uid, token)
        assert sum(row["current"] for row in sessions) == 1 and len(sessions) == 2
        assert not auth_api.revoke_session(uid + 10000, sessions[0]["id"])
        auth_api.revoke_other_sessions(uid, token)
        assert auth_api.user_from_token(second) is None and auth_api.user_from_token(token)
        auth_api.revoke_token(token)
        assert auth_api.user_from_token(token) is None
        token = auth_api.create_access_token(user)
        auth_api.revoke_all_sessions(uid)
        assert auth_api.user_from_token(token) is None
        assert authentication.get_user_by_id(uid)["token_version"] == 1
        enrollment = auth_api.begin_mfa_enrollment(user)
        stored = postgres.db.fetchone("SELECT encrypted_totp_secret FROM user_mfa WHERE user_id=%s", (uid,))["encrypted_totp_secret"]
        assert stored != enrollment["secret"] and auth_api._decrypt_totp_secret(stored) == enrollment["secret"]
        codes = auth_api.enable_mfa(uid, pyotp.TOTP(enrollment["secret"]).now())
        assert len(codes) == 10 and auth_api.mfa_status(uid)["recovery_codes_remaining"] == 10
        challenge = auth_api.issue_mfa_challenge(user, next_path="/portfolio")
        verified, target, method = auth_api.complete_mfa_challenge(challenge, codes[0])
        assert verified["id"] == uid and target == "/portfolio" and method == "recovery"
        try:
            auth_api.complete_mfa_challenge(challenge, codes[1])
        except ValueError:
            pass
        else:
            raise AssertionError("MFA challenge replay accepted")
        assert auth_api.verify_current_mfa(uid, codes[1]) == "recovery"

        def consume(_: int) -> bool:
            try:
                return auth_api.verify_current_mfa(uid, codes[2]) == "recovery"
            except ValueError:
                return False

        with ThreadPoolExecutor(max_workers=4) as executor:
            assert sum(executor.map(consume, range(4))) == 1
        capped = auth_api.issue_mfa_challenge(user)
        for _ in range(auth_api.MFA_MAX_ATTEMPTS):
            try:
                auth_api.complete_mfa_challenge(capped, "invalid")
            except ValueError:
                pass
            else:
                raise AssertionError("invalid factor accepted")
        try:
            auth_api.consume_mfa_challenge_for_passkey(capped)
        except ValueError:
            pass
        else:
            raise AssertionError("passkey path bypassed challenge cap")
        passkey = auth_api.issue_mfa_challenge(user, next_path="/account")
        assert auth_api.consume_mfa_challenge_for_passkey(passkey) == (uid, "/account")
        try:
            auth_api.consume_mfa_challenge_for_passkey(passkey)
        except ValueError:
            pass
        else:
            raise AssertionError("passkey challenge replay accepted")
        _probe_login_devices(postgres, user, codes[5])
        replacement = auth_api.regenerate_recovery_codes(uid, codes[3])
        assert len(replacement) == 10 and auth_api.mfa_status(uid)["recovery_codes_remaining"] == 10
        try:
            auth_api.verify_current_mfa(uid, codes[4])
        except ValueError:
            pass
        else:
            raise AssertionError("replaced recovery code remained active")
        auth_api.disable_mfa(uid, replacement[0])
        assert not auth_api.mfa_status(uid)["enabled"]
        _probe_passkey_crypto(postgres, uid)
        _probe_error_privacy(postgres)
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _probe_error_privacy(postgres: Any) -> None:
    import psycopg2
    import traceback
    from services.db.postgres_impl import PostgresDatabase
    marker = "disposable-private-marker-" + uuid.uuid4().hex
    try:
        postgres.db.fetchone("SELECT %s::integer AS value", (marker,))
    except psycopg2.Error as exc:
        rendered = "".join(traceback.format_exception(exc))
        assert marker not in rendered and marker not in str(exc), "vendor row detail leaked into a traceback"
    else:
        raise AssertionError("invalid typed value did not surface as a database failure")
    try:
        PostgresDatabase("postgresql://user:" + marker + "%ZZ@unused.invalid/database")
    except RuntimeError as exc:
        assert marker not in "".join(traceback.format_exception(exc)), "DSN parse failure leaked private URL material"
    else:
        raise AssertionError("malformed DSN was accepted")


def _probe_login_devices(postgres: Any, user: dict[str, Any], recovery: str) -> None:
    from services import login_anomaly, auth_api
    uid = user["id"]
    device = login_anomaly.token_device_hash("ab" * 32)
    first = login_anomaly.record_login(uid, user_agent="parity-browser", ip="203.0.113.10", device_hash=device)
    assert first["new_device"] and first["device_hash"] == device
    assert not login_anomaly.device_confirmed(uid, device)
    anomalies = login_anomaly.anomalies_for_user(uid)
    assert anomalies and not login_anomaly.acknowledge_anomaly(uid + 10000, anomalies[0]["id"])
    assert login_anomaly.acknowledge_anomaly(uid, anomalies[0]["id"])
    assert not login_anomaly.acknowledge_anomaly(uid, anomalies[0]["id"])
    assert not login_anomaly.device_confirmed(uid, device), "acknowledgment conferred trust"
    challenge = auth_api.issue_mfa_challenge(user, device_hash=device)
    try:
        auth_api.complete_mfa_challenge(challenge, recovery, device_hash="different-device")
    except ValueError:
        pass
    else:
        raise AssertionError("factor completed on a different device")
    verified, _, method = auth_api.complete_mfa_challenge(challenge, recovery, device_hash=device)
    assert verified["id"] == uid and method == "recovery" and login_anomaly.device_confirmed(uid, device)
    repeated = login_anomaly.record_login(uid, user_agent="parity-browser", ip="203.0.113.10", device_hash=device)
    assert not repeated["new_device"]
    row = postgres.db.fetchone("SELECT seen_count FROM login_devices WHERE user_id=%s AND device_hash=%s", (uid, device))
    assert row["seen_count"] == 2


def _probe_passkey_crypto(postgres: Any, uid: int) -> None:
    """Real P-256 signatures and SDK verification, not mocked WebAuthn results."""
    import cbor2
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import hashes
    import authentication
    from services import webauthn, auth_api
    names = ("STOCKPILOT_WEBAUTHN_RP_ID", "STOCKPILOT_WEBAUTHN_ORIGINS")
    saved = {name: os.environ.get(name) for name in names}
    os.environ[names[0]] = "localhost"
    os.environ[names[1]] = "http://localhost:3000"
    try:
        private = ec.generate_private_key(ec.SECP256R1())
        public = private.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: public.x.to_bytes(32, "big"), -3: public.y.to_bytes(32, "big")})
        credential_id = os.urandom(32)
        encode = webauthn._b64url_encode
        encoded_id = encode(credential_id)
        rp_hash = hashlib.sha256(b"localhost").digest()

        def registration(user: dict[str, Any]) -> dict[str, Any]:
            options = webauthn.begin_registration(user)
            client = json.dumps({"type": "webauthn.create", "challenge": options["challenge"], "origin": "http://localhost:3000", "crossOrigin": False}).encode()
            auth_data = rp_hash + b"\x45" + (0).to_bytes(4, "big") + bytes(16) + len(credential_id).to_bytes(2, "big") + credential_id + cose
            attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
            return {"challenge": options["challenge"], "response": {"id": encoded_id, "rawId": encoded_id, "type": "public-key",
                "response": {"clientDataJSON": encode(client), "attestationObject": encode(attestation), "transports": ["internal"]},
                "clientExtensionResults": {}}}

        user = authentication.get_user_by_id(uid)
        assert webauthn.complete_registration(user, registration(user))["registered"]
        assert webauthn.has_credentials(uid) and auth_api.mfa_status(uid)["enabled"]
        credential = webauthn.list_credentials(uid)[0]
        other = postgres.create_user_dao().create_user("Other Passkey", "other-passkey@example.test", "disabled")
        assert not webauthn.delete_credential(other, credential["id"])
        try:
            other_user = authentication.get_user_by_id(other)
            webauthn.complete_registration(other_user, registration(other_user))
        except ValueError:
            pass
        else:
            raise AssertionError("credential registration modified another user's credential")

        def assertion(challenge: str, count: int, *, corrupt: bool = False) -> dict[str, Any]:
            client = json.dumps({"type": "webauthn.get", "challenge": challenge, "origin": "http://localhost:3000", "crossOrigin": False}).encode()
            auth_data = rp_hash + b"\x05" + count.to_bytes(4, "big")
            signed = auth_data + hashlib.sha256(client).digest()
            signature = private.sign(signed, ec.ECDSA(hashes.SHA256()))
            if corrupt:
                signature = signature[:-1] + bytes([signature[-1] ^ 1])
            return {"challenge": challenge, "response": {"id": encoded_id, "rawId": encoded_id, "type": "public-key",
                "response": {"clientDataJSON": encode(client), "authenticatorData": encode(auth_data), "signature": encode(signature), "userHandle": encode(str(uid).encode())},
                "clientExtensionResults": {}}}

        options = webauthn.begin_authentication(uid)
        body = assertion(options["challenge"], 1)
        assert webauthn.complete_authentication(uid, body)["verified"]
        try:
            webauthn.complete_authentication(uid, body)
        except ValueError:
            pass
        else:
            raise AssertionError("WebAuthn assertion challenge replay accepted")
        options = webauthn.begin_authentication(uid)
        try:
            webauthn.complete_authentication(uid, assertion(options["challenge"], 2, corrupt=True))
        except ValueError:
            pass
        else:
            raise AssertionError("invalid WebAuthn signature accepted")
        count = postgres.db.fetchone("SELECT sign_count FROM webauthn_credentials WHERE id=%s", (credential["id"],))["sign_count"]
        assert count == 1, "failed assertion changed the counter"
        options = webauthn.begin_authentication(uid)
        try:
            webauthn.complete_authentication(uid, assertion(options["challenge"], 1))
        except ValueError:
            pass
        else:
            raise AssertionError("WebAuthn counter replay accepted")
        options = webauthn.begin_authentication(uid)
        assert webauthn.complete_authentication(uid, assertion(options["challenge"], 2))["verified"]
        assert webauthn.delete_credential(uid, credential["id"])
        assert not webauthn.has_credentials(uid)
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _probe_rls(admin: Any, schema: str) -> None:
    import psycopg2
    from psycopg2 import sql
    role = schema + "_client"
    with admin.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS NOINHERIT").format(sql.Identifier(role)))
        try:
            cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(role)))
            cursor.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            try:
                cursor.execute(sql.SQL("SELECT count(*) FROM {}.users").format(sql.Identifier(schema)))
            except psycopg2.errors.InsufficientPrivilege:
                pass
            else:
                raise AssertionError("unprivileged role bypassed revoked grants")
            finally:
                cursor.execute("RESET ROLE")
            cursor.execute(sql.SQL("GRANT SELECT ON {}.users TO {}").format(sql.Identifier(schema), sql.Identifier(role)))
            cursor.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            cursor.execute(sql.SQL("SELECT count(*) FROM {}.users").format(sql.Identifier(schema)))
            assert cursor.fetchone()[0] == 0, "policy-free RLS exposed users"
            cursor.execute("RESET ROLE")
            cursor.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user")
            assert any(cursor.fetchone()), "expected disposable bootstrap owner to bypass RLS"
        finally:
            cursor.execute("RESET ROLE")
            cursor.execute(sql.SQL("REVOKE SELECT ON {}.users FROM {}").format(sql.Identifier(schema), sql.Identifier(role)))
            cursor.execute(sql.SQL("REVOKE USAGE ON SCHEMA {} FROM {}").format(sql.Identifier(schema), sql.Identifier(role)))
            cursor.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def _probe_migration(admin: Any, url: str, cfg: Any, source: Path, temporary: Path, schema: str) -> None:
    from psycopg2 import sql
    from sqlalchemy.engine import make_url
    from alembic import command
    from scripts.migrate_sqlite_to_postgres import transfer
    import secrets
    import sqlite3
    from contextlib import closing
    secret = secrets.token_urlsafe(48)
    scoped_url = make_url(url).update_query_dict({"options": "-csearch_path=" + schema}).render_as_string(hide_password=False)
    with admin.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        cfg.set_main_option("sqlalchemy.url", scoped_url.replace("%", "%%"))
        command.upgrade(cfg, "head")
        first = transfer(source, scoped_url, approved=True, backup=temporary / "rehearsal.enc", backup_secret=secret)
        assert first["mode"] == "approved-transfer" and first["backup_restore_verified"]
        assert all(table["content_verified"] for table in first["tables"].values())
        second = transfer(source, scoped_url, approved=True, backup=temporary / "retry.enc", backup_secret=secret)
        assert second["mode"] == "already-matches" and second["tables"] == first["tables"]
        invalid = temporary / "invalid-source.db"
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original, closing(sqlite3.connect(invalid)) as malformed:
            original_created = original.execute("SELECT created_at FROM users WHERE id=1").fetchone()[0]
            original.backup(malformed)
            malformed.execute("UPDATE users SET created_at='not-a-timestamp' WHERE id=1")
            malformed.commit()
        try:
            transfer(invalid, scoped_url, approved=True, backup=temporary / "invalid-timestamp.enc", backup_secret=secret)
        except ValueError as exc:
            assert str(exc) == "Invalid timestamp value in migration source"
        else:
            raise AssertionError("malformed timestamp was imported")
        with closing(sqlite3.connect(invalid)) as malformed:
            malformed.execute("UPDATE users SET created_at=? WHERE id=1", (original_created,))
            malformed.execute("UPDATE audit_log SET details_json=?", ('{"invalid":NaN}',))
            malformed.commit()
        try:
            transfer(invalid, scoped_url, approved=True, backup=temporary / "invalid-json.enc", backup_secret=secret)
        except ValueError as exc:
            assert str(exc) == "Non-finite JSON constants are not accepted"
        else:
            raise AssertionError("non-standard JSON constant was imported")
        # Original matching snapshot still reconciles after both failed attempts:
        # no merge/overwrite/partial import occurred on the existing destination.
        third = transfer(source, scoped_url, approved=True, backup=temporary / "after-failures.enc", backup_secret=secret)
        assert third["mode"] == "already-matches" and third["tables"] == first["tables"]
    finally:
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _probe_sql(query: str, placeholder: str) -> str:
    """Only fixed probe statements use this; all row values remain bound."""
    return query.replace("?", placeholder)


def _probe_factory(factory: Any) -> dict[str, Any]:
    from services.db.postgres_impl import PostgresDatabase
    import sqlite3
    import psycopg2
    placeholder = "%s" if isinstance(factory.db, PostgresDatabase) else "?"
    user = factory.create_user_dao()
    uid = user.create_user("Parity", "parity@example.test", "disabled", date_of_birth="1990-01-01")
    other = user.create_user("Other", "other@example.test", "disabled")
    assert user.get_user_by_email("parity@example.test")["id"] == uid
    assert user.update_user(uid, name="Updated")
    assert user.get_user_by_id(uid)["name"] == "Updated"
    for key in ("name = NULL --", "unexpected"):
        try:
            user.update_user(uid, **{key: "bad"})
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe identifier accepted")
    user.set_mfa_secret(uid, "encrypted-fixture")
    factory.db.execute(_probe_sql("UPDATE user_mfa SET last_totp_counter=42 WHERE user_id=?", placeholder), (uid,))
    factory.db.commit()
    user.set_mfa_secret(uid, "replacement-fixture")
    assert factory.db.fetchone(_probe_sql("SELECT last_totp_counter FROM user_mfa WHERE user_id=?", placeholder), (uid,))["last_totp_counter"] is None
    assert user.get_mfa_secret(uid) == "replacement-fixture"
    user.disable_mfa(uid)
    assert user.get_mfa_secret(uid) is None
    user.enable_mfa(uid)
    assert user.get_mfa_secret(uid) == "replacement-fixture"
    user.add_recovery_code(uid, "fixture-hash")
    assert list(user.get_recovery_codes(uid)) == ["fixture-hash"]
    try:
        user.add_recovery_code(uid, "fixture-hash")
    except (sqlite3.IntegrityError, psycopg2.IntegrityError):
        factory.db.rollback()
    else:
        raise AssertionError("duplicate recovery code did not fail")
    assert not user.use_recovery_code(other, "fixture-hash")
    assert user.use_recovery_code(uid, "fixture-hash")
    assert not user.use_recovery_code(uid, "fixture-hash")
    holding = factory.create_portfolio_dao()
    hid = holding.add_holding(uid, "TCS", "TCS", 10, 100)
    assert not holding.update_holding(hid, other, 20, 100)
    assert not holding.delete_holding(hid, other)
    assert not holding.sell_holding(hid, other, 1, 101)
    assert holding.sell_holding(hid, uid, 3, 101)
    assert holding.get_holdings(uid)[0]["shares"] == 7
    try:
        holding.sell_holding(hid, uid, 8, 101)
    except ValueError:
        pass
    else:
        raise AssertionError("oversale accepted")
    assert holding.get_holdings(uid)[0]["shares"] == 7
    transactions = factory.create_transaction_dao()
    assert len(transactions.get_transactions(uid)) == 1
    transactions.add_transaction(uid, "TCS", "BUY", 1, 99)
    assert len(transactions.get_transactions(uid)) == 2
    # The ledger fails after the holding insert, proving the purchase is atomic
    # rather than two separately committed DAO calls.
    if isinstance(factory.db, PostgresDatabase):
        factory.db.execute("""CREATE FUNCTION parity_reject_buy() RETURNS trigger AS $$
            BEGIN IF NEW.symbol='FAIL' THEN RAISE EXCEPTION 'disposable ledger failure'; END IF;
            RETURN NEW; END; $$ LANGUAGE plpgsql""")
        factory.db.execute("CREATE TRIGGER parity_reject_buy BEFORE INSERT ON transactions FOR EACH ROW EXECUTE FUNCTION parity_reject_buy()")
    else:
        factory.db.execute("""CREATE TRIGGER parity_reject_buy BEFORE INSERT ON transactions WHEN NEW.symbol='FAIL'
            BEGIN SELECT RAISE(ABORT,'disposable ledger failure'); END""")
    factory.db.commit()
    try:
        holding.buy_holding(uid, "FAIL", "FAIL", 1, 100)
    except (sqlite3.IntegrityError, psycopg2.Error):
        factory.db.rollback()
    else:
        raise AssertionError("ledger failure was swallowed")
    assert not factory.db.fetchone("SELECT id FROM portfolio WHERE symbol='FAIL'")
    if isinstance(factory.db, PostgresDatabase):
        factory.db.execute("DROP TRIGGER parity_reject_buy ON transactions")
        factory.db.execute("DROP FUNCTION parity_reject_buy()")
    else:
        factory.db.execute("DROP TRIGGER parity_reject_buy")
    factory.db.commit()
    watch = factory.create_watchlist_dao()
    assert watch.add_symbol(uid, "tcs") and not watch.add_symbol(uid, "TCS")
    wid = watch.get_watchlist(uid)[0]["id"]
    assert not watch.remove_symbol(wid, other)
    assert watch.remove_symbol(wid, uid)
    paper = factory.create_paper_trading_dao()
    paper.create_account(uid, 1000)
    try:
        paper.create_account(uid, 9000)
    except (sqlite3.IntegrityError, psycopg2.IntegrityError):
        factory.db.rollback()
    else:
        raise AssertionError("duplicate paper account did not fail")
    assert paper.get_account(uid)["cash_balance"] == 1000
    assert list(paper.get_positions(uid)) == list(paper.get_orders(uid)) == []
    settings = factory.create_settings_dao()
    factory.db.execute(_probe_sql("INSERT INTO settings(user_id) VALUES(?)", placeholder), (uid,))
    factory.db.commit()
    assert settings.update_settings(uid, theme="Light")
    assert settings.get_settings(uid)["theme"] == "Light"
    audit = factory.create_audit_dao()
    audit.record_event(uid, "parity", details={"nested": [1, True, None]})
    assert audit.get_events(uid)[0]["details"] == {"nested": [1, True, None]}
    assert not audit.get_events(other)
    predictions = factory.create_prediction_dao()
    pid = predictions.save_prediction(uid, " tcs ", linear_prediction=100, payload={"nested": [1, True]})
    history = predictions.get_prediction_history(uid, 0)
    assert history[0]["id"] == pid and history[0]["symbol"] == "TCS"
    assert set(history[0]) == {"id", "symbol", "linear", "dt", "rf", "date"}
    assert predictions.get_prediction_details(uid, " tcs ", 0)[0]["payload"] == {"nested": [1, True]}
    assert not predictions.get_prediction_details(other)
    payload = {"generated_at": "2026-10-08T10:00:00+00:00", "forecast": {"low": 90., "median": 100., "high": 110., "confidence_level": .8},
               "training": {"timeframe": "1D"}, "context": {"provider": "upstox"}, "horizon": {"sessions": 1}}
    fid = predictions.save_range_forecast(uid, "TCS", payload)
    snapshot = factory.db.fetchone(_probe_sql("SELECT * FROM prediction_history WHERE id=?", placeholder), (fid,))
    assert snapshot["snapshot_hash"] and snapshot["forecast_low"] == 90
    # Manual, stale, demo, incomplete and abstained outcomes must all be excluded.
    for index, (source, stale, demo, official, status, score) in enumerate([
        ("automatic", 0, 0, 1, "available", 1.), ("manual", 0, 0, 1, "available", 1.),
        ("automatic", 1, 0, 1, "available", 1.), ("automatic", 0, 1, 1, "available", 1.),
        ("automatic", 0, 0, 0, "available", 1.), ("automatic", 0, 0, 1, "abstained", 1.),
        ("automatic", 0, 0, 1, "available", None),
    ]):
        values = (uid, "TCS", 90., 100., 110., .8, 100., 1, score, source, stale, demo, official, status)
        factory.db.execute(_probe_sql("""INSERT INTO prediction_history(user_id,symbol,forecast_low,forecast_median,forecast_high,confidence_level,
            actual_price,coverage_hit,winkler_score,settlement_source,settlement_is_stale,settlement_is_demo,official_outcome,forecast_status,outcome_status)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'settled')""", placeholder), values)
    factory.db.commit()
    settled = predictions.get_settled_forecasts(uid)
    assert len(settled) == 1 and not predictions.get_settled_forecasts(other)
    # Forecast snapshot immutability must actually be enforced by the DB.
    try:
        factory.db.execute(_probe_sql("UPDATE prediction_history SET forecast_low=1 WHERE id=?", placeholder), (fid,))
        factory.db.commit()
    except (sqlite3.IntegrityError, psycopg2.Error):
        factory.db.rollback()
    else:
        raise AssertionError("immutable forecast was modified")
    snapshot.pop("id")
    snapshot.pop("prediction_date")  # PG's existing TIMESTAMP is driver-native.
    return {"snapshot": snapshot, "settled_keys": sorted(settled[0]), "history_keys": sorted(history[0])}


def _probe_concurrency(factory: Any) -> None:
    uid = factory.create_user_dao().create_user("Concurrent", "concurrent@example.test", "disabled")
    hid = factory.create_portfolio_dao().add_holding(uid, "INFY", "INFY", 5, 100)
    factory.create_user_dao().add_recovery_code(uid, "single-use")
    factory.db.close()

    def worker(_: int) -> tuple[bool, bool, bool]:
        try:
            watch = factory.create_watchlist_dao().add_symbol(uid, "INFY")
            recovery = factory.create_user_dao().use_recovery_code(uid, "single-use")
            sale = factory.create_portfolio_dao().sell_holding(hid, uid, 1, 101)
            return watch, recovery, sale
        finally:
            factory.db.close()

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(worker, range(8)))
    assert tuple(sum(result[i] for result in results) for i in range(3)) == (1, 1, 5)
    assert not factory.create_portfolio_dao().get_holdings(uid)
    assert len(factory.create_transaction_dao().get_transactions(uid)) == 5


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Run real parity using DATABASE_MIGRATION_URL on a loopback stockpilot_test* database")
    args = parser.parse_args()
    issues = check_parity()
    if issues:
        print("Backend signature parity FAILED (non-connecting):")
        for issue in issues:
            print(f"  {issue}")
        return 1
    print("Backend signature parity passed (non-connecting).")
    print("Constructors/private helpers and contract-specific driver return types differ by design.")
    if args.live:
        url = os.getenv("DATABASE_MIGRATION_URL")
        if not url:
            print("Live parity requires DATABASE_MIGRATION_URL.", file=sys.stderr)
            return 1
        try:
            completed = check_live_parity(url)
        except Exception as exc:
            print(f"Live backend parity FAILED ({type(exc).__name__}); no connectivity or correctness pass claimed.", file=sys.stderr)
            return 1
        for item in completed:
            print("PASS: " + item)
    else:
        print("Use --live for disposable real-PostgreSQL evidence. Signature parity is not switch approval.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
