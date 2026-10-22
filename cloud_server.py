    timestamp = datetime.now(timezone.utc).isoformat()
    data["server"] = edge_id
    data["cloud_trust"] = round(current_trust, 3)
    data["cloud_direct_trust"] = round(direct_trust, 3)
    data["cloud_interactions"] = edge_trust[edge_id]["count"]
    data["log_time"] = timestamp
    data["cloud_validation"] = reason

    # Reject invalid/corrupted data and low-trust sources.
    if not valid:
        print(f"REJECTED {edge_id}: {reason}; cloud trust={current_trust:.3f}")
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
        print(f"REJECTED {edge_id}: trust {current_trust:.3f} < {TRUST_THRESHOLD}")
        raise HTTPException(
            status_code=403,
            detail={
                "status": "rejected_low_trust",
                "edge_id": edge_id,
                "cloud_trust": round(current_trust, 3),
            },
        )

    # Store every accepted report in the in-memory history.
    accepted_record = dict(data)
    data_logs.insert(0, accepted_record)
    if len(data_logs) > MAX_LOGS:
        data_logs.pop()

    # Keep the best accepted record seen so far, using the cloud's score.
    # Ties keep the existing best record, so this does not churn on equal scores.
    if (
        best_edge_record is None
        or current_trust > float(best_edge_record.get("cloud_trust", 0.0))
    ):
        best_edge_record = dict(accepted_record)

    print(
        f"[CLOUD ACCEPTED] {timestamp} | {edge_id} | "
        f"T={current_trust:.3f} | direct={direct_trust:.3f} | "
        f"pitch={data.get('smoothed_pitch', data.get('pitch'))} | "
        f"roll={data.get('smoothed_roll', data.get('roll'))}"
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
    """Retrieve the cloud's independent trust table for all six edges."""
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
    """Retrieve the highest-cloud-trust accepted telemetry record."""
    if best_edge_record is None:
        raise HTTPException(status_code=404, detail="No accepted edge record yet")
    return {"best_record": best_edge_record}


@app.get("/records")
async def get_records(limit: int = 50):
    """Retrieve recent accepted records as JSON."""
    limit = max(1, min(limit, MAX_LOGS))
    return {"count": min(len(data_logs), limit), "records": data_logs[:limit]}


@app.get("/logs", response_class=HTMLResponse)
async def view_logs():
    rows = ""
    for item in data_logs:
        anomaly = bool(item.get("anomaly", False))
        anomaly_style = "color: red; font-weight: bold;" if anomaly else "color: green;"
        pitch = item.get("smoothed_pitch", item.get("pitch", "N/A"))
        roll = item.get("smoothed_roll", item.get("roll", "N/A"))
        rows += f"""
        <tr>
            <td>{item.get('log_time', '')}</td>
            <td><b>{item.get('server', '')}</b></td>
            <td>{item.get('cloud_trust', '')}</td>
            <td>{item.get('cloud_direct_trust', '')}</td>
            <td>{pitch}° / {roll}°</td>
            <td>{item.get('encoder_pos', 'N/A')} ({item.get('encoder_angle_deg', 'N/A')}°)</td>
            <td>{'YES' if item.get('vibration') else 'NO'}</td>
            <td style="{anomaly_style}">{item.get('reason', item.get('cloud_validation', ''))}</td>
        </tr>
        """

    best_text = (
        f"{best_edge_record.get('server')} (trust {best_edge_record.get('cloud_trust')})"
        if best_edge_record else "No accepted report yet"
    )
    html = f"""
    <!DOCTYPE html>
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
            <b>Best accepted edge record:</b> {best_text}<br>
            <b>Accepted records held in memory:</b> {len(data_logs)}<br>
            Auto-refreshes every 5 seconds. Trust is calculated from cloud-observed reports.
        </div>
        <table>
            <tr>
                <th>Time (UTC)</th><th>Edge</th><th>Cloud T</th><th>Direct T</th>
                <th>Pitch / Roll</th><th>Encoder</th><th>Vibration</th><th>Status / Reason</th>
            </tr>
            {rows if rows else '<tr><td colspan="8">No accepted reports received yet.</td></tr>'}
        </table>
    </body>
    </html>
    """
    return HTMLResponse(content=html)
