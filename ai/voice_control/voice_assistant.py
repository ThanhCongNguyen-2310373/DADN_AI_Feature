"""
ai/voice_control/voice_assistant.py - Module điều khiển giọng nói YoloHome

Thực hiện REQ-05, REQ-06:
  - REQ-05: Thu âm → Speech-to-Text
  - REQ-06: NLP bóc tách ý định → MQTT điểu khiển thiết bị → TTS phản hồi

Pipeline đầy đủ:
  Microphone → Wake Word → Ghi âm → STT (Google) → NLP (Regex)
  → Nếu là lệnh điều khiển: MQTT publish → TTS phản hồi
  → Nếu là câu hỏi tư vấn: RAG (LangChain + Gemini + FAISS) → TTS phản hồi

Chạy độc lập để test:
    python ai/voice_control/voice_assistant.py
"""

import os
import sys
import time
import threading
import logging
import re
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import config
from core.mqtt_client import MQTTSingleton

logger = logging.getLogger(__name__)

# =====================================================================
# Ánh xạ từ khoá → MQTT Feed (NLP từ điển tiếng Việt)
# =====================================================================

# Từ khóa nhận biết câu hỏi tư vấn (sẽ được chuyển sang RAG thay vì NLP cơ bản)
QUESTION_KEYWORDS = [
    "là gì", "như thế nào", "bao nhiêu", "khi nào", "tại sao",
    "giải thích", "tư vấn", "hướng dẫn", "nên", "có thể",
    "ngưỡng", "an toàn", "tiết kiệm", "cảnh báo", "xử lý",
    "rò rỉ", "khí gas", "nhiệt độ", "độ ẩm", "nguy hiểm",
    "giúp", "hỏi", "cho biết", "thông tin",
]

# Từ khoá hành động
ACTION_KEYWORDS = {
    "bat":  ["bật", "mở", "khởi động", "bật lên", "mở lên", "bật on"],
    "tat":  ["tắt", "đóng", "tắt đi", "tắt xuống", "tắt off", "ngắt"],
}

