/**
 * YoloHome Dashboard – dashboard.js
 * Real-time cập nhật sensor, chat, face log qua WebSocket + REST polling
 */

// ── Constants ──
const SENSOR_THRESHOLDS = {
    temp: { warn: 32, danger: 35, max: 60 },
    humi: { warn: 80, danger: 90, max: 100 },
    gas: { warn: 200, danger: 300, max: 500 },
};

const POLL_CHAT_MS = 4000; // poll chat history mỗi 4 giây
const POLL_FACELOG_MS = 8000; // poll face log mỗi 8 giây
const WS_RECONNECT_MS = 5000; // thử reconnect WebSocket sau 5 giây

let ws = null;

// ── DOM helpers ──
const el = (id) => document.getElementById(id);

// ── Clock ──
function updateClock() {
    const now = new Date();
    el("clock").textContent = now.toLocaleTimeString("vi-VN");
}
setInterval(updateClock, 1000);
updateClock();

// ── WebSocket ──
function connectWebSocket() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/sensors`);

    ws.onopen = () => {
        console.log("[WS] Connected");
        setBadge("online");
        // Heartbeat ping
        setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) ws.send("ping");
        }, 15000);
    };

    ws.onmessage = (event) => {
        console.log("💌 [WS] Có tin nhắn gốc từ Server:", event.data);
        try {
            const msg = JSON.parse(event.data);
            if (msg.type === "sensors") updateSensors(msg.data);
        } catch (e) {
            /* ignore non-JSON */
        }
    };

    ws.onerror = () => {
        console.warn("[WS] Error – falling back to REST polling");
        setBadge("offline");
    };

    ws.onclose = () => {
        setBadge("offline");
        console.log(`[WS] Closed – reconnecting in ${WS_RECONNECT_MS}ms`);
        setTimeout(connectWebSocket, WS_RECONNECT_MS);
    };
}

// ── Badge ──
function setBadge(state) {
    const badge = el("conn-badge");
    badge.className = `badge badge-${state}`;
    const labels = {
        connecting: "Đang kết nối…",
        online: "Trực tuyến",
        offline: "Mất kết nối",
    };
    badge.innerHTML = `<i class="fas fa-circle"></i> ${labels[state] || state}`;
}

// ── Sensor update ──
function updateSensors(data) {
    if (data.temperature !== undefined)
        setSensor("temp", data.temperature, SENSOR_THRESHOLDS.temp);
    if (data.humidity !== undefined)
        setSensor("humi", data.humidity, SENSOR_THRESHOLDS.humi);
    if (data.gas !== undefined)
        setSensor("gas", data.gas, SENSOR_THRESHOLDS.gas);

    console.log("👉 Dữ liệu server báo về:", data);
    // Sync toggle states (chỉ cập nhật nếu người dùng không đang tương tác)
    syncToggle("led", data.led);
    syncToggle("fan", data.fan);
    syncToggle("door", data.door);
    syncToggle("pump", data.pump);
}

function setSensor(key, value, thr) {
    const valEl = el(`val-${key}`);
    const barEl = el(`bar-${key}`);
    const card = el(`card-${key}`);

    const numVal = parseFloat(value);
    valEl.textContent = isNaN(numVal) ? value : numVal.toFixed(1);

    // Progress bar (0–100%)
    const pct = Math.min(100, Math.max(0, (numVal / thr.max) * 100));
    barEl.style.width = pct + "%";

    // State class
    card.classList.remove("state-ok", "state-warn", "state-danger");
    if (numVal >= thr.danger) card.classList.add("state-danger");
    else if (numVal >= thr.warn) card.classList.add("state-warn");
    else card.classList.add("state-ok");
}

function syncToggle(device, value) {
    const sw = el(`sw-${device}`);
    const st = el(`st-${device}`);
    if (!sw) return;
    const isOn = value == 1 || value === true || value === "ON";
    sw.checked = isOn;
    if (device === "door") {
        st.textContent = isOn ? "Mở" : "Đóng";
    } else {
        st.textContent = isOn ? "Bật" : "Tắt";
    }
}

// ── Control ──
async function sendControl(device, value) {
    try {
        const resp = await fetch("/api/control", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ device, value }),
        });
        const data = await resp.json();
        if (!resp.ok) console.error("[Control]", data.detail);
    } catch (e) {
        console.error("[Control] Fetch error:", e);
    }
}

// ── Chat polling ──
let _lastChatLen = 0;

async function pollChat() {
    try {
        const resp = await fetch("/api/chat");
        const data = await resp.json();
        const history = data.history || [];
        if (history.length !== _lastChatLen) {
            _lastChatLen = history.length;
            renderChat(history);
        }
    } catch (e) {
        /* silent */
    }
}

function renderChat(history) {
    const container = el("chat-messages");
    // Giữ scroll nếu đang ở cuối
    const atBottom =
        container.scrollHeight - container.scrollTop - container.clientHeight <
        30;

    container.innerHTML = history
        .map((item) => {
            const isUser = item.role === "user";
            return `
      <div class="msg ${isUser ? "msg-user" : "msg-bot"}">
        ${escapeHtml(item.text || item.content || "")}
        <div class="msg-time">${item.time || ""}</div>
      </div>`;
        })
        .join("");

    if (atBottom) container.scrollTop = container.scrollHeight;
}

// ── Face Log polling ──
let _lastFaceLogLen = 0;

async function pollFaceLog() {
    try {
        const resp = await fetch("/api/face/log");
        const data = await resp.json();
        const events = data.events || [];
        if (events.length !== _lastFaceLogLen) {
            _lastFaceLogLen = events.length;
            renderFaceLog(events);
        }
    } catch (e) {
        /* silent */
    }
}

function renderFaceLog(events) {
    const list = el("face-log-list");
    if (events.length === 0) {
        list.innerHTML =
            '<p style="color:var(--text-dim);font-size:.8rem">Chưa có sự kiện nhận diện.</p>';
        return;
    }
    list.innerHTML = events
        .map(
            (ev) => `
    <div class="log-item" title="${ev.time}">
      <img src="${ev.url}" alt="face log" loading="lazy"
           onerror="this.src='/static/img/no_camera.svg'" />
      <div class="log-item-time">${ev.time}</div>
    </div>`,
        )
        .join("");
}

// ── REST fallback polling (khi WS không khả dụng) ──
async function pollSensors() {
    if (ws && ws.readyState === WebSocket.OPEN) return; // WS đang hoạt động
    try {
        const resp = await fetch("/api/sensors");
        const data = await resp.json();
        updateSensors(data);
    } catch (e) {
        /* silent */
    }
}

// ── Utils ──
function escapeHtml(str) {
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

// ── Sensor History Chart (Chart.js) ──
let _sensorChart = null;

async function initSensorChart() {
    const canvas = el("chart-sensor");
    if (!canvas) return;
    try {
        const resp = await fetch("/api/history?hours=24");
        if (!resp.ok) return;
        const data = await resp.json();
        const rows = data.data || [];
        if (rows.length === 0) return;

        const labels = rows.map((r) => {
            const d = new Date(r.ts * 1000);
            return d.toLocaleTimeString("vi-VN", {
                hour: "2-digit",
                minute: "2-digit",
            });
        });
        const temps = rows.map((r) => r.temp);
        const humis = rows.map((r) => r.humi);

        const ctx = canvas.getContext("2d");
        if (_sensorChart) _sensorChart.destroy();
        _sensorChart = new Chart(ctx, {
            type: "line",
            data: {
                labels,
                datasets: [
                    {
                        label: "Nhiệt độ (°C)",
                        data: temps,
                        borderColor: "#f0883e",
                        backgroundColor: "rgba(240,136,62,.08)",
                        borderWidth: 2,
                        tension: 0.35,
                        fill: true,
                        pointRadius: 0,
                    },
                    {
                        label: "Độ ẩm (%)",
                        data: humis,
                        borderColor: "#58a6ff",
                        backgroundColor: "rgba(88,166,255,.08)",
                        borderWidth: 2,
                        tension: 0.35,
                        fill: true,
                        pointRadius: 0,
                    },
                ],
            },
            options: {
                responsive: true,
                animation: false,
                plugins: {
                    legend: {
                        labels: {
                            color: "#c9d1d9",
                            boxWidth: 14,
                            font: { size: 12 },
                        },
                    },
                },
                scales: {
                    x: {
                        ticks: {
                            color: "#8b949e",
                            maxTicksLimit: 10,
                            maxRotation: 0,
                        },
                        grid: { color: "#21262d" },
                    },
                    y: {
                        ticks: { color: "#8b949e" },
                        grid: { color: "#21262d" },
                    },
                },
            },
        });
    } catch (e) {
        console.warn("[Chart] initSensorChart error:", e);
    }
}

// Refresh chart every 5 minutes
setInterval(initSensorChart, 5 * 60 * 1000);

// ── Energy Report ──
const ENERGY_ICONS = {
    led: "lightbulb",
    fan: "fan",
    pump: "tint",
    door: "door-open",
};
const ENERGY_NAMES = {
    led: "Đèn LED",
    fan: "Quạt",
    pump: "Máy bơm",
    door: "Cửa",
};

async function pollEnergy() {
    const container = el("energy-report");
    if (!container) return;
    try {
        const resp = await fetch("/api/energy?hours=24");
        if (!resp.ok) return;
        const data = await resp.json();

        container.innerHTML = ["led", "fan", "pump", "door"]
            .map((d) => {
                const info = data[d] || {};
                const hours = (info.on_hours || 0).toFixed(2);
                const kwh = (info.est_kwh || 0).toFixed(4);
                return `
        <div class="energy-item">
          <i class="fas fa-${ENERGY_ICONS[d]}" style="color:var(--accent)"></i>
          <div class="e-label">${ENERGY_NAMES[d]}</div>
          <div class="e-hours">${hours}<small>h</small></div>
          <div class="e-kwh">${kwh} kWh</div>
        </div>`;
            })
            .join("");
    } catch (e) {
        console.warn("[Energy] pollEnergy error:", e);
    }
}

// ── Init ──
setBadge("connecting");
connectWebSocket();

setInterval(pollSensors, 10000);
setInterval(pollChat, POLL_CHAT_MS);
setInterval(pollFaceLog, POLL_FACELOG_MS);
setInterval(pollEnergy, 30_000);

// Khởi tạo ngay
pollChat();
pollFaceLog();
initSensorChart();
pollEnergy();
