"""
ai/voice_control/voice_assistant.py - Module điều khiển giọng nói YoloHome

Thực hiện REQ-05, REQ-06:
  - REQ-05: Thu âm → Speech-to-Text
  - REQ-06: NLP bóc tách ý định → MQTT điều khiển thiết bị → TTS phản hồi

Pipeline:
  Microphone → Wake Word → Ghi âm → STT (Google) → NLP (Regex)
  → MQTT publish → TTS phản hồi

Tùy chọn nâng cao:
  - Tích hợp RAG với LangChain + Gemini cho các câu hỏi phức tạp hơn
    (xem phần GeminiRAGAssistant ở cuối file)

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
                        self._speak("Vâng, tôi nghe. Bạn muốn làm gì?")

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
                            self._speak("Xin lỗi, tôi chưa nghe rõ. Bạn có thể nhắc lại không?")

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
    def _process_command(self, text: str):
        """
        Phân tích câu lệnh tiếng Việt và ánh xạ thành lệnh MQTT.

        Bước 1: Chuẩn hóa text (lowercase, bỏ dấu câu)
        Bước 2: Tìm ACTION (bật/tắt)
        Bước 3: Tìm DEVICE (đèn/quạt/bơm/cửa)
        Bước 4: Publish MQTT + TTS phản hồi

        Args:
            text: Câu lệnh dạng văn bản từ STT
        """
        text_lower = text.lower().strip()
        logger.info(f"[Voice] 🧠 NLP xử lý: '{text_lower}'")

        # --- Bước 2: Tìm action ---
        action = None
        for action_key, keywords in ACTION_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                action = action_key
                break

        # --- Bước 3: Tìm thiết bị ---
        target_feed = None
        device_name = None
        for feed, info in DEVICE_KEYWORDS.items():
            if any(kw in text_lower for kw in info["keywords"]):
                target_feed = feed
                device_name = info["name_vi"]
                break

        # --- Bước 4: Thực thi lệnh ---
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

        elif not action:
            response = "Xin lỗi, tôi không hiểu bạn muốn bật hay tắt."
            logger.warning(f"[Voice] Không tìm thấy action trong: '{text_lower}'")

        elif not target_feed:
            response = "Xin lỗi, tôi không xác định được thiết bị cần điều khiển."
            logger.warning(f"[Voice] Không tìm thấy thiết bị trong: '{text_lower}'")

        # TTS phản hồi lại người dùng
        self._speak(response)

    # ------------------------------------------------------------------
    # Text-to-Speech
    # ------------------------------------------------------------------
    def _speak(self, text: str):
        """
        Phát âm thanh phản hồi bằng gTTS.
        Chạy trong thread riêng để không block vòng lắng nghe.

        Args:
            text: Câu cần phát âm (tiếng Việt)
        """
        print(f"[🔊 TTS] {text}")
        logger.info(f"[Voice] TTS: '{text}'")

        if self._gtts is None or self._pygame is None:
            return  # TTS chưa cài, bỏ qua

        def tts_task():
            try:
                import tempfile
                tts = self._gtts(text=text, lang="vi", slow=False)
                # Lưu vào file tạm rồi phát
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as fp:
                    tmp_path = fp.name
                    tts.save(tmp_path)

                self._pygame.mixer.music.load(tmp_path)
                self._pygame.mixer.music.play()

                # Chờ phát xong
                while self._pygame.mixer.music.get_busy():
                    time.sleep(0.1)

                os.remove(tmp_path)  # Dọn file tạm
            except Exception as e:
                logger.error(f"[Voice] Lỗi TTS: {e}")

        # Chạy TTS trong thread riêng để không block luồng lắng nghe
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
        self._setup_rag()

    def _setup_rag(self):
        """Khởi tạo RAG pipeline với LangChain + Gemini."""
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
            from langchain.chains import RetrievalQA
            from langchain.vectorstores import FAISS
            from langchain.document_loaders import TextLoader
            from langchain.text_splitter import CharacterTextSplitter

            # Khởi tạo Gemini LLM
            llm = ChatGoogleGenerativeAI(
                model="gemini-pro",
                google_api_key=self._api_key,
                temperature=0.3
            )

            # Khởi tạo embeddings
            embeddings = GoogleGenerativeAIEmbeddings(
                model="models/embedding-001",
                google_api_key=self._api_key
            )

            # Tải tài liệu nội bộ (knowledge base về nhà thông minh)
            kb_path = os.path.join(
                os.path.dirname(__file__), "knowledge_base.txt"
            )
            if os.path.exists(kb_path):
                loader = TextLoader(kb_path, encoding="utf-8")
                docs = loader.load()
                splitter = CharacterTextSplitter(chunk_size=500, chunk_overlap=50)
                chunks = splitter.split_documents(docs)
                vector_store = FAISS.from_documents(chunks, embeddings)

                self._chain = RetrievalQA.from_chain_type(
                    llm=llm,
                    retriever=vector_store.as_retriever(search_kwargs={"k": 3}),
                    return_source_documents=False
                )
                logger.info("[RAG] ✅ Gemini RAG Assistant đã sẵn sàng.")
            else:
                # Fallback: chỉ dùng Gemini không có RAG
                logger.warning(f"[RAG] Không tìm thấy knowledge_base.txt. Dùng Gemini thuần.")
                self._llm = llm

        except ImportError as e:
            logger.error(f"[RAG] Thiếu thư viện LangChain/Gemini: {e}")
            logger.error("[RAG] Cài đặt: pip install langchain langchain-google-genai faiss-cpu")

    def ask(self, question: str) -> str:
        """
        Đặt câu hỏi cho RAG Assistant.

        Args:
            question: Câu hỏi tiếng Việt

        Returns:
            Câu trả lời từ Gemini.
        """
        if self._chain is None:
            return "Tính năng trợ lý AI chưa được kích hoạt."
        try:
            result = self._chain.invoke({"query": question})
            return result.get("result", "Xin lỗi, tôi không tìm được câu trả lời.")
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
