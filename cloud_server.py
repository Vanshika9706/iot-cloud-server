from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from datetime import datetime, timezone
from typing import Any
import math
import time
import html

app = FastAPI(title="Independent Edge Trust Gatekeeper")

# -------------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------------
HISTORY_SIZE = 10
RHO = 0.9
A_ADJ = 3.0
TRUST_THRESHOLD = 0.4
ANGLE_LIMIT = 90.0
MAX_LOGS = 500

# Cloud maintains its own trust state. It does NOT receive ESP32 scores.
edge_trust = {
    f"edge{i}": {"T": 0.5, "count": 0, "history": []}
    for i in range(1, 7)
}

# In-memory only. These records reset if the Render service restarts.
data_logs: list[dict[str, Any]] = []
best_edge_record: dict[str, Any] | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def cloud_time_window() -> int:
    """Independent wall-clock time divided into 5-second windows."""
    return int(time.time() // 5)


def compute_direct_trust(node: dict[str, Any]) -> float:
    """Time-decayed score over the cloud's own last HISTORY_SIZE outcomes."""
    history = node["history"]
    if not history:
        return 0.5

    now_window = cloud_time_window()
    numerator = 0.0
    denominator = 0.0

    for observation in history:
        delta_t = max(0, now_window - observation["window"])
        weight = RHO ** delta_t
        numerator += observation["success"] * weight
        denominator += weight

    return numerator / denominator if denominator else 0.5


def update_cloud_trust(edge_id: str, success: bool) -> float:
    """
    Same adaptive update equation used in the supplied ESP32 code.
    The cloud's observations and interaction counter remain independent.
    """
    node = edge_trust[edge_id]
    S = 1.0 if success else 0.0
    T_old = node["T"]

    ratio = 0.0 if T_old == 0 else S / T_old
    if ratio >= 0.5:
        theta = 1.0 - math.sqrt(abs((ratio - 0.5) / (ratio + 1.0)))
    else:
        theta = 1.0

    node["count"] += 1
    n = node["count"]

    if S >= T_old:
        delta = (
            (1.0 - math.exp(-n / (2.0 * A_ADJ)))
            * T_old * (1.0 - S) * abs(S - T_old)
        )
    else:
        delta = (
            (1.0 - math.exp(-n / A_ADJ))
            * S * (1.0 - S) * abs(S - T_old)
        )

    T_new = theta * S + (1.0 - theta) * T_old + delta
    node["T"] = max(0.0, min(1.0, T_new))

    node["history"].append({
        "success": int(S),
        "window": cloud_time_window(),
    })
    if len(node["history"]) > HISTORY_SIZE:
                f"pitch={data.get('smoothed_pitch', data.get('pitch'))} | "
        f"roll={data.get('smoothed_roll', data.get('roll'))} | "
        f"anomaly={data.get('anomaly')} | reason={data.get('reason')}"
    )

    return {
        "status": "logged",
        "edge_id": edge_id,
        "cloud_trust": round(current_trust, 3),
        "cloud_direct_trust": round(direct_trust, 3),
        "best_edge": best_edge_record.get("server") if best_edge_record else None,
    }


@app.get("/trust")
async def get_trust():
    """Get the cloud's independent trust scores for all six edges."""
    result = {}
    for edge_id, node in edge_trust.items():
        result[edge_id] = {
            "trust": round(node["T"], 3),
            "direct_trust": round(compute_direct_trust(node), 3),
            "interactions": node["count"],
        }
    return {"cloud_edge_trust": result}


@app.get("/best")
async def get_best_record():
    """Retrieve the accepted telemetry record with the highest cloud trust."""
    if best_edge_record is None:
        raise HTTPException(status_code=404, detail="No accepted edge record yet")
    return {"best_record": best_edge_record}


@app.get("/records")
async def get_records(limit: int = 50):
    """Retrieve recent accepted telemetry records as JSON."""
    limit = max(1, min(limit, MAX_LOGS))
    return {
        "count": min(len(data_logs), limit),
        "records": data_logs[:limit],
    }


@app.get("/logs", response_class=HTMLResponse)
async def view_logs():
    rows = []
    for item in data_logs:
        anomaly = bool(item.get("anomaly", False))
        anomaly_style = "color: red; font-weight: bold;" if anomaly else "color: green;"
        pitch = item.get("smoothed_pitch", item.get("pitch", "N/A"))
        roll = item.get("smoothed_roll", item.get("roll", "N/A"))
        server = html.escape(str(item.get("server", "")))
        log_time = html.escape(str(item.get("log_time", "")))
        trust = html.escape(str(item.get("cloud_trust", "")))
        direct = html.escape(str(item.get("cloud_direct_trust", "")))
        encoder_pos = html.escape(str(item.get("encoder_pos", "N/A")))
        encoder_angle = html.escape(str(item.get("encoder_angle_deg", "N/A")))
        vibration = "YES" if item.get("vibration") else "NO"
        reason = html.escape(str(item.get("reason", item.get("cloud_validation", ""))))
        rows.append(
            f"<tr><td>{log_time}</td><td><b>{server}</b></td>"
            f"<td>{trust}</td><td>{direct}</td>"
            f"<td>{html.escape(str(pitch))}° / {html.escape(str(roll))}°</td>"
            f"<td>{encoder_pos} ({encoder_angle}°)</td><td>{vibration}</td>"
            f"<td style='{anomaly_style}'>{reason}</td></tr>"
        )

    if best_edge_record:
        best_text = (
            f"{best_edge_record.get('server')} "
            f"(trust {best_edge_record.get('cloud_trust')})"
        )
    else:
        best_text = "No accepted report yet"

    rows_html = "".join(rows) or (
        "<tr><td colspan='8'>No accepted reports received yet.</td></tr>"
    )
    safe_best = html.escape(best_text)

    page = f"""<!DOCTYPE html>
<html>
<head>
    <title>Independent Cloud Trust Dashboard</title>
    <meta http-equiv="refresh" content="5">
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #f4f7f6; }}
        h2 {{ color: #333; }}
        .summary {{ padding: 12px; margin: 12px 0; background: white; border-radius: 8px; }}
        table {{ width: 100%; border-collapse: collapse; background: white; }}
        th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background: #007bff; color: white; }}
        tr:hover {{ background: #f1f1f1; }}
    </style>
</head>
<body>
    <h2>Cloud Gatekeeper: Independent Edge Trust</h2>
    <div class="summary">
        <b>Best accepted edge record:</b> {safe_best}<br>
        <b>Accepted records currently in memory:</b> {len(data_logs)}<br>
        Auto-refreshes every 5 seconds.
    </div>
    <table>
        <tr>
            <th>Time (UTC)</th><th>Edge</th><th>Cloud Trust</th><th>Direct Trust</th>
            <th>Pitch / Roll</th><th>Encoder</th><th>Vibration</th><th>Status / Reason</th>
        </tr>
        {rows_html}
    </table>
</body>
</html>"""
    return HTMLResponse(content=page)
