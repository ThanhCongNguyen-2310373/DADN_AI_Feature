"""
Tiền xử lý audio trước STT: RNNoise (tuỳ chọn), webrtcvad hoặc RMS cắt im lặng.
PCM mono 16-bit; webrtcvad yêu cầu sample rate 8000/16000/32000/48000.
"""

from __future__ import annotations

import logging
import struct
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_RNNOISE_DENOSER = None


def _get_rnnoise_denoiser():
    """Lazy RNNoise (pyrnnoise); trả None nếu không cài được."""
    global _RNNOISE_DENOSER
    if _RNNOISE_DENOSER is False:
        return None
    if _RNNOISE_DENOSER is not None:
        return _RNNOISE_DENOSER
    try:
        from pyrnnoise import RNNoise

        _RNNOISE_DENOSER = RNNoise()
        logger.info("[Voice/STT] RNNoise (pyrnnoise) đã bật.")
    except Exception as e:
        _RNNOISE_DENOSER = False
        logger.info("[Voice/STT] RNNoise không khả dụng (%s), bỏ qua bước denoise.", e)
    return _RNNOISE_DENOSER if _RNNOISE_DENOSER is not False else None


def _pcm16_bytes_to_numpy(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype=np.int16).copy()


def _numpy_to_pcm16_bytes(arr: np.ndarray) -> bytes:
    return arr.astype(np.int16, copy=False).tobytes()


def _denoise_rnnoise(pcm_i16: np.ndarray, sample_rate: int) -> np.ndarray:
    den = _get_rnnoise_denoiser()
    if den is None or pcm_i16.size == 0:
        return pcm_i16
    try:
        if hasattr(den, "denoise_chunk"):
            chunk = pcm_i16.astype(np.float32) / 32768.0
            out, _ = den.denoise_chunk(chunk, sample_rate)
            if out is not None and len(out) == len(pcm_i16):
                return (np.clip(out, -1.0, 1.0) * 32767.0).astype(np.int16)
        if hasattr(den, "filter"):
            flt = den.filter(pcm_i16.tobytes(), sample_rate)
            if isinstance(flt, (bytes, bytearray)) and len(flt) >= len(pcm_i16) * 2 - 4:
                return _pcm16_bytes_to_numpy(bytes(flt)[: len(pcm_i16) * 2])
    except Exception as e:
        logger.debug("[Voice/STT] RNNoise denoise lỗi: %s", e)
    return pcm_i16


def _vad_trim_webrtc(pcm_i16: np.ndarray, sample_rate: int, aggressiveness: int) -> np.ndarray:
    try:
        import webrtcvad
    except ImportError:
        return pcm_i16

    if sample_rate not in (8000, 16000, 32000, 48000):
        return pcm_i16

    vad = webrtcvad.Vad(min(3, max(0, aggressiveness)))
    frame_ms = 30
    samples_per_frame = int(sample_rate * frame_ms / 1000)
    nbytes = samples_per_frame * 2
    raw = pcm_i16.tobytes()
    flags = []
    for i in range(0, len(raw) - nbytes + 1, nbytes):
        chunk = raw[i : i + nbytes]
        if len(chunk) < nbytes:
            break
        try:
            flags.append(1 if vad.is_speech(chunk, sample_rate) else 0)
        except Exception:
            flags.append(0)

    if not flags:
        return pcm_i16

    try:
        first = next(i for i, f in enumerate(flags) if f == 1)
        last = len(flags) - 1 - next(i for i, f in enumerate(reversed(flags)) if f == 1)
    except StopIteration:
        return pcm_i16

    pad_frames = 3
    first = max(0, first - pad_frames)
    last = min(len(flags) - 1, last + pad_frames)
    start_byte = first * nbytes
    end_byte = (last + 1) * nbytes
    trimmed = raw[start_byte:end_byte]
    if len(trimmed) < nbytes:
        return pcm_i16
    if len(trimmed) % 2:
        trimmed += b"\x00"
    return _pcm16_bytes_to_numpy(trimmed)