# Từ khoá thiết bị → (feed_name, tên hiển thị)
DEVICE_KEYWORDS = {
    config.FEED_LED:  {
        "keywords": ["đèn", "den", "bóng đèn", "bong den", "đèn điện", "ánh sáng"],
        "name_vi": "đèn"
    },
    config.FEED_FAN:  {
        "keywords": ["quạt", "quat", "máy quạt", "may quat", "quạt điện", "quạt gió"],
        "name_vi": "quạt"
    },
    config.FEED_PUMP: {
        "keywords": ["bơm", "máy bơm", "may bom", "bơm nước", "bom nuoc", "tưới cây", "tuoi cay"],
        "name_vi": "máy bơm"
    },
    config.FEED_DOOR: {
        "keywords": ["cửa", "cua", "khóa cửa", "khoa cua", "cửa nhà", "mở cửa"],
        "name_vi": "cửa"
    },
}


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
            logger.info("[Voice] 🔊 Đang hiệu chỉnh nhiễu môi trường (2s)...")
            self._recognizer.adjust_for_ambient_noise(source, duration=2)
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
        """
        Kiểm tra xem câu văn bản có phải câu hỏi tư vấn không.
        Nếu có từ khóa câu hỏi và không có từ khóa hành động điều khiển → là câu hỏi.

        Args:
            text: Văn bản cần kiểm tra (lowercase)
        Returns:
            True nếu là câu hỏi tư vấn
        """
        has_question_keyword  = any(kw in text for kw in QUESTION_KEYWORDS)
        has_action_keyword    = any(kw in text for kws in ACTION_KEYWORDS.values() for kw in kws)
        has_device_keyword    = any(kw in text for info in DEVICE_KEYWORDS.values() for kw in info["keywords"])
        # Là câu hỏi nếu có từ khóa câu hỏi nhưng không rõ là lệnh điều khiển
        return has_question_keyword and not (has_action_keyword and has_device_keyword)

    def _process_command(self, text: str):
        """
        Phân tích câu lệnh tiếng Việt và ánh xạ thành lệnh MQTT.
        Nếu là câu hỏi tư vấn, chuyển sang RAG (Gemini) để trả lời.

        Phân luồng:
          1. Kiểm tra có phải câu hỏi không (_is_question)
             YES → Hỏi RAG → TTS phản hồi
          2. Nếu là lệnh: tìm ACTION + DEVICE → MQTT → TTS

        Args:
            text: Câu lệnh dạng văn bản từ STT
        """
        text_lower = text.lower().strip()
        logger.info(f"[Voice] 🧠 NLP xử lý: '{text_lower}'")

        # Lưu vào lịch sử chat
        self._add_to_history("user", text)

        # --- Phân luồng: câu hỏi thời tiết → WeatherService (Phase 4) ---
        _WEATHER_KEYWORDS = [
            "thời tiết", "thoi tiet", "nhiệt độ ngoài", "trời", "mưa", "nắng",
            "tuyết", "gió", "bão", "ngoài trời", "hôm nay trời", "weather",
        ]
        if any(kw in text_lower for kw in _WEATHER_KEYWORDS):
            logger.info("[Voice] 🌤 Phát hiện câu hỏi thời tiết → WeatherService")
            response = self._answer_weather(text)
            self._speak(response)
            self._add_to_history("assistant", response)
            return

        # --- Phân luồng: câu hỏi tư vấn → RAG ---
        if self._is_question(text_lower):
            logger.info(f"[Voice] 📚 Phát hiện câu hỏi tư vấn, chuyển sang RAG...")
            response = self._ask_rag(text)
            self._speak(response)
            self._add_to_history("assistant", response)
            return

        # --- Lệnh điều khiển: Tìm action ---
        action = None
        for action_key, keywords in ACTION_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                action = action_key
                break

        # --- Tìm thiết bị ---
        target_feed = None
        device_name = None
        for feed, info in DEVICE_KEYWORDS.items():
            if any(kw in text_lower for kw in info["keywords"]):
                target_feed = feed
                device_name = info["name_vi"]
                break

        # --- Thực thi lệnh điều khiển ---
        if action and target_feed:
            mqtt_value = "ON" if action == "bat" else "OFF"
            action_vi  = "bật" if action == "bat" else "tắt"

            success = self._mqtt.publish(target_feed, mqtt_value)

            if success:
                response = f"Đã {action_vi} {device_name} thành công."
                logger.info(f"[Voice] ✅ Lệnh: {action_vi} {device_name} → MQTT {target_feed}={mqtt_value}")

                # Ghi log sự kiện điều khiển
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                log_msg = f"[{timestamp}] Voice: {action_vi.capitalize()} {device_name}"
                self._mqtt.publish(config.FEED_LOG, log_msg)
            else:
                response = f"Xin lỗi, không thể kết nối đến {device_name} lúc này."
                logger.warning(f"[Voice] Publish thất bại cho feed: {target_feed}")

        elif not action and not target_feed:
            # Không nhận ra cả action lẫn device → thử hỏi RAG trước khi báo lỗi
            logger.info(f"[Voice] Không nhận ra lệnh, chuyển RAG xử lý: '{text_lower}'")
            response = self._ask_rag(text)
        elif not action:
            response = "Xin lỗi, tôi không hiểu bạn muốn bật hay tắt."
            logger.warning(f"[Voice] Không tìm thấy action trong: '{text_lower}'")
        else:
            response = "Xin lỗi, tôi không xác định được thiết bị cần điều khiển."
            logger.warning(f"[Voice] Không tìm thấy thiết bị trong: '{text_lower}'")

        # TTS phản hồi và lưu lịch sử
        self._speak(response)
        self._add_to_history("assistant", response)

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
            answer = self._rag.ask(question, history=history_ctx)
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
                tts = self._gtts(text=spoken_text, lang="vi", slow=False)
                # Lưu vào file tạm rồi phát
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
                    tmp_path = fp.name
                    tts.save(tmp_path)

                self._pygame.mixer.music.load(tmp_path)
                self._pygame.mixer.music.play()

                # Chờ phát xong
                while self._pygame.mixer.music.get_busy():
                    time.sleep(0.1)

                self._pygame.mixer.music.stop()
                self._pygame.mixer.music.unload()

                os.remove(tmp_path)  # Dọn file tạm
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

            # Cố gắng khởi tạo LangChain LLM trước; nếu lỗi vẫn giữ SDK fallback.
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI
                self._llm = ChatGoogleGenerativeAI(
                    model=selected_model,
                    temperature=0.3,
                    convert_system_message_to_human=True,
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

            # Đường dẫn đến knowledge_base.txt
            kb_path = os.path.join(os.path.dirname(__file__), "knowledge_base.txt")

            if os.path.exists(kb_path):
                # Nạp và chia nhỏ tài liệu
                loader = TextLoader(kb_path, encoding="utf-8")
                docs = loader.load()
                splitter = CharacterTextSplitter(
                    chunk_size=500,
                    chunk_overlap=80,
                    separator="\n\n"   # Chia theo đoạn văn
                )
                chunks = splitter.split_documents(docs)
                logger.info(f"[RAG] Đã tạo {len(chunks)} chunks từ knowledge_base.txt")

                # Tạo FAISS vector store
                vector_store = FAISS.from_documents(chunks, embeddings)
                self._retriever = vector_store.as_retriever(search_kwargs={"k": 3})

                # Tạo RAG chain với prompt tiếng Việt (nếu PromptTemplate khả dụng).
                prompt_template = """Bạn là trợ lý AI của hệ thống nhà thông minh YoloHome.
Hãy trả lời câu hỏi dựa trên thông tin sau đây. Trả lời ngắn gọn, rõ ràng bằng tiếng Việt.
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
                logger.warning("[RAG] Không tìm thấy knowledge_base.txt → dùng Gemini thuần.")
                self._llm = llm

        except ImportError as e:
            logger.error(f"[RAG] Thiếu thư viện: {e}")
            logger.error("[RAG] Cài đặt: pip install langchain langchain-google-genai faiss-cpu langchain-community")
        except Exception as e:
            logger.error(f"[RAG] Lỗi khởi tạo: {e}")

    def ask(self, question: str, history: list = None) -> str:
        """
        Đặt câu hỏi cho RAG Assistant, có ngữ cảnh lịch sử hội thoại.

        Args:
            question: Câu hỏi tiếng Việt
            history : List[{"role": ..., "text": ..., "time": ...}] (6 mục gần nhất)

        Returns:
            Câu trả lời từ Gemini.
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

            # Nếu có lịch sử, đính kèm vào câu hỏi
            augmented_question = question
            if history_str:
                augmented_question = (
                    f"Lịch sử hội thoại trước đó:\n{history_str}\n\n"
                    f"Câu hỏi mới: {question}"
                )

            # Ưu tiên đường RAG chain nếu có.
            if self._chain is not None:
                result = self._chain.invoke({"query": augmented_question})
                return result.get("result", "Xin lỗi, tôi không tìm được câu trả lời.")

            # Fallback: tự retrieve context rồi hỏi LLM trực tiếp.
            context = ""
            if self._retriever is not None:
                try:
                    docs = self._retriever.invoke(augmented_question)
                except Exception:
                    docs = self._retriever.get_relevant_documents(augmented_question)
                context = "\n\n".join(
                    getattr(doc, "page_content", "") for doc in docs[:3]
                ).strip()

            if context:
                prompt = (
                    "Bạn là trợ lý AI của hệ thống nhà thông minh YoloHome. "
                    "Hãy trả lời ngắn gọn, rõ ràng bằng tiếng Việt dựa trên ngữ cảnh sau.\n\n"
                    f"Ngữ cảnh:\n{context}\n\n"
                    f"Câu hỏi: {augmented_question}\n\nTrả lời:"
                )
            else:
                prompt = (
                    "Bạn là trợ lý AI của hệ thống nhà thông minh YoloHome. "
                    "Hãy trả lời ngắn gọn, rõ ràng bằng tiếng Việt.\n\n"
                    f"Câu hỏi: {augmented_question}\n\nTrả lời:"
                )

            content = None
            if self._llm is not None:
                try:
                    llm_resp = self._llm.invoke(prompt)
                    content = getattr(llm_resp, "content", llm_resp)
                except Exception as llm_err:
                    logger.warning(f"[RAG] LangChain LLM invoke lỗi, chuyển SDK fallback: {llm_err}")

            if content is None:
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
