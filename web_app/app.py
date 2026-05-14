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


class FaceEnrollSession:
    def __init__(self):
        self._lock = threading.Lock()
        self._status = {
            "status": "idle",
            "captured": 0,
            "remaining": 0,
            "stage_text": "",
            "stage_index": 0,
            "quality_ok": False,
            "reasons": [],
            "blur": 0.0,
            "brightness": 0.0,
            "auto_mode": True,
            "paused": False,
            "person_name": "",
            "num_samples": 0,
            "message": "",
            "error": "",
        }
        self.pause_event = threading.Event()
        self.cancel_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self, person_name: str, num_samples: int, thread: threading.Thread):
        with self._lock:
            self._status.update({
                "status": "running",
                "captured": 0,
                "remaining": num_samples,
                "person_name": person_name,
                "num_samples": num_samples,
                "message": "Đang khởi tạo phiên chụp...",
                "error": "",
            })
            self.pause_event.clear()
            self.cancel_event.clear()
            self.thread = thread

    def update(self, data: Dict[str, Any]):
        with self._lock:
            self._status.update(data)

    def set_message(self, message: str):
        with self._lock:
            self._status["message"] = message

    def set_error(self, message: str):
        with self._lock:
            self._status["status"] = "error"
            self._status["error"] = message
            self._status["message"] = message

    def mark_completed(self):
        with self._lock:
            self._status["status"] = "completed"
            self._status["message"] = "Hoàn tất chụp mẫu."
            self._status["remaining"] = 0

    def snapshot(self):
        with self._lock:
            data = dict(self._status)
            data["paused"] = self.pause_event.is_set()
            data["cancelled"] = self.cancel_event.is_set()
            return data

    def is_active(self) -> bool:
        status = self.snapshot().get("status", "idle")
        return status in {"running", "paused"}


class FaceTrainSession:
    def __init__(self):
        self._lock = threading.Lock()
        self._status = {
            "status": "idle",
            "stage": "idle",
            "message": "Chưa bắt đầu train.",
            "error": "",
            "progress": 0,
            "current_person": "",
            "processed_people": 0,
            "total_people": 0,
            "trained_images": 0,
            "trained_people": [],
            "accuracy": 0.0,
            "model_path": "",
            "label_map_path": "",
            "report_path": "",
            "selected_members": [],
            "mode": "all",
            "started_at": None,
            "finished_at": None,
        }
        self.thread: threading.Thread | None = None

    def start(self, thread: threading.Thread, mode: str, selected_members: List[str]):
        with self._lock:
            self._status.update({
                "status": "running",
                "stage": "starting",
                "message": "Đang chuẩn bị train model...",
                "error": "",
                "progress": 0,
                "current_person": "",
                "processed_people": 0,
                "total_people": 0,
                "trained_images": 0,
                "trained_people": [],
                "accuracy": 0.0,
                "model_path": "",
                "label_map_path": "",
                "report_path": "",
                "selected_members": list(selected_members),
                "mode": mode,
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "finished_at": None,
            })
            self.thread = thread

    def update(self, data: Dict[str, Any]):
        with self._lock:
            self._status.update(data)

    def set_error(self, message: str):
        with self._lock:
            self._status["status"] = "error"
            self._status["error"] = message
            self._status["message"] = message
            self._status["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    def mark_completed(self):
        with self._lock:
            self._status["status"] = "completed"
            if not self._status.get("message"):
                self._status["message"] = "Train hoàn tất."
            self._status["progress"] = 100
            self._status["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    def snapshot(self):
        with self._lock:
            return dict(self._status)


_face_enroll_session = FaceEnrollSession()
_face_train_session = FaceTrainSession()


def _draw_face_enroll_overlay(frame):
    info = _face_enroll_session.snapshot()
    status = info.get("status", "idle")
    person = info.get("person_name", "")
    captured = int(info.get("captured", 0) or 0)
    remaining = int(info.get("remaining", 0) or 0)
    stage_text = str(info.get("stage_text", ""))
    quality_ok = bool(info.get("quality_ok", False))
    reasons = info.get("reasons", []) or []
    message = info.get("message", "") or ""

    overlay_lines = [
        f"Trang thai: {status}",
        f"Thanh vien: {person}" if person else "Thanh vien: -",
        f"Da chup: {captured} | Con lai: {remaining}",
        f"Buoc: {stage_text}" if stage_text else "Buoc: -",
        f"Chat luong: {'DAT' if quality_ok else 'CHUA DAT'}",
    ]
    if reasons:
        overlay_lines.append("Ly do: " + ", ".join(reasons[:2]))
    if message:
        overlay_lines.append(message)

    overlay = frame.copy()
    cv2.rectangle(overlay, (8, 8), (420, 170), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.45, frame, 0.55, 0, frame)

    y = 30
    for line in overlay_lines:
        cv2.putText(frame, line, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1, cv2.LINE_AA)
        y += 22

    if status in {"running", "paused"}:
        bar_w = 360
        ratio = 0.0
        total = int(info.get("num_samples", 0) or 0)
        if total > 0:
            ratio = max(0.0, min(1.0, captured / total))
        cv2.rectangle(frame, (20, 145), (20 + bar_w, 160), (80, 80, 80), 1)
        cv2.rectangle(frame, (20, 145), (20 + int(bar_w * ratio), 160), (0, 200, 255), -1)
        cv2.putText(frame, f"{int(ratio * 100)}%", (388, 158), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)

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


def _is_api_request(request: Request) -> bool:
    if request.url.path.startswith("/api"):
        return True
    accept = request.headers.get("accept", "").lower()
    return "application/json" in accept


def require_auth(request: Request) -> Dict[str, Any]:
    """Dependency: bắt buộc đăng nhập, trả về user session hiện tại."""
    user = _get_current_user(request)
    if not user:
        if _is_api_request(request):
            raise HTTPException(status_code=401, detail="Chưa đăng nhập")
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
        80, ge=80, le=200,
        description="Số ảnh mẫu cần chụp (80–200, mặc định 80)"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{"person_name": "Nguyen Van A", "num_samples": 80}]
        }
    }


