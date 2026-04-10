"""
YoloHome Web App - FastAPI Backend (Phase 4)
---------------------------------------------
- /               : Dashboard chính (HTML)
- /login          : Trang đăng nhập
- /members        : Quản lý khuôn mặt (Face Enrollment)
- /api/sensors    : JSON cảm biến mới nhất
- /api/history    : JSON lịch sử cảm biến (SQLite)
- /api/energy     : JSON báo cáo điện năng
- /api/weather    : JSON thời tiết ngoài trời (OpenWeatherMap)
- /api/chat       : JSON lịch sử chat Voice
- /api/control    : POST điều khiển thiết bị
- /api/rules      : CRUD quản lý Rule Engine
- /api/face/log   : JSON log nhận diện
- /api/face/enroll: POST đăng ký khuôn mặt qua web
- /api/face/train : POST huấn luyện lại model
- /api/face/members: GET danh sách thành viên
- /video_feed     : MJPEG stream
- /ws/sensors     : WebSocket real-time
- /docs           : Swagger UI
"""

import sys
import os
import time
import json
import threading
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any, Literal
from contextlib import asynccontextmanager

import cv2
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request, Response, Depends, Form, status, Query
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

import config
from core.auth_service import AuthService
from core.rate_limiter import get_rate_limiter
from core.observability import ObservabilityMiddleware, metrics_response, init_tracing

# Thêm gateway/ vào sys.path để import các module nội bộ
GATEWAY_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(GATEWAY_DIR))

logger = logging.getLogger(__name__)

# ──────────────────────────── Shared State ─────────────────────────────────
# Các module này được inject bởi main.py khi khởi động Web App
_sensor_reader = None
_voice_assistant = None
_face_recognizer = None

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self._clients: List[WebSocket] = []
        self._lock = threading.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        with self._lock:
            self._clients.append(ws)

    def disconnect(self, ws: WebSocket):
        with self._lock:
            self._clients = [c for c in self._clients if c != ws]

    async def broadcast(self, data: dict):
        """Gửi dữ liệu JSON đến tất cả clients đang kết nối."""
        dead = []
        with self._lock:
            clients_copy = list(self._clients)
        for ws in clients_copy:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

ws_manager = ConnectionManager()

# ───────────────────────── Session / Auth ───────────────────────────
_auth = AuthService.get_instance()
_rate_limiter = get_rate_limiter()


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    token = request.cookies.get("session_token", "")
    return _auth.get_session_user(token)


def require_auth(request: Request) -> Dict[str, Any]:
    """Dependency: bắt buộc đăng nhập, trả về user session hiện tại."""
    user = _get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/login"},
        )
    request.state.user = user
    return user


def require_role(*allowed_roles: str):
    def _dep(user: Dict[str, Any] = Depends(require_auth)):
        role = str(user.get("role", "viewer"))
        if role not in allowed_roles:
            raise HTTPException(status_code=403, detail="Không đủ quyền truy cập")
        return user
    return _dep


require_operator = require_role(config.ROLE_ADMIN, config.ROLE_OPERATOR)
require_admin = require_role(config.ROLE_ADMIN)


# ───────────────────────── Pydantic Models ────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Khởi động background task broadcast sensor data qua WebSocket."""
    import asyncio

    _auth.bootstrap_default_admin()
    init_tracing("yolohome-gateway")

    async def _broadcast_loop():
        while True:
            try:
                data = _get_sensor_data()
                await ws_manager.broadcast({"type": "sensors", "data": data})
            except Exception as e:
                logger.debug(f"[WebApp] broadcast error: {e}")
            await asyncio.sleep(3)  # Cập nhật mỗi 3 giây

    task = asyncio.create_task(_broadcast_loop())
    yield
    task.cancel()


