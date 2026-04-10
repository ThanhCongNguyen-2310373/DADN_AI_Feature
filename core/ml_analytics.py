"""
core/ml_analytics.py - Energy forecasting + anomaly detection (Phase 5)
"""

import math
import time
import logging
from typing import Any, Dict, List

import numpy as np

from core.database import DatabaseSingleton

logger = logging.getLogger(__name__)


class MLEnergyAnalytics:
    """Lightweight ML analytics dùng dữ liệu lịch sử trong DB."""

    def __init__(self):
        self._db = DatabaseSingleton.get_instance()

    def _hourly_energy_series(self, history_hours: int = 48) -> List[float]:
        # Dựa trên tích lũy est_kwh của get_energy_report để suy ra từng giờ
        cumulative = []
        for h in range(1, history_hours + 1):
            report = self._db.get_energy_report(hours=h)
            total_kwh = 0.0
            for dev in ("led", "fan", "pump", "door"):
                total_kwh += float(report.get(dev, {}).get("est_kwh", 0.0))
            cumulative.append(total_kwh)

        # C(h)-C(h-1) => năng lượng của giờ thứ h tính từ hiện tại lùi về
        hourly_recent_to_old = []
        prev = 0.0
        for c in cumulative:
            hourly_recent_to_old.append(max(0.0, c - prev))
            prev = c

        # Đảo để có thứ tự cũ -> mới
        return list(reversed(hourly_recent_to_old))

    def forecast_energy(self, history_hours: int = 48, horizon_hours: int = 6) -> Dict[str, Any]:
        series = self._hourly_energy_series(history_hours=history_hours)
        if len(series) < 4:
            return {
                "success": False,
                "error": "Không đủ dữ liệu để dự báo.",
                "history_points": len(series),
            }

        x = np.arange(len(series), dtype=float)
        y = np.array(series, dtype=float)

        # Linear trend baseline
        slope, intercept = np.polyfit(x, y, deg=1)
        next_x = np.arange(len(series), len(series) + horizon_hours, dtype=float)
        preds = intercept + slope * next_x
        preds = np.maximum(preds, 0.0)

        # 95% CI xấp xỉ theo residual std
        residuals = y - (intercept + slope * x)
        sigma = float(np.std(residuals))

        prediction = []
        for i, val in enumerate(preds.tolist(), start=1):
            prediction.append({
                "hour_ahead": i,
                "pred_kwh": round(float(val), 4),
                "lower": round(max(0.0, float(val - 1.96 * sigma)), 4),
                "upper": round(float(val + 1.96 * sigma), 4),
            })

        return {
            "success": True,
            "history_hours": history_hours,
            "horizon_hours": horizon_hours,
            "series": [round(float(v), 4) for v in series],
            "trend": {
                "slope": round(float(slope), 6),
                "intercept": round(float(intercept), 6),
            },
            "prediction": prediction,
            "generated_ts": time.time(),
        }

    def detect_anomalies(self, hours: int = 24, z_threshold: float = 3.0) -> Dict[str, Any]:
        rows = self._db.get_sensor_history(hours=hours, limit=2000)
        if len(rows) < 10:
            return {
                "success": False,
                "error": "Không đủ dữ liệu cảm biến để phát hiện bất thường.",
                "count": len(rows),
            }

        def _zscores(values: np.ndarray):
            mean = np.mean(values)
            std = np.std(values)
            if std == 0:
                std = 1e-6
            return (values - mean) / std, float(mean), float(std)

        temp = np.array([float(r.get("temp") or 0.0) for r in rows], dtype=float)
        humi = np.array([float(r.get("humi") or 0.0) for r in rows], dtype=float)
        gas = np.array([float(r.get("gas") or 0.0) for r in rows], dtype=float)

        z_temp, m_temp, s_temp = _zscores(temp)
        z_humi, m_humi, s_humi = _zscores(humi)
        z_gas, m_gas, s_gas = _zscores(gas)

        anomalies = []
        for idx, row in enumerate(rows):
            flag_fields = []
            if abs(z_temp[idx]) >= z_threshold:
                flag_fields.append("temp")
            if abs(z_humi[idx]) >= z_threshold:
                flag_fields.append("humi")
            if abs(z_gas[idx]) >= z_threshold:
                flag_fields.append("gas")
            if flag_fields:
                anomalies.append({
                    "ts": row.get("ts"),
                    "created": row.get("created"),
                    "temp": row.get("temp"),
                    "humi": row.get("humi"),
                    "gas": row.get("gas"),
                    "fields": flag_fields,
                    "z_temp": round(float(z_temp[idx]), 3),
                    "z_humi": round(float(z_humi[idx]), 3),
                    "z_gas": round(float(z_gas[idx]), 3),
                })

        # Energy anomaly tại giờ hiện tại
        hourly_series = self._hourly_energy_series(history_hours=min(48, max(8, hours)))
        energy_anomaly = None
        if len(hourly_series) >= 8:
            arr = np.array(hourly_series, dtype=float)
            mean = float(np.mean(arr[:-1]))
            std = float(np.std(arr[:-1])) or 1e-6
            z_last = (arr[-1] - mean) / std
            energy_anomaly = {
                "last_hour_kwh": round(float(arr[-1]), 4),
                "mean_prev_kwh": round(mean, 4),
                "z_score": round(float(z_last), 3),
                "is_anomaly": bool(abs(z_last) >= z_threshold),
            }

        return {
            "success": True,
            "hours": hours,
            "z_threshold": z_threshold,
            "stats": {
                "temp": {"mean": round(m_temp, 3), "std": round(s_temp, 3)},
                "humi": {"mean": round(m_humi, 3), "std": round(s_humi, 3)},
                "gas": {"mean": round(m_gas, 3), "std": round(s_gas, 3)},
            },
            "anomaly_count": len(anomalies),
            "anomalies": anomalies[-50:],
            "energy_anomaly": energy_anomaly,
            "generated_ts": time.time(),
        }
