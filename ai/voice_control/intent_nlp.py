"""
Phân loại ý định: từ điển (intent_lexicon.json) + mô hình nhỏ.
Ưu tiên fastText supervised nếu cài được `fasttext`; nếu không, dùng BoW + softmax (NumPy)
tương đương vai trò phân lớp (Python 3.13 thường thiếu wheel fastText trên Windows).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_DIR = os.path.dirname(os.path.abspath(__file__))
_LEXICON_PATH = os.path.join(_DIR, "intent_lexicon.json")
_CORPUS_PATH = os.path.join(_DIR, "intent_corpus.txt")
_DATA_DIR = os.path.join(_DIR, "data")
_FASTTEXT_BIN = os.path.join(_DATA_DIR, "intent_fasttext.bin")
_BOW_NPZ = os.path.join(_DATA_DIR, "intent_bow.npz")

_LABEL_ORDER = ["control_device", "question", "weather", "other"]


def _tokenize(text: str) -> List[str]:
    text = text.lower().strip()
    parts = re.split(r"[^\wàáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổộổơờớởỡợùúủũụưừứửữựỳýỷỹỵđa-z0-9]+", text, flags=re.IGNORECASE)
    return [p for p in parts if len(p) >= 1]


def _synthetic_lines() -> List[str]:
    """Mở rộng corpus trong bộ nhớ để train ổn định hơn."""
    lines = []
    acts = [
        ("bat", "bật", "mở", "bật giúp", "kick hoạt"),
        ("tat", "tắt", "đóng", "ngắt", "tắt giúp"),
    ]
    devs = [
        ("đèn", "led", "bóng đèn"),
        ("quạt", "fan", "máy quạt"),
        ("bơm", "máy bơm", "tưới cây"),
        ("cửa", "khóa cửa"),
    ]
    for ak, *verbs in acts:
        for dv in devs:
            for v in verbs:
                for w in ("yolo", "yolo ơi", ""):
                    frag = f"{w} {v} {dv[0]}".strip()
                    lines.append(f"__label__control_device {frag}")
    qs = [
        "nhiệt độ an toàn là bao nhiêu",
        "độ ẩm bao nhiêu là tốt",
        "gas bao nhiêu ppm nguy hiểm",
        "hướng dẫn tiết kiệm điện",
        "tại sao cần cảnh báo",
        "là gì yolohome",
    ]
    for q in qs:
        lines.append(f"__label__question {q}")
    wx = [
        "thời tiết hôm nay",
        "trời có mưa không",
        "nhiệt độ ngoài trời",
        "dự báo gió bão",
    ]
    for w in wx:
        lines.append(f"__label__weather {w}")
    for o in ("hello", "thanks", "random abc"):
        lines.append(f"__label__other {o}")
    return lines


def _read_corpus_lines() -> List[str]:
    lines: List[str] = []
    if os.path.isfile(_CORPUS_PATH):
        with open(_CORPUS_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(line)
    lines.extend(_synthetic_lines())
    return lines


class _BowSoftmaxModel:
    def __init__(self, vocab: List[str], W: np.ndarray, b: np.ndarray, labels: List[str]):
        self.vocab = vocab
        self.word_to_i = {w: i for i, w in enumerate(vocab)}
        self.W = W
        self.b = b
        self.labels = labels

    @staticmethod
    def _softmax(logits: np.ndarray) -> np.ndarray:
        z = logits - np.max(logits)
        e = np.exp(z)
        return e / (np.sum(e) + 1e-12)

    def predict_proba(self, text: str) -> Tuple[str, float]:
        toks = _tokenize(text)
        x = np.zeros(len(self.vocab), dtype=np.float64)
        for t in toks:
            j = self.word_to_i.get(t)
            if j is not None:
                x[j] += 1.0
        if x.sum() < 1e-9:
            x[0] = 1.0
        x = x / (x.sum() + 1e-12)
        logits = x @ self.W + self.b
        p = self._softmax(logits)
        k = int(np.argmax(p))
        return self.labels[k], float(p[k])

    @classmethod
    def train_from_corpus(cls, lines: List[str], vocab_size: int = 512, iters: int = 250, lr: float = 0.4):
        label_to_c = {lb: i for i, lb in enumerate(_LABEL_ORDER)}
        texts: List[str] = []
        y_idx: List[int] = []
        for line in lines:
            if not line.startswith("__label__"):
                continue
            sp = line.split(None, 1)
            if len(sp) < 2:
                continue
            lab = sp[0].replace("__label__", "")
            if lab not in label_to_c:
                continue
            texts.append(sp[1])
            y_idx.append(label_to_c[lab])

        freq: Dict[str, int] = {}
        for t in texts:
            for w in _tokenize(t):
                freq[w] = freq.get(w, 0) + 1
        vocab = [w for w, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:vocab_size]]
        word_to_i = {w: i for i, w in enumerate(vocab)}
        n = len(texts)
        V, C = len(vocab), len(_LABEL_ORDER)
        X = np.zeros((n, V), dtype=np.float64)
        for i, t in enumerate(texts):
            for w in _tokenize(t):
                j = word_to_i.get(w)
                if j is not None:
                    X[i, j] += 1.0
            s = X[i].sum()
            if s > 0:
                X[i] /= s
        Y = np.zeros((n, C), dtype=np.float64)
        for i, c in enumerate(y_idx):
            Y[i, c] = 1.0

        rng = np.random.default_rng(42)
        W = 0.01 * rng.standard_normal((V, C))
        b = np.zeros(C, dtype=np.float64)

        for _ in range(iters):
            logits = X @ W + b
            probs = _BowSoftmaxModel._softmax_row(logits)
            diff = (probs - Y) / max(n, 1)
            W -= lr * (X.T @ diff)
            b -= lr * np.sum(diff, axis=0)
            lr *= 0.995

        return cls(vocab, W, b, list(_LABEL_ORDER))

    @staticmethod
    def _softmax_row(logits: np.ndarray) -> np.ndarray:
        z = logits - np.max(logits, axis=1, keepdims=True)
        e = np.exp(z)
        return e / (np.sum(e, axis=1, keepdims=True) + 1e-12)

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        np.savez(path, vocab=np.array(self.vocab, dtype=object), W=self.W, b=self.b, labels=np.array(self.labels, dtype=object))

    @classmethod
    def load(cls, path: str) -> Optional["_BowSoftmaxModel"]:
        if not os.path.isfile(path):
            return None
        z = np.load(path, allow_pickle=True)
        vocab = [str(x) for x in z["vocab"].tolist()]
        labels = [str(x) for x in z["labels"].tolist()]
        return cls(vocab, z["W"], z["b"], labels)


@dataclass
class VoiceContext:
    last_target_feed: Optional[str] = None
    last_action_key: Optional[str] = None
    last_device_name_vi: Optional[str] = None
    last_intent: Optional[str] = None
    last_ts: float = 0.0

    def touch(self, intent: str, feed: Optional[str], action_key: Optional[str], name_vi: Optional[str]) -> None:
        self.last_intent = intent
        self.last_target_feed = feed
        self.last_action_key = action_key
        self.last_device_name_vi = name_vi
        self.last_ts = time.monotonic()

    def clear_if_expired(self, window_sec: float) -> None:
        if self.last_ts and (time.monotonic() - self.last_ts) > window_sec:
            self.last_target_feed = None
            self.last_action_key = None
            self.last_device_name_vi = None
            self.last_intent = None
            self.last_ts = 0.0


class IntentNLPEngine:
    """
    Từ điển + fastText (nếu có) / BoW-softmax.
    """

    def __init__(self, context_window_sec: float = 12.0, ml_confidence: float = 0.42):
        self.context_window_sec = context_window_sec
        self.ml_confidence = ml_confidence
        self._lex: Dict[str, Any] = {}
        self._load_lexicon()
        self._ft_model = None
        self._bow: Optional[_BowSoftmaxModel] = None
        self._ensure_ml_model()

    def _load_lexicon(self) -> None:
        if os.path.isfile(_LEXICON_PATH):
            with open(_LEXICON_PATH, encoding="utf-8") as f:
                self._lex = json.load(f)
        else:
            self._lex = {}

    @staticmethod
    def _patch_fasttext_model(model) -> None:
        """Wrap fasttext model's predict method for NumPy 2.0+ compatibility."""
        try:
            # fasttext>=0.9.14 exposes .predict via model.predict (top-level callable)
            # but the underlying C++ layer uses np.array(..., copy=False) which breaks NumPy 2.
            # We wrap model.predict so that np.asarray() is used instead.
            original_predict = model.predict

            def patched_predict(text, k=1, threshold=0.0, on_unicode_error='strict'):
                """Patched predict that uses np.asarray instead of np.array(..., copy=False)."""
                def check(entry):
                    if entry.find('\n') != -1:
                        raise ValueError("predict processes one line at a time (remove '\\n')")
                    entry += "\n"
                    return entry

                if isinstance(text, list):
                    text = [check(entry) for entry in text]
                    result = original_predict(text, k=k, threshold=threshold, on_unicode_error=on_unicode_error)
                    return result
                else:
                    text = check(text)
                    result = original_predict(text, k=k, threshold=threshold, on_unicode_error=on_unicode_error)
                    if result:
                        probs, labels = zip(*result)
                    else:
                        probs, labels = ([], ())
                    return labels, np.asarray(probs)

            model.predict = patched_predict
            logger.debug("[Intent] Patched fasttext model for NumPy 2.0+ compatibility")
        except AttributeError as e:
            logger.warning("[Intent] Fasttext model API does not support patching (%s); using BoW fallback.", e)

    def _ensure_ml_model(self) -> None:
        os.makedirs(_DATA_DIR, exist_ok=True)
        lines = _read_corpus_lines()

        # --- fastText nếu khả dụng ---
        try:
            import fasttext  # type: ignore

            if not os.path.isfile(_FASTTEXT_BIN):
                train_path = os.path.join(_DATA_DIR, "intent_train.txt")
                with open(train_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines))
                model = fasttext.train_supervised(
                    train_path,
                    dim=16,
                    lr=0.5,
                    epoch=40,
                    wordNgrams=2,
                    minCount=1,
                    loss="softmax",
                    verbose=0,
                )
                model.save_model(_FASTTEXT_BIN)
                logger.info("[Intent] Đã train fastText intent model.")
            self._ft_model = fasttext.load_model(_FASTTEXT_BIN)
            # Patch model for NumPy 2.0+ compatibility (np.array(..., copy=False) deprecated)
            self._patch_fasttext_model(self._ft_model)
            logger.info("[Intent] Dùng fastText: %s", _FASTTEXT_BIN)
            return
        except Exception as e:
            logger.info("[Intent] fastText không dùng được (%s), chuyển BoW-softmax.", e)

        self._bow = _BowSoftmaxModel.load(_BOW_NPZ)
        if self._bow is None:
            self._bow = _BowSoftmaxModel.train_from_corpus(lines)
            self._bow.save(_BOW_NPZ)
            logger.info("[Intent] Đã train và lưu BoW-softmax intent model.")

    def ml_predict(self, text: str) -> Tuple[str, float]:
        t = text.lower().strip()
        if self._ft_model is not None:
            lab, prob = self._ft_model.predict(t.replace("\n", " "), k=1)
            raw = lab[0]
            name = raw.decode("utf-8", errors="ignore") if isinstance(raw, (bytes, bytearray)) else str(raw)
            name = name.replace("__label__", "")
            return name, float(prob[0])
        if self._bow is not None:
            return self._bow.predict_proba(t)
        return "other", 0.0

    def is_question(self, text_lower: str) -> bool:
        qkw = self._lex.get("question_keywords", [])
        has_q = any(kw in text_lower for kw in qkw)
        has_action = self._find_action(text_lower) is not None
        has_dev = self._find_device_feed(text_lower) is not None
        return has_q and not (has_action and has_dev)

    def is_weather(self, text_lower: str) -> bool:
        return any(kw in text_lower for kw in self._lex.get("weather_keywords", []))

    def _find_action(self, text_lower: str) -> Optional[str]:
        best = None
        best_len = 0
        for key, kws in self._lex.get("action_keywords", {}).items():
            for kw in kws:
                if kw in text_lower and len(kw) >= best_len:
                    best = key
                    best_len = len(kw)
        return best

    def _find_device_feed(self, text_lower: str) -> Tuple[Optional[str], Optional[str]]:
        best_feed = None
        best_name = None
        best_kw_len = 0
        for feed, info in self._lex.get("device_keywords", {}).items():
            for kw in info.get("keywords", []):
                if kw in text_lower and len(kw) >= best_kw_len:
                    best_feed = feed
                    best_name = info.get("name_vi")
                    best_kw_len = len(kw)
        return best_feed, best_name

    def _has_pronoun_device(self, text_lower: str) -> bool:
        return any(p in text_lower for p in self._lex.get("pronoun_device", []))

    def resolve_action_device(
        self, text_lower: str, ctx: VoiceContext
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Trả về (action_key 'bat'|'tat', feed, name_vi).
        """
        ctx.clear_if_expired(self.context_window_sec)
        action = self._find_action(text_lower)
        feed, name = self._find_device_feed(text_lower)

        if action and not feed and self._has_pronoun_device(text_lower):
            if (
                ctx.last_target_feed
                and ctx.last_ts > 0
                and (time.monotonic() - ctx.last_ts) <= self.context_window_sec
            ):
                feed = ctx.last_target_feed
                name = ctx.last_device_name_vi

        return action, feed, name


# Export cho backward compatibility / test
def load_lexicon_for_tests() -> Dict[str, Any]:
    if os.path.isfile(_LEXICON_PATH):
        with open(_LEXICON_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}