# ─────────────────────────── FastAPI App ────────────────────────────────────
app = FastAPI(
    title="YoloHome API",
    description=(
        "## Smart Home IoT + AI Gateway\n\n"
        "API quản lý và điều khiển hệ thống nhà thông minh YoloHome.\n\n"
        "### Xác thực\n"
        "Tất cả API (trừ `/login`) yêu cầu đăng nhập trước tại "
        "[/login](/login). Cookie `session_token` sẽ được set tự động.\n\n"
        "### Tags\n"
        "- **IoT Control** – Điều khiển thiết bị qua MQTT\n"
        "- **Statistics** – Dữ liệu lịch sử, biểu đồ, năng lượng\n"
        "- **AI Features** – Nhận diện khuôn mặt, giọng nói, thời tiết\n"
        "- **Automation** – Rule Engine (Nếu-Thì)\n"
        "- **Security** – Xác thực, session\n"
    ),
    version="4.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)
app.add_middleware(ObservabilityMiddleware)

# Static files & Templates
_STATIC_DIR = Path(__file__).parent / "static"
_TEMPLATES_DIR = Path(__file__).parent / "templates"

app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# ───────────────────────── Pydantic Models ────────────────────────
class ControlCommand(BaseModel):
    device: Literal["led", "fan", "door", "pump"] = Field(
        ..., description="Thiết bị cần điều khiển"
    )
    value: Any = Field(
        ..., description="Giá trị: 1/0 (bật/tắt) hoặc góc servo 0-180 cho door"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"device": "led", "value": 1},
                {"device": "fan", "value": 0},
                {"device": "door", "value": 1},
            ]
        }
    }


class EnrollRequest(BaseModel):
    person_name: str = Field(
        ..., min_length=2, max_length=50,
        description="Tên thành viên cần đăng ký (chữ và số)"
    )
    num_samples: int = Field(
        30, ge=10, le=100,
        description="Số ảnh mẫu cần chụp (10–100, mặc định 30)"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{"person_name": "Nguyen Van A", "num_samples": 30}]
        }
    }


class RuleCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100, description="Tên quy tắc")
    condition_field: Literal["temp", "humi", "gas"] = Field(
        ..., description="Trường cảm biến áp dụng điều kiện"
    )
    condition_op: Literal[">", "<", ">=", "<=", "=="] = Field(
        ..., description="Toán tử so sánh"
    )
    condition_value: float = Field(..., description="Giá trị ngưỡng")
    action_device: Literal["led", "fan", "pump", "door"] = Field(
        ..., description="Thiết bị thực hiện khi điều kiện đúng"
    )
    action_state: int = Field(..., ge=0, le=1, description="Trạng thái: 1=BẬT, 0=TẮT")
    notify_telegram: bool = Field(False, description="Gửi thông báo Telegram khi kích hoạt")
    enabled: bool = Field(True, description="Bật/tắt quy tắc")

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "name": "Bật quạt khi nhiệt độ cao",
                "condition_field": "temp",
                "condition_op": ">",
                "condition_value": 32.0,
                "action_device": "fan",
                "action_state": 1,
                "notify_telegram": True,
                "enabled": True,
            }]
        }
    }


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=128)
    role: Literal["admin", "operator", "viewer"] = Field("viewer")
    full_name: Optional[str] = Field(default=None, max_length=120)
    email: Optional[str] = Field(default=None, max_length=120)
    phone: Optional[str] = Field(default=None, max_length=30)
    department: Optional[str] = Field(default=None, max_length=120)


class UserRoleUpdate(BaseModel):
    role: Literal["admin", "operator", "viewer"]


class VoiceAskRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=500)


# ─────────────────────────── Helper Functions ───────────────────────────────
def _get_sensor_data() -> Dict[str, Any]:
    """Lấy dữ liệu cảm biến từ SensorReader hoặc giả lập."""
    if _sensor_reader is not None:
        try:
            return _sensor_reader.get_latest_data()
        except Exception:
            pass
    # Fallback: giả lập khi chạy standalone
    return {
        "temperature": 28.5,
        "humidity": 65.0,
        "gas": 120,
        "led": 0,
        "fan": 0,
        "door": 0,
        "timestamp": time.strftime("%H:%M:%S"),
    }