class TrainRequest(BaseModel):
    mode: Literal["all", "selected"] = Field(
        "all",
        description="Train toàn bộ dataset hoặc chỉ các thành viên được chọn",
    )
    selected_members: List[str] = Field(
        default_factory=list,
        description="Danh sách thành viên được chọn khi mode=selected",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"mode": "all", "selected_members": []},
                {"mode": "selected", "selected_members": ["Cong", "Bame"]},
            ]
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
    try:
        from core.database import DatabaseSingleton
        db = DatabaseSingleton.get_instance()
        rows = db.get_face_events(hours=24, limit=10)
    except Exception as e:
        logger.debug(f"[WebApp] face log db error: {e}")
        rows = []

    entries = []
    for row in rows:
        img_path = row.get("img_path") or ""
        url = None
        if img_path:
            try:
                abs_img = Path(img_path)
                if not abs_img.is_absolute():
                    abs_img = (GATEWAY_DIR / img_path).resolve()
                if abs_img.exists():
                    url = f"/face_log/{abs_img.name}"
            except Exception:
                url = None
        entries.append({
            "event_type": row.get("event_type", ""),
            "person": row.get("person") or "",
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(row.get("ts", 0) or 0))),
            "url": url,
            "filename": os.path.basename(img_path) if img_path else "",
        })
    return entries


def _get_face_status() -> Dict[str, Any]:
    if _face_recognizer is not None and hasattr(_face_recognizer, "get_status"):
        try:
            return _face_recognizer.get_status()
        except Exception as e:
            logger.debug(f"[WebApp] face status error: {e}")
    return {
        "state": "idle",
        "message": "Chờ nhận diện...",
        "person_name": "",
        "event_type": "none",
        "timestamp": None,
    }


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
          description="API hỏi đáp không cần microphone; dùng chung logic NLP với Voice Assistant.")
