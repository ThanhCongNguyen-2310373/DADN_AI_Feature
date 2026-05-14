"""
ai/voice_control/voice_assistant.py - Module điều khiển giọng nói YoloHome

Thực hiện REQ-05, REQ-06:
  - REQ-05: Thu âm → Speech-to-Text
  - REQ-06: NLP bóc tách ý định → MQTT điểu khiển thiết bị → TTS phản hồi

Pipeline đầy đủ:
  Microphone → Wake Word → Ghi âm → tiền xử lý (RNNoise tuỳ chọn + VAD/RMS) → STT (Google)
  → NLP (từ điển JSON + fastText hoặc BoW-softmax + ngữ cảnh thiết bị)
  → Nếu là lệnh điều khiển: MQTT publish → TTS (cache mp3) phản hồi ngắn
  → Nếu là câu hỏi tư vấn: RAG (LangChain + Gemini + FAISS, KB mở rộng, fallback từ khóa) → TTS

Chạy độc lập để test:
    python ai/voice_control/voice_assistant.py
"""

import os
import sys
import time
import threading
import logging
import re
import hashlib
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import config
from core.mqtt_client import MQTTSingleton
from ai.voice_control.audio_preprocess import preprocess_audio_for_stt, clamp_energy_threshold
from ai.voice_control.intent_nlp import IntentNLPEngine, VoiceContext

logger = logging.getLogger(__name__)