def _get_chat_history() -> List[Dict]:
    """Lấy lịch sử chat từ VoiceAssistant."""
    if _voice_assistant is not None:
        try:
            return list(_voice_assistant.chat_history)
        except Exception:
            pass
    return []


def _get_face_log() -> List[Dict]:
    """Lấy log sự kiện nhận diện khuôn mặt gần nhất."""
    log_dir = GATEWAY_DIR / "logs" / "face_events"
    if not log_dir.exists():
        return []
    entries = []
    for f in sorted(log_dir.glob("*.jpg"), key=os.path.getmtime, reverse=True)[:10]:
        entries.append({
            "filename": f.name,
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(f))),
            "url": f"/face_log/{f.name}",
        })
    return entries


# ───────────────────────── Routes ───────────────────────────────

# ── Auth ──
@app.get("/login", response_class=HTMLResponse, tags=["Security"],
         summary="Trang đăng nhập", include_in_schema=False)
async def login_page(request: Request):
    if _get_current_user(request):
        return RedirectResponse("/")
    return templates.TemplateResponse(request=request, name="login.html", context={"error": ""})


@app.post("/login", tags=["Security"],
          summary="Xử lý đăng nhập",
          description="Xác thực tài khoản, set cookie `session_token` (TTL 8h). "
                      "Bảo vệ brute-force: khoá IP sau 5 lần sai trong 5 phút.",
          include_in_schema=False)
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    client_ip = _get_client_ip(request)
    blocked, _ = _rate_limiter.check(client_ip)
    if blocked:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"request": request, "error": "Quá nhiều lần thử. Vui lòng đợi 5 phút."},
            status_code=429,
        )

    user = _auth.authenticate(username=username, password=password)
    if user:
        _rate_limiter.reset(client_ip)
        token = _auth.create_session(int(user["id"]))
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("session_token", token, httponly=True, samesite="lax")
        return resp

    _rate_limiter.record_failure(client_ip)
    _, remaining = _rate_limiter.check(client_ip)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"request": request, "error": f"Sai tên đăng nhập hoặc mật khẩu. Còn {remaining} lần thử."},
        status_code=401,
    )


@app.get("/logout", tags=["Security"], summary="Đăng xuất", include_in_schema=False)
async def logout(request: Request):
    token = request.cookies.get("session_token")
    _auth.delete_session(token)
    resp = RedirectResponse("/login")
    resp.delete_cookie("session_token")
    return resp


# ── Dashboard ──
@app.get("/", response_class=HTMLResponse, tags=["Security"],
         summary="Dashboard chính", include_in_schema=False)
async def index(request: Request, _=Depends(require_auth)):
    """Trang dashboard chính."""
    return templates.TemplateResponse(request=request, name="index.html", context={"request": request})


# ── Members (Face Enrollment) ──
@app.get("/members", response_class=HTMLResponse, tags=["AI Features"],
         summary="Trang quản lý khuôn mặt", include_in_schema=False)
async def members_page(request: Request, _=Depends(require_operator)):
    return templates.TemplateResponse(request=request, name="members.html", context={"request": request})


@app.get("/api/sensors", tags=["IoT Control"],
         summary="Dữ liệu cảm biến hiện tại",
         description="Trả về snapshot dữ liệu cảm biến mới nhất: nhiệt độ, độ ẩm, khí gas và trạng thái thiết bị.")
async def get_sensors(request: Request, _=Depends(require_auth)):
    return JSONResponse(_get_sensor_data())


@app.get("/api/history", tags=["Statistics"],
         summary="Lịch sử cảm biến (cho Chart.js)",
         description="Trả về tối đa 500 bản ghi cảm biến từ SQLite trong N giờ gần nhất. "
                     "Dùng để vẽ biểu đồ đường trên Dashboard.")
async def get_history(
    hours: int = Query(24, ge=1, le=168, description="Số giờ lịch sử cần lấy (1–168)"),
    _=Depends(require_auth)
):
    try:
        from core.database import DatabaseSingleton
        db = DatabaseSingleton.get_instance()
        rows = db.get_sensor_history(hours=hours, limit=500)
        return JSONResponse({"data": rows, "hours": hours, "count": len(rows)})
    except Exception as e:
        return JSONResponse({"data": [], "error": str(e)})


