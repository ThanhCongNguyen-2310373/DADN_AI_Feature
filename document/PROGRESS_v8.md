# PROGRESS v8

> **Ngày cập nhật:** 14/05/2026  
> **Giai đoạn:** cải tiến Voice AI — STT (lọc nhiễu + VAD), NLP (fastText/BoW + từ điển + ngữ cảnh), TTS cache, RAG tối ưu + fallback

---

## 1. Tổng quan thay đổi

| # | Hạng mục | Trạng thái |
|---|----------|-----------|
| 1 | STT: tiền xử lý webrtcvad (hoặc RMS fallback) + RNNoise tuỳ chọn | ✅ Hoàn thành |
| 2 | STT: clamp / hiệu chỉnh lại `energy_threshold` theo môi trường | ✅ Hoàn thành |
| 3 | NLP: từ điển JSON + fastText (nếu cài) hoặc BoW-softmax NumPy | ✅ Hoàn thành |
| 4 | NLP: ngữ cảnh thiết bị 12s — "tắt nó" → thiết bị gần nhất | ✅ Hoàn thành |
| 5 | TTS: cache file mp3 theo hash nội dung (giảm gọi gTTS lặp) | ✅ Hoàn thành |
| 6 | Phản hồi điều khiển ngắn ("Đã bật đèn.") | ✅ Hoàn thành |
| 7 | RAG: KB nội bộ thêm `device_manual_vi.txt`, chunk nhỏ hơn, k=2 | ✅ Hoàn thành |
| 8 | RAG: giới hạn context, max output tokens, fallback từ khóa KB | ✅ Hoàn thành |
| 9 | HTTP `/api/voice/ask` dùng chung `handle_user_text` với mic | ✅ Hoàn thành |
| 10 | Test `test_intent_nlp.py` | ✅ Hoàn thành |

---

## 2. Cải tiến STT 

### 2.1 Noise reduction
- Module [`ai/voice_control/audio_preprocess.py`](ai/voice_control/audio_preprocess.py): chuyển PCM mono 16 kHz → (tuỳ cấu hình) **RNNoise** qua `pyrnnoise` nếu import được; sau đó **webrtcvad** cắt im lặng mép câu.
- Nếu không cài được `webrtcvad`, tự động dùng **RMS edge trim** trên các khung 30 ms (fallback tương đương vai trò VAD trên môi trường thiếu wheel, ví dụ Python 3.13 build lỗi MSVC).

### 2.2 Cân chỉnh energy_threshold
- Trong [`config.py`](config.py): `VOICE_AUTO_ENERGY`, `VOICE_ENERGY_THRESHOLD_MIN/MAX`, `VOICE_AMBIENT_CALIB_SEC`, `VOICE_AMBIENT_RECALIB_SEC`, `VOICE_ENERGY_RECALIB_AFTER_TIMEOUTS`.
- Sau `adjust_for_ambient_noise`, gọi `clamp_energy_threshold`. Cứ mỗi N lần `WaitTimeoutError` trong vòng lặp wake word, hiệu chỉnh lại ngắn để bám nhiễu nền.

---

## 3. Cải tiến NLP 

### 3.1 Phân loại ý định (fastText + từ điển)
- [`ai/voice_control/intent_nlp.py`](ai/voice_control/intent_nlp.py): huấn luyện **fastText supervised** vào `ai/voice_control/data/intent_fasttext.bin` khi `import fasttext` thành công.
- Nếu không có fastText (wheel/build lỗi), huấn luyện **BoW + softmax** (NumPy), lưu `intent_bow.npz` — cùng nhãn: `control_device`, `question`, `weather`, `other`.
- Luồng xử lý văn bản: ưu tiên **từ điển** điều khiển (action + feed); sau đó câu hỏi từ điển; cuối cùng gợi ý từ mô hình (ngưỡng `ml_confidence`).

