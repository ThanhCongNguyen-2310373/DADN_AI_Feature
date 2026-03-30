from pathlib import Path
import py_compile


ROOT_DIR = Path(__file__).resolve().parents[1]


# Keep smoke tests dependency-light so they can run in CI quickly.
SMOKE_FILES = [
    "main.py",
    "config.py",
    "core/mqtt_client.py",
    "core/serial_client.py",
    "sensors/sensor_reader.py",
    "ai/voice_control/voice_assistant.py",
    "web_app/app.py",
]


def test_smoke_files_exist() -> None:
    missing = [rel for rel in SMOKE_FILES if not (ROOT_DIR / rel).exists()]
    assert not missing, f"Missing expected files: {missing}"


def test_smoke_files_compile() -> None:
    for rel in SMOKE_FILES:
        py_compile.compile(str(ROOT_DIR / rel), doraise=True)