class VoiceAssistant:
    """
    Module điều khiển bằng giọng nói cho YoloHome.

    Luồng xử lý:
      1. Liên tục lắng nghe microphone
      2. Khi phát hiện wake word (config.WAKE_WORD)
      3. Ghi âm câu lệnh
      4. STT → text
      5. NLP bóc tách {action, device}
      6. Publish MQTT
      7. TTS phản hồi

    Cách dùng:
        assistant = VoiceAssistant()
        assistant.start()
    """

    def __init__(self):
        self._running = False
        self._thread: threading.Thread = None
        self._mqtt = MQTTSingleton.get_instance()
        self._intent = IntentNLPEngine(
            context_window_sec=getattr(config, "VOICE_CONTEXT_SECS", 12),
            ml_confidence=0.42,
        )
        self._voice_ctx = VoiceContext()
        self._listen_timeouts = 0

        # Lịch sử trò chuyện (dùng cho WebApp chat UI)
        self.chat_history = []   # [{"role": "user"|"assistant", "text": str}]

        # Import speech_recognition ở đây để tránh crash nếu chưa cài
        try:
            import speech_recognition as sr
            self._sr = sr
            self._recognizer = sr.Recognizer()
            self._recognizer.energy_threshold = config.VOICE_ENERGY_THRESHOLD
            self._recognizer.dynamic_energy_threshold = True
            logger.info("[Voice] ✅ SpeechRecognition đã sẵn sàng.")
        except ImportError:
            logger.error("[Voice] ❌ Thiếu thư viện: pip install SpeechRecognition")
            self._sr = None

        # Import gTTS cho Text-to-Speech
        try:
            from gtts import gTTS
            import pygame
            self._gtts = gTTS
            pygame.mixer.init()
            self._pygame = pygame
            logger.info("[Voice] ✅ gTTS + pygame đã sẵn sàng.")
        except ImportError:
            logger.warning("[Voice] ⚠️ Thiếu gTTS/pygame: TTS sẽ bị tắt. pip install gTTS pygame")
            self._gtts = None
            self._pygame = None

        # Khởi tạo RAG Assistant (chạy trong thread riêng để không block startup)
        self._rag: GeminiRAGAssistant = None
        rag_init_thread = threading.Thread(
            target=self._init_rag, daemon=True, name="RAG-Init"
        )
        rag_init_thread.start()

    def _init_rag(self):
        """Khởi tạo RAG trong background thread để không làm chậm startup."""
        try:
            api_key = config.GEMINI_API_KEY
            if api_key:
                self._rag = GeminiRAGAssistant(api_key=api_key)
                if self._rag.is_ready():
                    logger.info("[Voice] ✅ Gemini RAG Assistant đã sẵn sàng.")
                else:
                    logger.warning("[Voice] ⚠️ Gemini Assistant khởi tạo chưa hoàn chỉnh, sẽ dùng fallback khi có thể.")
            else:
                logger.warning("[Voice] ⚠️ GEMINI_API_KEY trống, RAG bị tắt.")
        except Exception as e:
            logger.error(f"[Voice] Lỗi khởi tạo RAG: {e}")

    # ------------------------------------------------------------------
    # Thread control
    # ------------------------------------------------------------------
    def start(self):
        """Khởi động thread lắng nghe giọng nói chạy nền."""
        if self._sr is None:
            logger.error("[Voice] Không thể khởi động: thiếu SpeechRecognition.")
            return
        if self._running:
            logger.warning("[Voice] Thread đã đang chạy.")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._listen_loop,
            daemon=True,
            name="VoiceAI-Thread"
        )
        self._thread.start()
        logger.info("[Voice] 🎙️  Thread điều khiển giọng nói đã khởi động.")
        logger.info(f"[Voice] 🔑 Wake word: '{config.WAKE_WORD}'")

    def stop(self):
        """Dừng thread lắng nghe."""
        self._running = False
        logger.info("[Voice] 🛑 Thread điều khiển giọng nói đã dừng.")

    # ------------------------------------------------------------------
    # Vòng lặp lắng nghe chính
    # ------------------------------------------------------------------
    def _listen_loop(self):
        """
        Vòng lặp liên tục lắng nghe microphone để bắt wake word.
        Sau khi bắt được wake word, ghi âm câu lệnh và xử lý.

        Lưu ý: Dùng try/except riêng để tránh crash toàn hệ thống (NFR 2.2).
        """
        sr = self._sr
        with sr.Microphone() as source:
            calib = float(getattr(config, "VOICE_AMBIENT_CALIB_SEC", 2.0))
            logger.info("[Voice] 🔊 Đang hiệu chỉnh nhiễu môi trường (%.1fs)...", calib)
            self._recognizer.adjust_for_ambient_noise(source, duration=calib)
            clamp_energy_threshold(self._recognizer, config)
            logger.info(f"[Voice] 👂 Đang lắng nghe wake word '{config.WAKE_WORD}'...")

            while self._running:
                try:
                    # Lắng nghe liên tục, timeout ngắn để không bị block
                    audio = self._recognizer.listen(
                        source,
                        timeout=1,
                        phrase_time_limit=3  # Chỉ cần ngắn cho wake word
                    )

                    # STT để bắt wake word
                    text = self._speech_to_text(audio).lower().strip()
                    if not text:
                        continue

                    logger.debug(f"[Voice] Nghe được: '{text}'")

                    # Kiểm tra có chứa wake word không
                    if config.WAKE_WORD.lower() in text:
                        logger.info(f"[Voice] 🔔 Wake word phát hiện! Đang lắng nghe lệnh...")
                        self._speak("Vâng, tôi nghe. Bạn muốn làm gì?", wait=True)

                        # Ghi âm câu lệnh thực sự
                        command_audio = self._recognizer.listen(
                            source,
                            timeout=config.VOICE_TIMEOUT,
                            phrase_time_limit=config.VOICE_PHRASE_LIMIT
                        )
                        command_text = self._speech_to_text(command_audio)
                    
                        if command_text:
                            logger.info(f"[Voice] 📝 Lệnh nhận được: '{command_text}'")
                            self._process_command(command_text)
                        else:
                            self._speak("Xin lỗi, tôi chưa nghe rõ. Bạn có thể nhắc lại không?", wait=True)

                except self._sr.WaitTimeoutError:
                    self._listen_timeouts += 1
                    nrec = int(getattr(config, "VOICE_ENERGY_RECALIB_AFTER_TIMEOUTS", 30))
                    if getattr(config, "VOICE_AUTO_ENERGY", True) and nrec > 0 and self._listen_timeouts >= nrec:
                        self._listen_timeouts = 0
                        try:
                            recalib = float(getattr(config, "VOICE_AMBIENT_RECALIB_SEC", 0.5))
                            self._recognizer.adjust_for_ambient_noise(source, duration=recalib)
                            clamp_energy_threshold(self._recognizer, config)
                            logger.debug("[Voice] Đã hiệu chỉnh lại energy_threshold theo môi trường.")
                        except Exception as e:
                            logger.debug("[Voice] Recalib mic: %s", e)
                    # Timeout bình thường khi không có âm thanh - không phải lỗi
                    pass
                except self._sr.UnknownValueError:
                    # Không nhận diện được giọng nói - bỏ qua
                    pass
                except self._sr.RequestError as e:
                    # Lỗi API (mất mạng) - log nhưng KHÔNG crash (NFR 2.2)
                    logger.error(f"[Voice] ❌ Lỗi STT API: {e}. Thử lại sau 5s...")
                    time.sleep(5)
                except Exception as e:
                    # Bắt tất cả lỗi không mong đợi để hệ thống không bị treo
                    logger.error(f"[Voice] Lỗi không xác định: {e}")
                    time.sleep(1)

    # ------------------------------------------------------------------
    # Speech-to-Text
    # ------------------------------------------------------------------
    def _speech_to_text(self, audio) -> str:
        """
        Chuyển đổi audio thành văn bản bằng Google Web Speech API.

        Args:
            audio: AudioData từ speech_recognition

        Returns:
            Chuỗi văn bản hoặc chuỗi rỗng nếu thất bại.
        """
        try:
            audio = preprocess_audio_for_stt(audio, self._sr)
            text = self._recognizer.recognize_google(
                audio,
                language=config.VOICE_LANGUAGE
            )
            return text
        except self._sr.UnknownValueError:
            return ""
        except self._sr.RequestError as e:
            logger.error(f"[Voice] STT RequestError: {e}")
            raise  # Re-raise để vòng lặp xử lý

    # ------------------------------------------------------------------
    # NLP: Bóc tách ý định (Intent Extraction)
    # ------------------------------------------------------------------
    def _is_question(self, text: str) -> bool:
        """Ủy quyền cho IntentNLPEngine (từ điển mở rộng trong intent_lexicon.json)."""
        return self._intent.is_question(text)

    def _process_command(self, text: str):
        """Giữ API cũ: xử lý lệnh và TTS."""
        self.handle_user_text(text, speak=True)

    def handle_user_text(self, text: str, *, speak: bool = True) -> str:
        """
        Luồng NLP thống nhất (mic + HTTP). Trả về nội dung phản hồi; tùy chọn TTS.
        """
        text_lower = text.lower().strip()
        logger.info(f"[Voice] 🧠 NLP xử lý: '{text_lower}'")

        self._add_to_history("user", text)

        response = ""

        if self._intent.is_weather(text_lower):
            logger.info("[Voice] 🌤 Phát hiện câu hỏi thời tiết → WeatherService")
            response = self._answer_weather(text)
        else:
            action, target_feed, device_name = self._intent.resolve_action_device(
                text_lower, self._voice_ctx
            )

            if action and target_feed:
                mqtt_value = "ON" if action == "bat" else "OFF"
                action_vi = "bật" if action == "bat" else "tắt"
                success = self._mqtt.publish(target_feed, mqtt_value)

                if success:
                    response = f"Đã {action_vi} {device_name}."
                    logger.info(
                        f"[Voice] ✅ Lệnh: {action_vi} {device_name} → MQTT {target_feed}={mqtt_value}"
                    )
                    self._voice_ctx.touch("control", target_feed, action, device_name)
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    log_msg = f"[{timestamp}] Voice: {action_vi.capitalize()} {device_name}"
                    self._mqtt.publish(config.FEED_LOG, log_msg)
                else:
                    response = f"Không kết nối được {device_name}."
                    logger.warning(f"[Voice] Publish thất bại cho feed: {target_feed}")

            elif self._is_question(text_lower):
                logger.info("[Voice] 📚 Câu hỏi tư vấn (từ điển) → RAG")
                response = self._ask_rag(text)

            else:
                ml_label, prob = self._intent.ml_predict(text_lower)
                thr = self._intent.ml_confidence
                if ml_label == "question" and prob >= thr:
                    logger.info("[Voice] 📚 Câu hỏi (mô hình) → RAG")
                    response = self._ask_rag(text)
                elif ml_label == "weather" and prob >= thr:
                    response = self._answer_weather(text)
                elif ml_label == "control_device" and prob >= thr and not action and not target_feed:
                    response = "Xin nhắc rõ thiết bị, ví dụ: bật đèn hoặc tắt quạt."
                elif not action and not target_feed:
                    logger.info(f"[Voice] Không nhận ra lệnh, chuyển RAG: '{text_lower}'")
                    response = self._ask_rag(text)
                elif not action:
                    response = "Bạn muốn bật hay tắt?"
                    logger.warning(f"[Voice] Thiếu action: '{text_lower}'")
                else:
                    response = "Không xác định được thiết bị."
                    logger.warning(f"[Voice] Thiếu thiết bị: '{text_lower}'")

        if speak:
            self._speak(response)
        self._add_to_history("assistant", response)
        return response

    def _ask_rag(self, question: str) -> str:
        """
        Trả lời câu hỏi tư vấn bằng RAG (Gemini + FAISS knowledge base).
        Truyền lịch sử hội thoại để Gemini hiểu ngữ cảnh (đa lượt).

        Args:
            question: Câu hỏi cần trả lời
        Returns:
            Câu trả lời dạng chuỗi
        """
        if self._rag is None:
            return "Tính năng trợ lý AI chưa được khởi động. Hãy kiểm tra GEMINI_API_KEY trong file .env."
        try:
            # Truyền kèm lịch sử hội thoại (tối đa 6 tin gần nhất)
            history_ctx = self.chat_history[-6:] if len(self.chat_history) > 0 else []
            answer = self._rag.ask(question, history=history_ctx, short_answer=True)
            logger.info(f"[Voice] 📚 RAG trả lời: '{answer[:80]}...'")
            return answer
        except Exception as e:
            logger.error(f"[Voice] Lỗi RAG: {e}")
            return "Xin lỗi, tôi đang gặp lỗi khi tìm kiếm thông tin. Vui lòng thử lại sau."

    def _answer_weather(self, question: str) -> str:
        """
        Trả lời câu hỏi thời tiết bằng OpenWeatherMap API (Phase 4).
        Nếu WeatherService không khả dụng, fallback sang RAG.

        Args:
            question: Câu hỏi người dùng về thời tiết
        Returns:
            Câu trả lời dạng chuỗi tiếng Việt
        """
        try:
            from core.weather_service import WeatherService
            ws = WeatherService.get_instance()
            if not ws.is_available():
                logger.info("[Voice] WeatherService không khả dụng, fallback RAG")
                return self._ask_rag(question)

            data = ws.get_current_weather()
            if not data.get("success"):
                return "Xin lỗi, tôi không thể lấy thông tin thời tiết lúc này. " \
                       + data.get("error", "")

            city        = data.get("city", "")
            temp        = data.get("temp", "?")
            feels       = data.get("feels_like", "?")
            humidity    = data.get("humidity", "?")
            desc        = data.get("description", "")
            wind        = data.get("wind_speed", "?")
            clouds      = data.get("clouds", "?")

            # Nếu RAG sẵn, inject thông tin thời tiết vào câu hỏi và hỏi Gemini
            if self._rag is not None:
                weather_ctx = (
                    f"[Thông tin thời tiết hiện tại tại {city}] "
                    f"Nhiệt độ: {temp}°C (cảm giác như {feels}°C), "
                    f"Độ ẩm: {humidity}%, "
                    f"Mô tả: {desc}, "
                    f"Gió: {wind} m/s, "
                    f"Mây: {clouds}%."
                )
                enriched = f"{weather_ctx}\n\nCâu hỏi của người dùng: {question}"
                return self._ask_rag(enriched)

            # Fallback: tổng hợp câu trả lời trực tiếp
            return (
                f"Thời tiết hiện tại tại {city}: {desc}. "
                f"Nhiệt độ {temp} độ C, cảm giác như {feels} độ. "
                f"Độ ẩm {humidity} phần trăm. Gió {wind} mét trên giây."
            )
        except Exception as e:
            logger.error(f"[Voice] Lỗi _answer_weather: {e}")
            return self._ask_rag(question)

    def _add_to_history(self, role: str, text: str):
        """
        Thêm một tin nhắn vào lịch sử trò chuyện.
        Giới hạn tối đa 100 tin nhắn để tránh tốn bộ nhớ.

        Args:
            role: "user" hoặc "assistant"
            text: Nội dung tin nhắn
        """
        self.chat_history.append({
            "role": role,
            "text": text,
            "time": datetime.now().strftime("%H:%M:%S")
        })
        if len(self.chat_history) > 100:
            self.chat_history.pop(0)

    # ------------------------------------------------------------------
    # Text-to-Speech
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_for_tts(text: str) -> str:
        """
        Chuyển markdown/formatting về plain text để TTS đọc tự nhiên.

        Ví dụ: "**Môi trường**" -> "Môi trường"
        """
        if text is None:
            return ""

        plain = str(text)
        # Markdown link: [text](url) -> text
        plain = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", plain)
        # Inline code/backticks
        plain = re.sub(r"`{1,3}([^`]*)`{1,3}", r"\1", plain)
        # Bỏ bullet marker ở đầu dòng
        plain = re.sub(r"^\s*[-*+]\s+", "", plain, flags=re.MULTILINE)
        # Bỏ ký tự markdown còn lại (bold/italic/header/quote/strike)
        plain = re.sub(r"[*_~#>]+", "", plain)
        # Giảm nhiễu khi LLM trả bảng markdown
        plain = plain.replace("|", " ")
        # Chuẩn hóa khoảng trắng/newline
        plain = re.sub(r"\s+", " ", plain).strip()
        return plain

    def _speak(self, text: str, wait: bool = False):
        """
        Phát âm thanh phản hồi bằng gTTS.
        Chạy trong thread riêng để không block vòng lắng nghe.

        Args:
            text: Câu cần phát âm (tiếng Việt)
        """
        spoken_text = self._normalize_for_tts(text)
        print(f"[🔊 TTS] {spoken_text}")
        logger.info(f"[Voice] TTS: '{spoken_text}'")

        if not spoken_text:
            return

        if self._gtts is None or self._pygame is None:
            return  # TTS chưa cài, bỏ qua

        def tts_task():
            try:
                import tempfile
                cache_dir = getattr(config, "TTS_CACHE_DIR", os.path.join("data", "tts_cache"))
                os.makedirs(cache_dir, exist_ok=True)
                key_src = spoken_text.strip().lower()
                digest = hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:20]
                cache_path = os.path.join(cache_dir, f"{digest}.mp3")

                tmp_path = cache_path
                if not os.path.isfile(cache_path):
                    tts = self._gtts(text=spoken_text, lang="vi", slow=False)
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
                        gen_path = fp.name
                    tts.save(gen_path)
                    try:
                        os.replace(gen_path, cache_path)
                    except Exception:
                        tmp_path = gen_path
                else:
                    tmp_path = cache_path

                self._pygame.mixer.music.load(tmp_path)
                self._pygame.mixer.music.play()

                while self._pygame.mixer.music.get_busy():
                    time.sleep(0.1)

                self._pygame.mixer.music.stop()
                self._pygame.mixer.music.unload()

                if tmp_path != cache_path and os.path.isfile(tmp_path):
                    os.remove(tmp_path)
            except Exception as e:
                logger.error(f"[Voice] Lỗi TTS: {e}")

        # 2. Xử lý đồng bộ hoặc chạy ngầm tùy theo tham số wait
        if wait:
            tts_task()  # Đợi nói xong mới làm việc khác
        else:
            tts_thread = threading.Thread(target=tts_task, daemon=True, name="TTS-Thread")
            tts_thread.start()