@app.get("/api/energy", tags=["Statistics"],
         summary="Báo cáo điện năng tiêu thụ",
         description="Tính thời gian bật (giờ) và ước tính kWh cho từng thiết bị "
                     "dựa trên bảng `device_events` trong SQLite. "
                     "Công suất: LED=6W, Quạt=40W, Máy bơm=30W, Cửa=5W.")
async def get_energy(
    hours: int = Query(24, ge=1, le=168, description="Số giờ tính toán"),
    _=Depends(require_auth)
):
    try:
        from core.database import DatabaseSingleton
        db = DatabaseSingleton.get_instance()
        report = db.get_energy_report(hours=hours)
        return JSONResponse(report)
    except Exception as e:
        return JSONResponse({"error": str(e)})


@app.get("/api/weather", tags=["AI Features"],
         summary="Thời tiết hiện tại (OpenWeatherMap)",
         description="Lấy thông tin thời tiết ngoài trời từ OpenWeatherMap API. "
                     "Kết quả được cache 10 phút để tránh gọi API quá nhiều. "
                     "Dùng trong Voice Assistant khi hỏi về thời tiết.")
async def get_weather(_=Depends(require_auth)):
    try:
        from core.weather_service import WeatherService
        ws = WeatherService.get_instance()
        data = ws.get_current_weather()
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e), "available": False})


@app.get("/api/chat", tags=["AI Features"],
         summary="Lịch sử hội thoại Voice Assistant",
         description="Trả về toàn bộ lịch sử trò chuyện của Voice Assistant trong phiên hiện tại.")
async def get_chat(_=Depends(require_auth)):
    return JSONResponse({"history": _get_chat_history()})


@app.get("/api/me", tags=["Security"],
         summary="Hồ sơ người dùng hiện tại",
         description="Trả về thông tin profile + role từ session hiện tại.")
async def get_me(user: Dict[str, Any] = Depends(require_auth)):
    safe = {
        "id": user.get("user_id") or user.get("id"),
        "username": user.get("username"),
        "role": user.get("role"),
        "full_name": user.get("full_name"),
        "email": user.get("email"),
        "phone": user.get("phone"),
        "department": user.get("department"),
    }
    return JSONResponse({"user": safe})


@app.get("/api/users", tags=["Security"],
         summary="Danh sách người dùng",
         description="Admin API: liệt kê người dùng và role.")
async def list_users(_=Depends(require_admin)):
    return JSONResponse({"users": _auth.list_users()})


@app.post("/api/users", tags=["Security"],
          summary="Tạo người dùng mới",
          description="Admin API: tạo user mới với profile và role.")
async def create_user(req: UserCreate, _=Depends(require_admin)):
    user_id = _auth.create_user(
        username=req.username,
        password=req.password,
        role=req.role,
        full_name=req.full_name,
        email=req.email,
        phone=req.phone,
        department=req.department,
    )
    return JSONResponse({"status": "created", "id": user_id})


@app.patch("/api/users/{user_id}/role", tags=["Security"],
           summary="Cập nhật role người dùng",
           description="Admin API: đổi vai trò cho user.")
async def update_user_role(user_id: int, req: UserRoleUpdate, _=Depends(require_admin)):
    ok = _auth.update_user_role(user_id=user_id, role=req.role)
    if not ok:
        raise HTTPException(status_code=404, detail="Không tìm thấy user")
    return JSONResponse({"status": "updated", "id": user_id, "role": req.role})


@app.post("/api/voice/ask", tags=["AI Features"],
          summary="Hỏi đáp AI qua HTTP",
          description="API hỏi đáp không cần microphone; dùng chung logic với Voice Assistant.")
