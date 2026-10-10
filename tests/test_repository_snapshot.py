import database
from services.db.sqlite_impl import SQLiteDatabase, SQLitePredictionDAO


def test_repository_writes_use_same_connection_and_canonical_snapshot(temp_db):
    db = SQLiteDatabase()
    try:
        db.execute("INSERT INTO users(id,name,email,password) VALUES(1,'Test','test@example.invalid','disabled')")
        db.commit()
        dao = SQLitePredictionDAO(db)
        prediction_id = dao.save_prediction(1, "RELIANCE", payload={"source": "test"})
        assert db.fetchone("SELECT symbol FROM prediction_history WHERE id=?", (prediction_id,))["symbol"] == "RELIANCE"
        payload = {"generated_at": "2026-10-08T10:00:00+00:00", "forecast": {"low": 90., "median": 100., "high": 110., "confidence_level": .8},
                   "training": {"timeframe": "1D"}, "context": {"provider": "upstox"}, "horizon": {"sessions": 1}}
        legacy_id = database.save_range_forecast(1, "RELIANCE", payload)
        repository_id = dao.save_range_forecast(1, "RELIANCE", payload)
        left = db.fetchone("SELECT * FROM prediction_history WHERE id=?", (legacy_id,))
        right = db.fetchone("SELECT * FROM prediction_history WHERE id=?", (repository_id,))
        assert left.pop("id") != right.pop("id")
        assert left == right
    finally:
        db.close()