def _rms_edge_trim(pcm_i16: np.ndarray, sample_rate: int, frame_ms: int = 30) -> np.ndarray:
    """Cắt mép im lặng theo RMS (fallback khi không có webrtcvad)."""
    if pcm_i16.size == 0:
        return pcm_i16
    spf = max(1, int(sample_rate * frame_ms / 1000))
    n_frames = pcm_i16.size // spf
    if n_frames < 2:
        return pcm_i16
    rms_list = []
    for i in range(n_frames):
        seg = pcm_i16[i * spf : (i + 1) * spf].astype(np.float64)
        rms_list.append(float(np.sqrt(np.mean(seg * seg)) + 1e-9))
    rms_arr = np.array(rms_list)
    peak = float(np.max(rms_arr))
    thr = max(peak * 0.12, 120.0)

    speech = rms_arr >= thr
    if not np.any(speech):
        return pcm_i16
    idx = np.where(speech)[0]
    first, last = int(idx[0]), int(idx[-1])
    pad = max(1, int(0.15 * sample_rate / spf))
    first = max(0, first - pad)
    last = min(n_frames - 1, last + pad)
    out = pcm_i16[first * spf : (last + 1) * spf].copy()
    return out if out.size > 0 else pcm_i16


def _vad_trim(pcm_i16: np.ndarray, sample_rate: int, aggressiveness: int) -> np.ndarray:
    trimmed = _vad_trim_webrtc(pcm_i16, sample_rate, aggressiveness)
    if trimmed.shape == pcm_i16.shape and np.array_equal(trimmed, pcm_i16):
        trimmed = _rms_edge_trim(pcm_i16, sample_rate)
    elif trimmed.size >= pcm_i16.size * 0.98:
        trimmed = _rms_edge_trim(pcm_i16, sample_rate)
    return trimmed


def preprocess_audio_for_stt(audio, sr_recognizer) -> object:
    """
    Nhận speech_recognition.AudioData; trả về AudioData đã lọc/trim (hoặc nguyên bản nếu lỗi).
    """
    import config

    if not getattr(config, "VOICE_VAD_ENABLED", True) and not getattr(config, "VOICE_RNNOISE_ENABLED", False):
        return audio

    try:
        target = int(getattr(config, "VOICE_STT_TARGET_HZ", 16000))
        if target not in (8000, 16000, 32000, 48000):
            target = 16000

        pcm = audio.get_raw_data(convert_rate=target, convert_width=2)
        samples = _pcm16_bytes_to_numpy(pcm)

        if getattr(config, "VOICE_RNNOISE_ENABLED", True):
            samples = _denoise_rnnoise(samples, target)

        if getattr(config, "VOICE_VAD_ENABLED", True):
            samples = _vad_trim(
                samples,
                target,
                int(getattr(config, "VOICE_VAD_AGGRESSIVENESS", 2)),
            )

        if samples.size < int(target * 0.12):
            logger.debug("[Voice/STT] Sau tiền xử lý audio quá ngắn, giữ bản gốc.")
            return audio

        out_bytes = _numpy_to_pcm16_bytes(samples)
        return sr_recognizer.AudioData(out_bytes, target, 2)
    except Exception as e:
        logger.warning("[Voice/STT] preprocess_audio_for_stt: %s — dùng audio gốc.", e)
        return audio


def clamp_energy_threshold(recognizer, config_module) -> None:
    if not getattr(config_module, "VOICE_AUTO_ENERGY", True):
        return
    lo = int(getattr(config_module, "VOICE_ENERGY_THRESHOLD_MIN", 120))
    hi = int(getattr(config_module, "VOICE_ENERGY_THRESHOLD_MAX", 1800))
    try:
        v = recognizer.energy_threshold
        recognizer.energy_threshold = max(lo, min(hi, int(v)))
    except Exception:
        pass