async def voice_ask(req: VoiceAskRequest, _=Depends(require_auth)):
    if _voice_assistant is None:
        return JSONResponse({"answer": "Voice Assistant chưa được khởi động."})
    try:
        text = req.question.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Question rỗng")

        if hasattr(_voice_assistant, "_answer_weather") and any(
            kw in text.lower() for kw in ["thời tiết", "thoi tiet", "trời", "mưa", "nắng", "weather"]
        ):
            ans = _voice_assistant._answer_weather(text)
        elif hasattr(_voice_assistant, "_ask_rag"):
            ans = _voice_assistant._ask_rag(text)
        else:
            ans = "Voice Assistant không hỗ trợ ask API ở phiên bản hiện tại."
        return JSONResponse({"answer": ans})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/ml/forecast", tags=["AI Features"],
         summary="Dự báo tiêu thụ năng lượng",
         description="ML endpoint: dự báo kWh trong các giờ tới từ lịch sử bật/tắt thiết bị.")
async def ml_forecast(
    history_hours: int = Query(48, ge=12, le=240),
    horizon_hours: int = Query(6, ge=1, le=24),
    _=Depends(require_auth),
):
    from core.ml_analytics import MLEnergyAnalytics
    data = MLEnergyAnalytics().forecast_energy(history_hours=history_hours, horizon_hours=horizon_hours)
    return JSONResponse(data)


@app.get("/api/ml/anomalies", tags=["AI Features"],
         summary="Phát hiện hành vi bất thường",
         description="ML endpoint: phát hiện bất thường từ sensor/energy bằng z-score.")
async def ml_anomalies(
    hours: int = Query(24, ge=6, le=168),
    z_threshold: float = Query(3.0, ge=2.0, le=5.0),
    _=Depends(require_auth),
):
    from core.ml_analytics import MLEnergyAnalytics
    data = MLEnergyAnalytics().detect_anomalies(hours=hours, z_threshold=z_threshold)
    return JSONResponse(data)


@app.get("/api/face/log", tags=["AI Features"],
         summary="Nhật ký nhận diện khuôn mặt",
         description="Trả về 10 sự kiện nhận diện gần nhất dưới dạng URL ảnh JPEG.")
async def get_face_log(_=Depends(require_auth)):
    return JSONResponse({"events": _get_face_log()})


@app.get("/api/face/members", tags=["AI Features"],
         summary="Danh sách thành viên đã đăng ký",
         description="Quét thư mục `dataset/` và trả về tên + số ảnh mẫu của từng người.")
async def get_face_members(_=Depends(require_auth)):
    import config as cfg
    dataset_dir = Path(GATEWAY_DIR) / cfg.FACE_DATASET_DIR
    members = []
    if dataset_dir.exists():
        for d in dataset_dir.iterdir():
            if d.is_dir():
                count = len(list(d.glob("*.jpg")))
                members.append({"name": d.name, "samples": count})
    return JSONResponse({"members": members})


@app.post("/api/face/enroll", tags=["AI Features"],
          summary="Bắt đầu đăng ký khuôn mặt",
          description="Chụp `num_samples` ảnh khuôn mặt từ webcam và lưu vào thư mục dataset. "
                      "Chạy trong background thread, trả về ngay (non-blocking). "
                      "Sau khi chụp xong, gọi `/api/face/train` để retrain model.")
async def face_enroll(req: EnrollRequest, _=Depends(require_operator)):
    name = req.person_name.strip()
    if not name or not name.replace(" ", "").isalnum():
        raise HTTPException(status_code=400, detail="Tên không hợp lệ (chỉ chữ + số).")

    def _do_enroll():
        try:
            import config as cfg
            face_cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            )
            save_dir = Path(GATEWAY_DIR) / cfg.FACE_DATASET_DIR / name
            save_dir.mkdir(parents=True, exist_ok=True)

            cap = cv2.VideoCapture(cfg.CAMERA_INDEX)
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
            count = 0
            while count < req.num_samples:
                ret, frame = cap.read()
                if not ret:
                    break
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = face_cascade.detectMultiScale(gray, 1.1, 4, minSize=(50, 50))
                for (x, y, w, h) in faces:
                    face_roi = cv2.resize(gray[y:y+h, x:x+w], (100, 100))
                    img_path = save_dir / f"{name}_{count:03d}.jpg"
                    cv2.imwrite(str(img_path), face_roi)
                    count += 1
                    if count >= req.num_samples:
                        break
                time.sleep(0.1)
            cap.release()
            logger.info(f"[Enroll] Đã chụp {count} ảnh cho '{name}'")
        except Exception as e:
            logger.error(f"[Enroll] Lỗi: {e}")

    threading.Thread(target=_do_enroll, daemon=True, name="FaceEnroll").start()
    return {"status": "started", "person": name, "samples": req.num_samples}


