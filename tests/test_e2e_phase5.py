import importlib
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient


TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _bootstrap_app(tmp_path: Path):
    import config
    import core.database as db_module
    import core.auth_service as auth_module
    import core.rate_limiter as rl_module

    config.DATABASE_BACKEND = "sqlite"
    config.DATABASE_PATH = str(tmp_path / "e2e.db")
    config.RATE_LIMIT_BACKEND = "memory"
    config.METRICS_ENABLED = True

    db_module.DatabaseSingleton._instance = None
    db_module.DatabaseSingleton._backend_instance = None
    auth_module.AuthService._instance = None
    rl_module._rate_limiter_instance = None

    import web_app.app as app_module
    app_module = importlib.reload(app_module)

    class FakeSensorReader:
        def get_latest_data(self):
            return {
                "temperature": 28.5,
                "humidity": 65.0,
                "gas": 120.0,
                "led": 0,
                "fan": 0,
                "door": 0,
                "pump": 0,
                "timestamp": "10:00:00",
            }

    class FakeVoiceAssistant:
        chat_history = [{"role": "assistant", "text": "ok", "time": "10:00:00"}]

        def _ask_rag(self, q: str):
            return f"RAG:{q}"

        def _answer_weather(self, q: str):
            return f"WEATHER:{q}"

    app_module.inject_modules(
        sensor_reader=FakeSensorReader(),
        voice_assistant=FakeVoiceAssistant(),
        face_recognizer=None,
    )

    # Seed sensor/event data for ML endpoints
    from core.database import DatabaseSingleton

    db = DatabaseSingleton.get_instance()
    for i in range(12):
        db.insert_sensor(25 + i * 0.2, 60 + i * 0.3, 100 + i * 2)
    db.insert_device_event("fan", 1, source="test")
    db.insert_device_event("fan", 0, source="test")

    return app_module.app


def _login(client: TestClient, username: str, password: str):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


def test_e2e_rbac_and_iot_ai_flow(tmp_path):
    app = _bootstrap_app(tmp_path)
    with TestClient(app) as client:
        viewer_username = f"viewer_{uuid.uuid4().hex[:8]}"

        # Default admin bootstrapped from config WEB_USERNAME/WEB_PASSWORD
        r = _login(client, "admin", "yolohome2025")
        assert r.status_code == 303
        assert "session_token" in r.cookies

        # Admin APIs
        r = client.get("/api/users")
        assert r.status_code == 200
        assert "users" in r.json()

        # Create viewer user
        r = client.post(
            "/api/users",
            json={
                "username": viewer_username,
                "password": "viewerpass",
                "role": "viewer",
                "full_name": "Viewer User",
            },
        )
        assert r.status_code == 200

        # AI endpoint + ML + metrics
        r = client.post("/api/voice/ask", json={"question": "nha co an toan khong"})
        assert r.status_code == 200
        assert "answer" in r.json()

        r = client.get("/api/ml/forecast")
        assert r.status_code == 200
        assert "success" in r.json()

        r = client.get("/api/ml/anomalies")
        assert r.status_code == 200
        assert "success" in r.json()

        r = client.get("/metrics")
        assert r.status_code in (200, 503)

        # Rules CRUD (operator/admin)
        r = client.post(
            "/api/rules",
            json={
                "name": "E2E Rule",
                "condition_field": "temp",
                "condition_op": ">",
                "condition_value": 30,
                "action_device": "fan",
                "action_state": 1,
                "notify_telegram": False,
                "enabled": True,
            },
        )
        assert r.status_code == 200

        r = client.get("/api/rules")
        assert r.status_code == 200
        assert "rules" in r.json()

        # Logout admin
        client.get("/logout")

        # Login viewer and verify RBAC
        r = _login(client, viewer_username, "viewerpass")
        assert r.status_code == 303

        r = client.get("/api/sensors")
        assert r.status_code == 200

        r = client.post("/api/control", json={"device": "led", "value": 1})
        assert r.status_code == 403


def test_rate_limit_bruteforce(tmp_path):
    app = _bootstrap_app(tmp_path)
    with TestClient(app) as client:
        # 5 lần sai -> lần 6 bị khóa
        for i in range(5):
            r = _login(client, "admin", f"wrong{i}")
            assert r.status_code in (401, 429)

        r = _login(client, "admin", "wrong-final")
        assert r.status_code == 429