async def voice_ask(req: VoiceAskRequest, _=Depends(require_auth)):
    if _voice_assistant is None:
        return JSONResponse({"answer": "Voice Assistant chưa được khởi động."})
    try:
        text = req.question.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Question rỗng")

        if hasattr(_voice_assistant, "handle_user_text"):
            ans = _voice_assistant.handle_user_text(text, speak=False)
        else:
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


@app.get("/api/face/status", tags=["AI Features"],
         summary="Trạng thái nhận diện khuôn mặt",
         description="Trả về trạng thái nhận diện mới nhất: người đã đăng ký, người lạ hoặc đang chờ.")
async def get_face_status(_=Depends(require_auth)):
    return JSONResponse(_get_face_status())


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

    if _face_enroll_session.thread and _face_enroll_session.thread.is_alive():
        raise HTTPException(status_code=409, detail="Đang có một phiên chụp khác đang chạy.")

    def _do_enroll():
        try:
            sys.path.insert(0, str(GATEWAY_DIR))
            from ai.face_recognition.face_register import capture_face_samples_from_provider

            if _face_recognizer is None or not hasattr(_face_recognizer, "get_latest_frame"):
                raise RuntimeError("FaceRecognizer chưa sẵn sàng để cung cấp frame.")

            _face_enroll_session.set_message("Đang chụp mẫu theo hướng dẫn trên camera...")
            ok = capture_face_samples_from_provider(
                name,
                req.num_samples,
                frame_provider=_face_recognizer.get_latest_frame,
                pause_event=_face_enroll_session.pause_event,
                cancel_event=_face_enroll_session.cancel_event,
                progress_callback=_face_enroll_session.update,
            )
            if ok:
                _face_enroll_session.mark_completed()
                logger.info(f"[Enroll] ✅ Đã thu thập {req.num_samples} ảnh cho '{name}'")
            elif _face_enroll_session.cancel_event.is_set():
                _face_enroll_session.update({"status": "cancelled", "message": "Phiên chụp đã bị huỷ."})
            else:
                _face_enroll_session.set_error("Thu thập chưa đủ ảnh yêu cầu.")
                logger.warning(f"[Enroll] ⚠️ Thu thập chưa đủ ảnh cho '{name}'")
        except Exception as e:
            _face_enroll_session.set_error(str(e))
            logger.error(f"[Enroll] Lỗi: {e}")

    thread = threading.Thread(target=_do_enroll, daemon=True, name="FaceEnroll")
    _face_enroll_session.start(name, req.num_samples, thread)
    thread.start()
    return {"status": "started", "person": name, "samples": req.num_samples}


@app.get("/api/face/enroll/status", tags=["AI Features"],
         summary="Trạng thái phiên chụp khuôn mặt")
async def face_enroll_status(_=Depends(require_auth)):
    return JSONResponse(_face_enroll_session.snapshot())


@app.get("/api/face/train/status", tags=["AI Features"],
         summary="Trạng thái train khuôn mặt")
async def face_train_status(_=Depends(require_auth)):
    return JSONResponse(_face_train_session.snapshot())


@app.post("/api/face/enroll/pause", tags=["AI Features"],
          summary="Tạm dừng phiên chụp khuôn mặt")
async def face_enroll_pause(_=Depends(require_operator)):
    _face_enroll_session.pause_event.set()
    _face_enroll_session.update({"status": "paused", "message": "Đã tạm dừng phiên chụp."})
    return JSONResponse(_face_enroll_session.snapshot())


@app.post("/api/face/enroll/resume", tags=["AI Features"],
          summary="Tiếp tục phiên chụp khuôn mặt")
async def face_enroll_resume(_=Depends(require_operator)):
    _face_enroll_session.pause_event.clear()
    _face_enroll_session.update({"status": "running", "message": "Đang tiếp tục chụp mẫu..."})
    return JSONResponse(_face_enroll_session.snapshot())


@app.post("/api/face/enroll/cancel", tags=["AI Features"],
          summary="Huỷ phiên chụp khuôn mặt")