@app.post("/api/face/train", tags=["AI Features"],
          summary="Train lại LBPH model",
          description="Chạy lại quá trình huấn luyện LBPH từ toàn bộ dataset hiện tại. "
                      "Chạy trong background thread, trả về ngay. Mất khoảng 5–30 giây.")
async def face_train(_=Depends(require_operator)):
    def _do_train():
        try:
            sys.path.insert(0, str(GATEWAY_DIR))
            from ai.face_recognition.face_register import train_face_model
            train_face_model()
            logger.info("[Train] ✅ Model LBPH đã được train lại thành công.")
        except Exception as e:
            logger.error(f"[Train] Lỗi train model: {e}")

    threading.Thread(target=_do_train, daemon=True, name="FaceTrain").start()
    return {"status": "training_started"}


@app.post("/api/control", tags=["IoT Control"],
          summary="Điều khiển thiết bị",
          description="Publish lệnh điều khiển lên Adafruit IO MQTT và ghi sự kiện vào SQLite. "
                      "Source được đánh dấu là `web` trong bảng `device_events`.")
async def control_device(cmd: ControlCommand, request: Request, _=Depends(require_operator)):
    from core.mqtt_client import MQTTSingleton
    from config import (
        FEED_LED, FEED_FAN, FEED_DOOR, FEED_PUMP,
    )

    feed_map = {
        "led":  FEED_LED,
        "fan":  FEED_FAN,
        "door": FEED_DOOR,
        "pump": FEED_PUMP,
    }
    device = cmd.device.lower()
    if device not in feed_map:
        raise HTTPException(status_code=400, detail=f"Unknown device: {device}")

    try:
        mqtt = MQTTSingleton.get_instance()
        mqtt.publish(feed_map[device], str(cmd.value))
        # Ghi vào SQLite
        try:
            from core.database import DatabaseSingleton
            db = DatabaseSingleton.get_instance()
            state = 1 if str(cmd.value).upper() in ("1", "ON") else 0
            db.insert_device_event(device, state, source="web")
        except Exception:
            pass
        return {"status": "ok", "device": device, "value": cmd.value}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Rule Engine API ──
@app.get("/api/rules", tags=["Automation"],
         summary="Danh sách tất cả quy tắc",
         description="Trả về toàn bộ các quy tắc 'Nếu-Thì' từ bảng `automation_rules` trong SQLite.")
async def list_rules(_=Depends(require_auth)):
    try:
        from core.database import DatabaseSingleton
        db = DatabaseSingleton.get_instance()
        return JSONResponse({"rules": db.get_rules()})
    except Exception as e:
        return JSONResponse({"rules": [], "error": str(e)})


@app.post("/api/rules", tags=["Automation"],
          summary="Tạo quy tắc mới",
          description="Thêm một quy tắc tự động hoá mới. Ví dụ: "
                      "'Nếu temp > 32 → bật quạt + gửi Telegram'.")
async def create_rule(rule: RuleCreate, _=Depends(require_operator)):
    try:
        from core.database import DatabaseSingleton
        db = DatabaseSingleton.get_instance()
        rule_id = db.insert_rule(
            name=rule.name,
            condition_field=rule.condition_field,
            condition_op=rule.condition_op,
            condition_value=rule.condition_value,
            action_device=rule.action_device,
            action_state=rule.action_state,
            notify_telegram=int(rule.notify_telegram),
            enabled=int(rule.enabled),
        )
        return {"status": "created", "id": rule_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/rules/{rule_id}", tags=["Automation"],
            summary="Xoá quy tắc",
            description="Xoá quy tắc theo ID. Rule Engine sẽ ngừng áp dụng quy tắc này ngay lập tức.")