# =====================================================================
# (Tùy chọn nâng cao) RAG Assistant với LangChain + Gemini
# Kích hoạt khi câu hỏi không thuộc các lệnh điều khiển cơ bản
# =====================================================================
class GeminiRAGAssistant:
    """
    Trợ lý giọng nói nâng cao tích hợp RAG (Retrieval-Augmented Generation).

    Sử dụng:
      - LangChain làm orchestration framework
      - Google Gemini (gemini-pro) làm LLM
      - FAISS vector store cho RAG (tra cứu tài liệu nội bộ)

    Khi nào dùng:
      - Câu hỏi phức tạp: "Nhiệt độ phòng bao nhiêu là ổn?"
      - Tra cứu lịch sử: "Hôm nay đèn được bật mấy lần?"
      - Tư vấn: "Tiết kiệm điện như thế nào?"

    Cài đặt:
        pip install langchain langchain-google-genai faiss-cpu

    Cách dùng:
        rag = GeminiRAGAssistant(api_key="YOUR_GEMINI_KEY")
        answer = rag.ask("Nhiệt độ an toàn cho nhà là bao nhiêu?")
    """

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._chain = None
        self._llm = None
        self._retriever = None
        self._raw_genai_model = None
        self._rag_k = int(getattr(config, "RAG_RETRIEVER_K", 2))
        self._kb_fallback_chunks: list = []
        self._kb_plain: str = ""
        self._setup_rag()

    def is_ready(self) -> bool:
        """Kiểm tra assistant còn ít nhất một đường trả lời khả dụng."""
        return bool(self._chain is not None or self._llm is not None or self._raw_genai_model is not None)

    def _setup_rag(self):
        """
        Khởi tạo RAG pipeline với LangChain + Gemini + FAISS.
        Đọc knowledge_base.txt cùng thư mục, tạo FAISS index để tra cứu nhanh.
        """
        import asyncio
        asyncio.set_event_loop(asyncio.new_event_loop())
        try:
            import google.generativeai as genai

            # Cấu hình API key cho cả đường LangChain và đường gọi SDK trực tiếp.
            genai.configure(api_key=self._api_key)
            os.environ["GOOGLE_API_KEY"] = self._api_key

            # Chọn model hợp lệ theo API key hiện tại để tránh lỗi 404 model not found.
            selected_model = "gemini-2.0-flash"
            try:
                available = {
                    str(m.name).replace("models/", "")
                    for m in genai.list_models()
                    if "generateContent" in (getattr(m, "supported_generation_methods", []) or [])
                }
                for cand in ["gemini-3-flash-preview", "gemini-3-pro-preview", "gemini-3.1-pro-preview", "gemini-pro-latest"]:
                    if cand in available:
                        selected_model = cand
                        break
            except Exception as model_pick_err:
                logger.warning(f"[RAG] Không lấy được danh sách model, dùng mặc định {selected_model}: {model_pick_err}")

            self._raw_genai_model = genai.GenerativeModel(selected_model)

            base_dir = os.path.dirname(__file__)
            kb_files = [
                os.path.join(base_dir, "knowledge_base.txt"),
                os.path.join(base_dir, "device_manual_vi.txt"),
            ]
            plain_parts = []
            for kb_path in kb_files:
                if os.path.isfile(kb_path):
                    with open(kb_path, encoding="utf-8") as f:
                        plain_parts.append(f.read())
            self._kb_plain = "\n\n".join(plain_parts)
            self._kb_fallback_chunks = [
                p.strip() for p in self._kb_plain.split("\n\n")
                if len(p.strip()) > 30
            ]

            # Cố gắng khởi tạo LangChain LLM trước; nếu lỗi vẫn giữ SDK fallback.
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI
                max_out = int(getattr(config, "RAG_MAX_OUTPUT_TOKENS", 256))
                self._llm = ChatGoogleGenerativeAI(
                    model=selected_model,
                    temperature=0.3,
                    convert_system_message_to_human=True,
                    model_kwargs={"max_output_tokens": max_out},
                )
            except Exception as llm_err:
                self._llm = None
                logger.warning(f"[RAG] Không thể khởi tạo ChatGoogleGenerativeAI: {llm_err}. Sẽ dùng SDK fallback.")

            # Nếu không có LangChain LLM thì bỏ qua bước build RAG chain.
            if self._llm is None:
                logger.warning("[RAG] LangChain LLM không khả dụng, chạy chế độ Gemini fallback không vector DB.")
                return

            from langchain_google_genai import GoogleGenerativeAIEmbeddings
            from langchain_community.vectorstores import FAISS
            from langchain_community.document_loaders import TextLoader
            try:
                from langchain.text_splitter import CharacterTextSplitter
            except Exception:
                from langchain_text_splitters import CharacterTextSplitter

            llm = self._llm

            # Khởi tạo Google Embeddings
            embeddings = GoogleGenerativeAIEmbeddings(
                model="models/gemini-embedding-001",
            )

            all_docs = []
            for kb_path in kb_files:
                if os.path.exists(kb_path):
                    loader = TextLoader(kb_path, encoding="utf-8")
                    all_docs.extend(loader.load())

            if all_docs:
                cs = int(getattr(config, "RAG_CHUNK_SIZE", 360))
                co = int(getattr(config, "RAG_CHUNK_OVERLAP", 55))
                splitter = CharacterTextSplitter(
                    chunk_size=cs,
                    chunk_overlap=co,
                    separator="\n\n"
                )
                chunks = splitter.split_documents(all_docs)
                logger.info(f"[RAG] Đã tạo {len(chunks)} chunks từ {len(kb_files)} file KB")

                # Tạo FAISS vector store
                vector_store = FAISS.from_documents(chunks, embeddings)
                self._retriever = vector_store.as_retriever(
                    search_kwargs={"k": self._rag_k}
                )

                # Tạo RAG chain với prompt tiếng Việt (nếu PromptTemplate khả dụng).
                prompt_template = """Bạn là trợ lý AI của hệ thống nhà thông minh YoloHome.
Hãy trả lời câu hỏi dựa trên thông tin sau đây. Trả lời ngắn gọn, rõ ràng bằng tiếng Việt (ưu tiên tối đa 2 câu).
Nếu không tìm thấy thông tin phù hợp, hãy trả lời dựa trên kiến thức chung của bạn.

Thông tin tham khảo:
{context}

Câu hỏi: {question}

Trả lời:"""
                PROMPT = None
                try:
                    from langchain.prompts import PromptTemplate
                    PROMPT = PromptTemplate(
                        template=prompt_template,
                        input_variables=["context", "question"],
                    )
                except Exception as prompt_err:
                    logger.warning(
                        f"[RAG] Không import được PromptTemplate ({prompt_err}). "
                        "Sẽ dùng prompt mặc định của RetrievalQA hoặc fallback thủ công."
                    )

                # Ưu tiên dùng RetrievalQA nếu bản langchain hiện tại còn hỗ trợ.
                try:
                    from langchain.chains import RetrievalQA
                    chain_type_kwargs = {"prompt": PROMPT} if PROMPT is not None else {}
                    self._chain = RetrievalQA.from_chain_type(
                        llm=llm,
                        chain_type="stuff",
                        retriever=self._retriever,
                        chain_type_kwargs=chain_type_kwargs,
                        return_source_documents=False,
                    )
                except Exception as chain_err:
                    self._chain = None
                    logger.warning(
                        f"[RAG] RetrievalQA không khả dụng ở phiên bản langchain hiện tại: {chain_err}. "
                        "Sẽ dùng fallback LLM + retriever thủ công."
                    )
                logger.info("[RAG] ✅ Gemini RAG Assistant đã sẵn sàng với knowledge base.")
            else:
                # Fallback: Gemini thuần không có RAG
                logger.warning("[RAG] Không tìm thấy file knowledge base → dùng Gemini thuần.")
                self._llm = llm

        except ImportError as e:
            logger.error(f"[RAG] Thiếu thư viện: {e}")
            logger.error("[RAG] Cài đặt: pip install langchain langchain-google-genai faiss-cpu langchain-community")
        except Exception as e:
            logger.error(f"[RAG] Lỗi khởi tạo: {e}")

    def _clip_context(self, context: str) -> str:
        mx = int(getattr(config, "RAG_MAX_CONTEXT_CHARS", 1800))
        if len(context) <= mx:
            return context
        return context[:mx] + "\n...[rút gọn]..."

    def _keyword_fallback(self, question: str) -> str | None:
        """Trả lời tĩnh từ đoạn KB khớp từ khóa khi LLM/RAG lỗi."""
        if not self._kb_fallback_chunks:
            return None
        q = question.lower()
        words = set(re.findall(r"[\wàáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổộổơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]+", q))
        words = {w for w in words if len(w) > 2}
        if len(words) < 1:
            return None
        best_score = 0
        best_para = None
        for para in self._kb_fallback_chunks:
            pl = para.lower()
            score = sum(1 for w in words if w in pl)
            if score > best_score:
                best_score = score
                best_para = para
        if best_score >= 2 and best_para:
            return best_para[:900].strip()
        return None

    def ask(self, question: str, history: list = None, short_answer: bool = True) -> str:
        """
        Đặt câu hỏi cho RAG Assistant, có ngữ cảnh lịch sử hội thoại.

        Args:
            question: Câu hỏi tiếng Việt
            history : List[{"role": ..., "text": ..., "time": ...}] (6 mục gần nhất)
            short_answer: ép câu trả lời ngắn (prompt + max tokens)

        Returns:
            Câu trả lời từ Gemini hoặc fallback KB.
        """
        if self._llm is None and self._raw_genai_model is None:
            return "Tính năng trợ lý AI chưa được kích hoạt."
        try:
            # Xây dựng context lịch sử hội thoại nếu có
            history_str = ""
            if history:
                lines = []
                for item in history[-6:]:
                    role = "Người dùng" if item.get("role") == "user" else "Trợ lý"
                    lines.append(f"{role}: {item.get('text', '')}")
                history_str = "\n".join(lines)

            augmented_question = question
            if history_str:
                augmented_question = (
                    f"Lịch sử hội thoại trước đó:\n{history_str}\n\n"
                    f"Câu hỏi mới: {question}"
                )

            query_for_chain = augmented_question
            if short_answer:
                query_for_chain = (
                    augmented_question
                    + "\n\n(Yêu cầu: trả lời tối đa 1–2 câu, tiếng Việt, đi thẳng vào ý chính.)"
                )

            # Ưu tiên đường RAG chain nếu có.
            if self._chain is not None:
                result = self._chain.invoke({"query": query_for_chain})
                return result.get("result", "Xin lỗi, tôi không tìm được câu trả lời.")

            # Fallback: tự retrieve context rồi hỏi LLM trực tiếp.
            context = ""
            if self._retriever is not None:
                try:
                    docs = self._retriever.invoke(query_for_chain)
                except Exception:
                    docs = self._retriever.get_relevant_documents(query_for_chain)
                rk = getattr(self, "_rag_k", 2)
                context = "\n\n".join(
                    getattr(doc, "page_content", "") for doc in docs[:rk]
                ).strip()
            context = self._clip_context(context)

            short_hint = (
                " Trả lời tối đa 1–2 câu tiếng Việt."
                if short_answer else ""
            )
            if context:
                prompt = (
                    "Bạn là trợ lý AI của hệ thống nhà thông minh YoloHome."
                    + short_hint
                    + " Dựa trên ngữ cảnh sau.\n\n"
                    f"Ngữ cảnh:\n{context}\n\n"
                    f"Câu hỏi: {query_for_chain}\n\nTrả lời:"
                )
            else:
                prompt = (
                    "Bạn là trợ lý AI của hệ thống nhà thông minh YoloHome."
                    + short_hint
                    + "\n\n"
                    f"Câu hỏi: {query_for_chain}\n\nTrả lời:"
                )

            content = None
            if self._llm is not None:
                try:
                    llm_resp = self._llm.invoke(prompt)
                    content = getattr(llm_resp, "content", llm_resp)
                except Exception as llm_err:
                    logger.warning(f"[RAG] LangChain LLM invoke lỗi, chuyển SDK fallback: {llm_err}")

            if content is None:
                max_out = int(getattr(config, "RAG_MAX_OUTPUT_TOKENS", 256))
                try:
                    import google.generativeai as genai
                    gen_cfg = genai.types.GenerationConfig(max_output_tokens=max_out)
                    raw_resp = self._raw_genai_model.generate_content(
                        prompt,
                        generation_config=gen_cfg,
                    )
                except (TypeError, AttributeError):
                    raw_resp = self._raw_genai_model.generate_content(prompt)
                content = getattr(raw_resp, "text", "")
            if isinstance(content, list):
                text_parts = []
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        text_parts.append(str(part["text"]))
                    else:
                        text_parts.append(str(part))
                content = " ".join(text_parts)

            final_text = str(content).strip()
            return final_text or "Xin lỗi, tôi không nhận được nội dung trả lời từ mô hình AI."
        except Exception as e:
            logger.error(f"[RAG] Lỗi khi hỏi Gemini: {e}")
            fb = self._keyword_fallback(question)
            if fb:
                logger.info("[RAG] Dùng fallback từ khóa trên knowledge base.")
                return fb
            return "Xin lỗi, đã có lỗi khi xử lý câu hỏi của bạn."


# =====================================================================
# Chạy trực tiếp để test
# =====================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    print("\n🏠 YoloHome - Voice Control Test")
    print("=" * 40)
    print(f"Wake word: '{config.WAKE_WORD}'")
    print("Nói lệnh ví dụ: 'Yolo ơi bật đèn' | 'Yolo ơi tắt quạt'")
    print("Nhấn Ctrl+C để dừng\n")

    assistant = VoiceAssistant()
    assistant.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        assistant.stop()
        print("\nĐã dừng Voice Assistant.")
