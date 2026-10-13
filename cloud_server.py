from fastapi import FastAPI, HTTPException
from datetime import datetime
import math

app = FastAPI()

# Cloud maintains its own independent trust for each edge.
cloud_edge_trust = {
    f"edge{i}": 0.5 for i in range(1, 7)
}

trusted_logs = []
rejected_logs = []


@app.get("/ping")
async def ping():
    return {"status": "ok"}


@app.post("/cloud/analyze")
async def cloud_analyze(payload: dict):
    source = payload.get("source_edge")
    result = payload.get("edge_result")

    if source not in cloud_edge_trust or not isinstance(result, dict):
        raise HTTPException(
            status_code=400,
            detail="Invalid source or result"
        )

    # Accept explicit outage reports from Edge 4.
    if result.get("available") is False:
        valid = False
    else:
        try:
            pitch = float(result["smoothed_pitch"])
            roll = float(result["smoothed_roll"])

            valid = (
                math.isfinite(pitch)
                and math.isfinite(roll)
                and -90 <= pitch <= 90
                and -90 <= roll <= 90
            )
        except (KeyError, TypeError, ValueError):
            valid = False

    # Independent cloud-side trust update.
    alpha = 0.2
    old_trust = cloud_edge_trust[source]
    success = 1.0 if valid else 0.0

    new_trust = alpha * success + (1 - alpha) * old_trust
    cloud_edge_trust[source] = new_trust

    record = {
        "source_edge": source,
        "cloud_trust": round(new_trust, 3),
        "received_at": datetime.utcnow().isoformat(),
        "edge_result": result,
    }

    if valid:
        trusted_logs.append(record)
    else:
        rejected_logs.append(record)

    return {
        "accepted": valid,
        "source_edge": source,
        "cloud_trust": round(new_trust, 3),
        "reason": "accepted" if valid else "invalid_result_or_edge_outage",
    }
