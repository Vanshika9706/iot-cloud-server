from fastapi import FastAPI, Request
from datetime import datetime

app = FastAPI()
history = []

@app.get("/ping")
async def ping():
    return {"status": "ok"}

@app.post("/cloud/analyze")
async def cloud_analyze(request: Request):
    data = await request.json()
    timestamp = datetime.utcnow().isoformat()
    entry = {**data, "timestamp": timestamp}
    history.append(entry)
    print(f"[{timestamp}] Logged: {data}")
    return {"status": "logged", "server": "cloud", "timestamp": timestamp}

@app.get("/cloud/history")
async def get_history():
    return {"count": len(history), "records": history}