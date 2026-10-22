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