async def delete_rule(rule_id: int, _=Depends(require_operator)):
    try:
        from core.database import DatabaseSingleton
        db = DatabaseSingleton.get_instance()
        db.delete_rule(rule_id)
        return {"status": "deleted", "id": rule_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/rules/{rule_id}/toggle", tags=["Automation"],
           summary="Bật/tắt quy tắc",
           description="Chuyển đổi trạng thái enabled/disabled của một quy tắc mà không xoá nó.")
async def toggle_rule(rule_id: int, _=Depends(require_operator)):
    try:
        from core.database import DatabaseSingleton
        db = DatabaseSingleton.get_instance()
        new_state = db.toggle_rule(rule_id)
        return {"status": "toggled", "id": rule_id, "enabled": new_state}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health", tags=["Security"],
         summary="Health check")
async def health():
    return {"status": "ok", "service": "yolohome-gateway"}


@app.get("/metrics", tags=["Security"],
         summary="Prometheus metrics")
async def metrics():
    if not config.METRICS_ENABLED:
        return JSONResponse({"enabled": False, "message": "Metrics disabled"}, status_code=404)
    return metrics_response()


@app.get("/video_feed", tags=["AI Features"],
         summary="Camera MJPEG stream",
         description="Stream video trực tiếp từ webcam dưới dạng MJPEG (~10 FPS). "
                     "Dùng trong thẻ `<img src='/video_feed'>` trên Dashboard.")
async def video_feed():
    def generate():
        # Lấy frame từ FaceRecognizer nếu có, không thì mở camera trực tiếp
        cap = None
        try:
            if _face_recognizer is not None and hasattr(_face_recognizer, "_cap"):
                cap = _face_recognizer._cap
            else:
                cap = cv2.VideoCapture(0)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)

            own_cap = (_face_recognizer is None or not hasattr(_face_recognizer, "_cap"))

            while True:
                if cap is None or not cap.isOpened():
                    break
                ret, frame = cap.read()
                if not ret:
                    break
                frame_small = cv2.resize(frame, (320, 240))
                _, jpeg = cv2.imencode(".jpg", frame_small, [cv2.IMWRITE_JPEG_QUALITY, 70])
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"
                )
                time.sleep(0.1)  # ~10 FPS

        finally:
            if cap is not None and own_cap:
                cap.release()

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/face_log/{filename}", tags=["AI Features"],
         summary="Serve ảnh log nhận diện")
async def face_log_image(filename: str):
    log_dir = GATEWAY_DIR / "logs" / "face_events"
    img_path = log_dir / filename
    if not img_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return StreamingResponse(
        open(img_path, "rb"),
        media_type="image/jpeg",
    )


@app.websocket("/ws/sensors")
async def websocket_sensors(websocket: WebSocket):
    """
    WebSocket endpoint: push dữ liệu cảm biến real-time về client.
    Client nhận JSON: {"type": "sensors", "data": {...}}
    """
    await ws_manager.connect(websocket)
    try:
        # Gửi snapshot ngay khi kết nối
        await websocket.send_json({"type": "sensors", "data": _get_sensor_data()})
        # Giữ kết nối, đợi client ping
        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# ─────────────────────────── Injection API ──────────────────────────────────
def inject_modules(sensor_reader=None, voice_assistant=None, face_recognizer=None):
    """
    Được gọi từ main.py để inject các module runtime.
    Ví dụ: web_app.app.inject_modules(sensor_reader=sr, voice_assistant=va)
    """
    global _sensor_reader, _voice_assistant, _face_recognizer
    _sensor_reader = sensor_reader
    _voice_assistant = voice_assistant
    _face_recognizer = face_recognizer
    logger.info("[WebApp] Modules injected successfully")


# ─────────────────────────── Standalone Entry ───────────────────────────────
if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
