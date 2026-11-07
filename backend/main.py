"""Cargo monitor backend: MQTT -> SQLite/CSV -> WebSocket + REST for the dashboard."""
import asyncio, csv, io, json, sqlite3, threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

MQTT_HOST, MQTT_PORT = "localhost", 1883
DB_PATH = Path(__file__).parent / "data" / "cargo.db"
DB_PATH.parent.mkdir(exist_ok=True)

LABELS = ["normal", "carried", "shaken", "impact", "thrown", "crushed", "hot"]
COLS = ["uptime_ms", "ax", "ay", "az", "gx", "gy", "gz", "acc_mag", "acc_min", "acc_max",
        "temp", "hum", "pressure", "p_base", "buzzer", "red", "yellow", "green", "alert"]
ALL_COLS = ["timestamp"] + COLS + ["label", "session"]

db = sqlite3.connect(DB_PATH, check_same_thread=False)
lock = threading.Lock()
db.execute("CREATE TABLE IF NOT EXISTS readings (id INTEGER PRIMARY KEY AUTOINCREMENT, "
           + ", ".join(f"{c} {'TEXT' if c in ('timestamp', 'alert', 'label') else 'REAL'}" for c in ALL_COLS)
           + ")")
db.commit()


state = {"recording": False, "label": "normal", "session": 0, "esp_online": False}
state["session"] = db.execute("SELECT COALESCE(MAX(session),0) FROM readings").fetchone()[0]
counts = {l: 0 for l in LABELS}
for lab, n in db.execute("SELECT label, COUNT(*) FROM readings WHERE label IS NOT NULL GROUP BY label"):
    counts[lab] = n
pending = 0

clients: set[WebSocket] = set()
loop: asyncio.AbstractEventLoop | None = None
mq = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="cargo-backend")


async def broadcast(msg: dict):
    dead = []
    for ws in list(clients):
        try:
            await ws.send_text(json.dumps(msg))
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


def push(msg: dict):
    if loop and clients:
        asyncio.run_coroutine_threadsafe(broadcast(msg), loop)


def handle_data(payload: bytes):
    global pending
    try:
        d = json.loads(payload)
    except ValueError:
        return
    ts = datetime.now().isoformat(timespec="milliseconds")
    with lock:
        rec = state["recording"]
        label = state["label"] if rec else None
        session = state["session"] if rec else None
        row = [ts] + [d.get(c) for c in COLS] + [label, session]
        db.execute(f"INSERT INTO readings ({','.join(ALL_COLS)}) VALUES ({','.join('?' * len(ALL_COLS))})", row)
        pending += 1
        if pending >= 20:
            db.commit()
            pending = 0
        if rec:
            counts[label] += 1
        snapshot = dict(counts)
    push({"type": "data", "timestamp": ts, "d": d, "recording": rec, "label": state["label"],
          "session": state["session"], "counts": snapshot, "esp_online": state["esp_online"]})


def on_connect(c, userdata, flags, reason_code, properties):
    c.subscribe([("cargo/data", 0), ("cargo/status", 0)])


def on_message(c, userdata, msg):
    if msg.topic == "cargo/data":
        handle_data(msg.payload)
    elif msg.topic == "cargo/status":
        state["esp_online"] = msg.payload.decode() == "online"
        push({"type": "status", "esp_online": state["esp_online"]})


mq.on_connect, mq.on_message = on_connect, on_message


@asynccontextmanager
async def lifespan(app: FastAPI):
    global loop
    loop = asyncio.get_running_loop()
    mq.connect_async(MQTT_HOST, MQTT_PORT)
    mq.loop_start()
    yield
    mq.loop_stop()
    with lock:
        db.commit()


app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class RecordReq(BaseModel):
    label: str
    recording: bool


class CmdReq(BaseModel):
    cmd: str


@app.get("/api/state")
def get_state():
    with lock:
        return {**state, "labels": LABELS, "counts": dict(counts), "total": sum(counts.values())}


@app.post("/api/record")
def record(req: RecordReq):
    if req.label not in LABELS:
        raise HTTPException(400, f"label must be one of {LABELS}")
    with lock:
        if req.recording and (not state["recording"] or state["label"] != req.label):
            state["session"] += 1  # each recording run gets its own session id
        state.update(recording=req.recording, label=req.label)
        db.commit()
    return get_state()


@app.post("/api/cmd")
def cmd(req: CmdReq):
    if req.cmd not in ("recal", "mute", "unmute"):
        raise HTTPException(400, "cmd must be recal, mute or unmute")
    mq.publish("cargo/cmd", req.cmd)
    return {"sent": req.cmd}


@app.get("/api/history")
def history(minutes: int = 10, max_points: int = 600):
    cutoff = (datetime.now().timestamp() - minutes * 60)
    cutoff_iso = datetime.fromtimestamp(cutoff).isoformat(timespec="milliseconds")
    with lock:
        cur = db.execute("SELECT timestamp, acc_mag, acc_max, temp, hum, pressure, alert, buzzer, red, yellow, "
                         "green, label FROM readings WHERE timestamp >= ? ORDER BY id", (cutoff_iso,))
        names = [x[0] for x in cur.description]
        rows = cur.fetchall()
    step = max(1, len(rows) // max_points)
    return [dict(zip(names, r)) for r in rows[::step]]


@app.get("/api/export.csv")
def export_csv(labeled_only: bool = True):
    q = f"SELECT {','.join(ALL_COLS)} FROM readings"
    if labeled_only:
        q += " WHERE label IS NOT NULL"
    with lock:
        db.commit()
        rows = db.execute(q + " ORDER BY id").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(ALL_COLS)
    w.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=dataset.csv"})


@app.delete("/api/session/{session}")
def delete_session(session: int):
    """Remove a bad recording run (e.g. wrong label)."""
    with lock:
        db.execute("DELETE FROM readings WHERE session = ?", (session,))
        db.commit()
        counts.update({l: 0 for l in LABELS})
        for lab, n in db.execute("SELECT label, COUNT(*) FROM readings WHERE label IS NOT NULL GROUP BY label"):
            counts[lab] = n
    return get_state()


@app.delete("/api/dataset")
def reset_dataset():
    global pending
    with lock:
        if state["recording"]:
            raise HTTPException(409, "Stop recording before resetting the dataset")
        db.execute("DELETE FROM readings")
        db.commit()
        pending = 0
        state["session"] = 0
        counts.update({label: 0 for label in LABELS})
    result = get_state()
    push({"type": "dataset_reset", "state": result})
    return result


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        clients.discard(ws)