async def face_enroll_cancel(_=Depends(require_operator)):
    _face_enroll_session.cancel_event.set()
    _face_enroll_session.update({"status": "cancelled", "message": "Đã huỷ phiên chụp."})
    return JSONResponse(_face_enroll_session.snapshot())


@app.post("/api/face/train", tags=["AI Features"],
          summary="Train lại LBPH model",
          description="Chạy lại quá trình huấn luyện LBPH từ toàn bộ dataset hiện tại. "
                      "Chạy trong background thread, trả về ngay. Mất khoảng 5–30 giây.")
async def face_train(req: TrainRequest, _=Depends(require_operator)):
    if _face_train_session.thread and _face_train_session.thread.is_alive():
        raise HTTPException(status_code=409, detail="Đang có một phiên train khác đang chạy.")

    selected_members = [name.strip() for name in req.selected_members if str(name).strip()]
    if req.mode == "selected" and not selected_members:
        raise HTTPException(status_code=400, detail="Hãy chọn ít nhất một thành viên để train.")

    def _do_train():
        try:
            sys.path.insert(0, str(GATEWAY_DIR))
            from ai.face_recognition.face_register import train_face_model
            _face_train_session.update({
                "status": "running",
                "stage": "starting",
                "message": "Đang quét dataset...",
            })
            ok = train_face_model(
                progress_callback=_face_train_session.update,
                include_members=selected_members if req.mode == "selected" else None,
            )
            if ok:
                _face_train_session.update({"message": "Huấn luyện xong. Đang nạp lại model...", "stage": "reloading", "progress": 100})
                reload_ok = True
                if _face_recognizer is not None and hasattr(_face_recognizer, "reload_model"):
                    try:
                        reload_ok = bool(_face_recognizer.reload_model())
                    except Exception as reload_err:
                        reload_ok = False
                        logger.warning(f"[Train] Không thể reload FaceAI: {reload_err}")
                if reload_ok:
                    _face_train_session.update({"message": "Huấn luyện hoàn tất và model đã được nạp lại.", "stage": "completed", "progress": 100})
                else:
                    _face_train_session.update({"message": "Huấn luyện hoàn tất nhưng reload model gặp lỗi.", "stage": "warning", "progress": 100})
                _face_train_session.mark_completed()
                logger.info("[Train] ✅ Model LBPH đã được train lại thành công.")
            else:
                _face_train_session.set_error("Huấn luyện thất bại.")
        except Exception as e:
            _face_train_session.set_error(str(e))
            logger.error(f"[Train] Lỗi train model: {e}")

    thread = threading.Thread(target=_do_train, daemon=True, name="FaceTrain")
    _face_train_session.start(thread, req.mode, selected_members)
    thread.start()
    return {"status": "training_started", "mode": req.mode, "selected_members": selected_members}


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
        preview_w = 640
        preview_h = int(preview_w * config.FACE_FRAME_HEIGHT / max(1, config.FACE_FRAME_WIDTH))
        # Lấy frame từ FaceRecognizer cache nếu có, không thì mở camera trực tiếp
        if _face_recognizer is not None and hasattr(_face_recognizer, "get_latest_frame"):
            while True:
                frame = _face_recognizer.get_latest_frame()
                if frame is None:
                    time.sleep(0.05)
                    continue
                _draw_face_enroll_overlay(frame)
                frame_small = cv2.resize(frame, (preview_w, preview_h))
                _, jpeg = cv2.imencode(".jpg", frame_small, [cv2.IMWRITE_JPEG_QUALITY, 70])
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"
                )
                time.sleep(0.1)
            return

        cap = cv2.VideoCapture(0)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.FACE_FRAME_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FACE_FRAME_HEIGHT)

        try:
            while True:
                if cap is None or not cap.isOpened():
                    break
                ret, frame = cap.read()
                if not ret:
                    break
                _draw_face_enroll_overlay(frame)
                frame_small = cv2.resize(frame, (preview_w, preview_h))
                _, jpeg = cv2.imencode(".jpg", frame_small, [cv2.IMWRITE_JPEG_QUALITY, 70])
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"
                )
                time.sleep(0.1)  # ~10 FPS
        finally:
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
