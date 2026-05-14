import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai.voice_control.intent_nlp import IntentNLPEngine, VoiceContext, load_lexicon_for_tests


@pytest.fixture
def engine(tmp_path, monkeypatch):
    import ai.voice_control.intent_nlp as inn

    monkeypatch.setattr(inn, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(inn, "_FASTTEXT_BIN", str(tmp_path / "intent_fasttext.bin"))
    monkeypatch.setattr(inn, "_BOW_NPZ", str(tmp_path / "intent_bow.npz"))
    return IntentNLPEngine(context_window_sec=12.0, ml_confidence=0.25)


def test_lexicon_load():
    lex = load_lexicon_for_tests()
    assert "question_keywords" in lex
    assert "yolohome-led" in lex.get("device_keywords", {})


def test_pronoun_context(engine):
    ctx = VoiceContext()
    ctx.touch("control", "yolohome-led", "tat", "đèn")
    action, feed, name = engine.resolve_action_device("tắt nó đi", ctx)
    assert action == "tat"
    assert feed == "yolohome-led"
    assert name == "đèn"


def test_control_explicit(engine):
    ctx = VoiceContext()
    a, f, n = engine.resolve_action_device("yolo bật quạt", ctx)
    assert a == "bat" and f == "yolohome-fan"


def test_ml_predict_returns_label(engine):
    lab, p = engine.ml_predict("xin chào bạn")
    assert lab in ("other", "question", "weather", "control_device")
    assert 0.0 <= p <= 1.0