### 3.2 Mở rộng từ điển
- File [`ai/voice_control/intent_lexicon.json`](ai/voice_control/intent_lexicon.json): thêm từ khóa hành động, thiết bị (led, fan, pump…), câu hỏi, thời tiết, **đại từ thiết bị**.

### 3.3 Xử lý ngữ cảnh
- `VoiceContext` lưu `last_target_feed`, action, tên thiết bị; `config.VOICE_CONTEXT_SECS` (mặc định 12 giây).
- Câu có động từ + đại từ ("tắt nó") map feed từ lần điều khiển thành công gần nhất.

---

## 4. Cải tiến TTS 

- Thư mục cache: `config.TTS_CACHE_DIR` (mặc định `gateway/data/tts_cache/`).
- Mọi câu TTS sau chuẩn hóa markdown: hash SHA-256 (rút gọn) → file `.mp3` tái sử dụng; lần đầu mới gọi gTTS.
- Câu phản hồi điều khiển thành công rút gọn thành một câu ngắn.

---

## 5. Cải tiến RAG 

- Nạp song song [`knowledge_base.txt`](ai/voice_control/knowledge_base.txt) và [`device_manual_vi.txt`](ai/voice_control/device_manual_vi.txt).
- Tham số từ `config`: `RAG_CHUNK_SIZE`, `RAG_CHUNK_OVERLAP`, `RAG_RETRIEVER_K`, `RAG_MAX_CONTEXT_CHARS`, `RAG_MAX_OUTPUT_TOKENS`.
- Prompt và tham số LLM hướng tới **trả lời ngắn** (`short_answer` mặc định bật khi gọi từ trợ lý giọng nói).
- Khi exception trong `GeminiRAGAssistant.ask`: **fallback từ khóa** — chọn đoạn KB có nhiều từ trùng với câu hỏi (không cần embedding).

---

## 6. Danh sách file cập nhật / mới

- [`config.py`](config.py) — (đã có khối Voice v8; kiểm tra biến môi trường tùy chọn)
- [`ai/voice_control/voice_assistant.py`](ai/voice_control/voice_assistant.py)
- [`ai/voice_control/audio_preprocess.py`](ai/voice_control/audio_preprocess.py) (mới)
- [`ai/voice_control/intent_nlp.py`](ai/voice_control/intent_nlp.py) (mới)
- [`ai/voice_control/intent_lexicon.json`](ai/voice_control/intent_lexicon.json) (mới)
- [`ai/voice_control/intent_corpus.txt`](ai/voice_control/intent_corpus.txt) (mới)
- [`ai/voice_control/device_manual_vi.txt`](ai/voice_control/device_manual_vi.txt) (mới)
- [`web_app/app.py`](web_app/app.py) — `/api/voice/ask` → `handle_user_text(..., speak=False)`
- [`tests/test_e2e_phase5.py`](tests/test_e2e_phase5.py) — `FakeVoiceAssistant.handle_user_text`
- [`tests/test_intent_nlp.py`](tests/test_intent_nlp.py) (mới)
- [`requirements.txt`](requirements.txt), [`requirements.in`](requirements.in)

---

## 7. Ghi chú vận hành

- **Python 3.13 / Windows:** `webrtcvad` và `fasttext-wheel` có thể **không build** nếu thiếu Windows SDK / MSVC đầy đủ; code vẫn chạy nhờ RMS trim và BoW-softmax. Khi cần đúng thư viện native, nên dùng Python 3.11–3.12 hoặc cài đủ Windows 10/11 SDK.
- **pyrnnoise:** tuỳ chọn; tắt bằng `VOICE_RNNOISE_ENABLED=0` trong `.env` nếu gặp lỗi DLL.
- **Dung lượng:** thư mục `data/tts_cache` và `ai/voice_control/data/` (model intent) sẽ tăng dần; có thể xóa cache an toàn nếu cần.

---

## 8. Kiểm thử

- Đã chạy `pytest gateway/tests` (8 passed): smoke, E2E phase 5, intent NLP.
