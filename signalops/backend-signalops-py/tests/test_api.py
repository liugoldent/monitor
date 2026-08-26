from signalops.main import app, healthcheck


def test_liveness_does_not_require_database_connection() -> None:
    assert healthcheck() == {"status": "ok"}


def test_openapi_exposes_read_only_signal_timeline() -> None:
    paths = app.openapi()["paths"]
    signals_path = paths["/api/v1/signals"]
    assert set(signals_path) == {"get"}
    assert set(paths["/api/v1/overview"]) == {"get"}
    assert set(paths["/api/v1/positions"]) == {"get"}
    assert set(paths["/api/v1/analytics"]) == {"get"}
    assert set(paths["/api/v1/assistant/query"]) == {"post"}
