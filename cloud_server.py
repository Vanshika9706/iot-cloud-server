from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from datetime import datetime
import math

app = FastAPI()

# Track Edge Server Trust States
edge_trust = {f"edge{i}": {"T": 0.5, "count": 0} for i in range(1, 7)}

# In-Memory Buffer to store the last 50 logged records
data_logs = []

RHO = 0.9
A_ADJ = 3.0

def update_cloud_trust(edge_id: str, success: bool):
    """
    Implements Kong et al. Trust Model:
    Calculates dynamic trust updates based on historical success (S), 
    decay rates (rho), and adaptive scale factors (a_adj).
    """
    node = edge_trust[edge_id]
    S = 1.0 if success else 0.0
    T_old = node["T"]
    
    ratio = 0.0 if T_old == 0 else (S / T_old)
    theta = 1.0 - math.sqrt(abs((ratio - 0.5) / (ratio + 1.0))) if ratio >= 0.5 else 1.0
    
    node["count"] += 1
    n = node["count"]
    
    if S >= T_old:
        delta = (1 - math.exp(-n / (2 * A_ADJ))) * T_old * (1 - S) * abs(S - T_old)
    else:
        delta = (1 - math.exp(-n / A_ADJ)) * S * (1 - S) * abs(S - T_old)
        
    T_new = max(0.0, min(1.0, theta * S + (1 - theta) * T_old + delta))
    node["T"] = T_new
    return T_new

@app.get("/ping")
async def ping():
    return {"status": "cloud_online"}

@app.post("/cloud/log")
async def log_data(request: Request):
    data = await request.json()
    edge_id = data.get("server", "unknown")
    corrupt_flag = data.get("corrupt", False)
    
    if edge_id in edge_trust:
        success = not corrupt_flag
        current_trust = update_cloud_trust(edge_id, success)
        
        # Gatekeeper Rule: Reject data if server trust score drops below 0.4
        if current_trust < 0.4:
            print(f"[{datetime.utcnow().isoformat()}] REJECTED payload from {edge_id} | Trust T = {current_trust:.3f} (< 0.4)")
            raise HTTPException(
                status_code=403, 
                detail=f"Data rejected: {edge_id} trust score too low ({current_trust:.3f})"
            )
    else:
        current_trust = 0.0

    # Attach Cloud Calculated Trust Score to the Logged Record
    data["cloud_trust"] = round(current_trust, 3)
    data["log_time"] = datetime.utcnow().strftime("%H:%M:%S")

    # Store in memory for the browser dashboard (keep latest 50 records)
    data_logs.insert(0, data)
    if len(data_logs) > 50:
        data_logs.pop()

    # PRINT DATA AND TRUST SCORES TO TERMINAL/LOGS
    print(f"\n================ [CLOUD TELEMETRY LOGGED] ================")
    print(f"Timestamp:    {data['log_time']} UTC")
    print(f"Edge Server:  {edge_id}")
    print(f"Cloud Trust:  T = {current_trust:.3f}")
    print(f"Pitch / Roll: Pitch = {data.get('smoothed_pitch')}° | Roll = {data.get('smoothed_roll')}°")
    print(f"Encoder:      Pos = {data.get('encoder_pos')} | Angle = {data.get('encoder_angle_deg')}° | Speed = {data.get('encoder_speed_dps')}°/s")
    print(f"Vibration:    {data.get('vibration')}")
    print(f"Anomaly:      {data.get('anomaly')} (Reason: {data.get('reason')})")
    print(f"===========================================================\n")

    return {"status": "logged", "edge_id": edge_id, "cloud_trust": current_trust}

# ---- BROWSER LIVE DASHBOARD ENDPOINT ----
@app.get("/logs", response_class=HTMLResponse)
async def view_logs():
    rows = ""
    for item in data_logs:
        anomaly_style = "color: red; font-weight: bold;" if item.get("anomaly") else "color: green;"
        rows += f"""
        <tr>
            <td>{item.get('log_time')}</td>
            <td><b>{item.get('server')}</b></td>
            <td><b>{item.get('cloud_trust')}</b></td>
            <td>{item.get('smoothed_pitch')}° / {item.get('smoothed_roll')}°</td>
            <td>{item.get('encoder_pos')} ({item.get('encoder_angle_deg')}°)</td>
            <td>{'YES' if item.get('vibration') else 'NO'}</td>
            <td style="{anomaly_style}">{item.get('reason')}</td>
        </tr>
        """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Cloud Gatekeeper Live Logs</title>
        <meta http-equiv="refresh" content="5">
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; background-color: #f4f7f6; }}
            h2 {{ color: #333; }}
            table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
            th, td {{ padding: 12px 15px; text-align: left; border-bottom: 1px solid #ddd; }}
            th {{ background-color: #007bff; color: white; }}
            tr:hover {{ background-color: #f1f1f1; }}
        </style>
    </head>
    <body>
        <h2>Cloud Gatekeeper — Received Telemetry & Trust Scores</h2>
        <p><i>Auto-refreshing every 5 seconds. Showing latest {len(data_logs)} records.</i></p>
        <table>
            <tr>
                <th>Time (UTC)</th>
                <th>Edge Server</th>
                <th>Cloud Trust (T)</th>
                <th>Pitch / Roll</th>
                <th>Encoder (Pos / Deg)</th>
                <th>Vibration</th>
                <th>Status / Reason</th>
            </tr>
            {rows if rows else "<tr><td colspan='7'>No data received yet. Connect your ESP32 and Edge Servers.</td></tr>"}
        </table>
    </body>
    </html>
    """
    return HTMLResponse(content=html)
