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
        node["history"].pop(0)

    return node["T"]


def normalise_edge_id(value: Any) -> str:
    """Accept edge1, Edge1, edge_1, edge-1, etc."""
    if not isinstance(value, str):
        return ""
    cleaned = value.strip().lower().replace("_", "").replace("-", "").replace(" ", "")
    if cleaned.startswith("edge") and cleaned[4:].isdigit():
        return "edge" + cleaned[4:]
    return ""


def numeric_angle(data: dict[str, Any], primary: str, alternate: str) -> float | None:
    value = data.get(primary, data.get(alternate))
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def validate_report(data: dict[str, Any]) -> tuple[bool, str]:
    """Validate fields before an accepted report can become the best record."""
    if data.get("corrupt", False) is True:
        return False, "corrupt_flag"

    pitch = numeric_angle(data, "smoothed_pitch", "pitch")
    roll = numeric_angle(data, "smoothed_roll", "roll")

    if pitch is None:
        return False, "missing_or_invalid_pitch"
    if roll is None:
        return False, "missing_or_invalid_roll"
    if abs(pitch) > ANGLE_LIMIT:
        return False, "pitch_out_of_range"
    if abs(roll) > ANGLE_LIMIT:
        return False, "roll_out_of_range"

    return True, "valid"


@app.get("/")
async def root():
    return {
        "service": "Independent Edge Trust Gatekeeper",
        "status": "online",
        "endpoints": ["/ping", "/trust", "/best", "/records", "/logs", "/docs"],
    }


@app.get("/ping")
async def ping():
    return {"status": "cloud_online"}


@app.post("/cloud/log")
@app.post("/cloud/analyze")  # Compatibility with the earlier edge forwarding code
async def log_data(request: Request):
    global best_edge_record

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON")

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="JSON body must be a JSON object")

    # Accept both payload shapes:
    # 1) Direct: {"server": "edge1", "smoothed_pitch": ..., "smoothed_roll": ...}
    # 2) Earlier forwarding helper: {"source_edge": "edge1", "edge_result": {...}}
    if isinstance(data.get("edge_result"), dict):
        edge_result = data["edge_result"]
        source = data.get("source_edge", data.get("server", ""))
        merged_data = dict(edge_result)
        # Keep any outer metadata not already provided by the result.
        for key, value in data.items():
            if key != "edge_result" and key not in merged_data:
                merged_data[key] = value
        data = merged_data
        data["server"] = source
    elif "server" not in data and "source_edge" in data:
        data["server"] = data["source_edge"]

    edge_id = normalise_edge_id(data.get("server", ""))
    if edge_id not in edge_trust:
        raise HTTPException(
            status_code=400,
            detail="Unknown edge server. Send server/source_edge='edge1' through 'edge6'.",
        )

    valid, reason = validate_report(data)

    # Update cloud trust from the cloud's own validation result.
    # This happens before the acceptance threshold is checked.
    current_trust = update_cloud_trust(edge_id, valid)
    direct_trust = compute_direct_trust(edge_trust[edge_id])
    timestamp = utc_now().isoformat()

    # Add cloud-side metadata to the received record.
    data["server"] = edge_id
    data["cloud_trust"] = round(current_trust, 3)
    data["cloud_direct_trust"] = round(direct_trust, 3)
    data["cloud_interactions"] = edge_trust[edge_id]["count"]
    data["log_time"] = timestamp
    data["cloud_validation"] = reason

    # Reject invalid reports. Their failed interaction still affects trust,
    # but the invalid telemetry itself is not added to accepted data_logs.
    if not valid:
        print(
            f"[CLOUD REJECTED] {timestamp} | {edge_id} | "
            f"reason={reason} | trust={current_trust:.3f}"
        )
        raise HTTPException(
            status_code=422,
            detail={
                "status": "rejected_invalid_data",
                "edge_id": edge_id,
                "reason": reason,
                "cloud_trust": round(current_trust, 3),
            },
        )

    if current_trust < TRUST_THRESHOLD:
        print(
            f"[CLOUD REJECTED] {timestamp} | {edge_id} | "
            f"trust={current_trust:.3f} below {TRUST_THRESHOLD}"
        )
        raise HTTPException(
            status_code=403,
            detail={
                "status": "rejected_low_trust",
                "edge_id": edge_id,
                "cloud_trust": round(current_trust, 3),
            },
        )

    # Store accepted telemetry in memory.
    accepted_record = dict(data)
    data_logs.insert(0, accepted_record)
    if len(data_logs) > MAX_LOGS:
        data_logs.pop()

    # Keep the accepted record associated with the highest cloud trust.
    # Equal scores leave the existing best record unchanged.
    if (
        best_edge_record is None
        or current_trust > float(best_edge_record.get("cloud_trust", 0.0))
    ):
        best_edge_record = dict(accepted_record)

    print(
        f"[CLOUD ACCEPTED] {timestamp} | {edge_id} | "
        f"trust={current_trust:.3f} | direct={direct_trust:.3f} | "
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
