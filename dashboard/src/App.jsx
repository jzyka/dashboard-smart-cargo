import { useEffect, useState } from "react";
import { LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ResponsiveContainer, Legend } from "recharts";

const HOST = window.location.hostname;
const API = `http://${HOST}:8000`;
const TARGET = 1500; // rows per label we aim for
const post = (path, body) =>
  fetch(API + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) => r.json());

const Dot = ({ on, color }) => <span className="dot" style={on ? { background: color, boxShadow: `0 0 8px ${color}` } : {}} />;

const Chart = ({ title, data, lines }) => (
  <div className="card">
    <h3>{title}</h3>
    <ResponsiveContainer width="100%" height={210}>
      <LineChart data={data}>
        <CartesianGrid strokeDasharray="3 3" stroke="#2a3440" />
        <XAxis dataKey="t" tick={{ fontSize: 10, fill: "#9aa7b4" }} minTickGap={40} />
        <YAxis tick={{ fontSize: 10, fill: "#9aa7b4" }} domain={["auto", "auto"]} />
        <Tooltip contentStyle={{ background: "#1a2129", border: "1px solid #2a3440" }} />
        <Legend />
        {lines.map((l) => (
          <Line key={l.key} type="monotone" dataKey={l.key} name={l.name} stroke={l.color} dot={false} isAnimationActive={false} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  </div>
);

export default function App() {
  const [live, setLive] = useState(null);
  const [series, setSeries] = useState([]);
  const [rec, setRec] = useState({ recording: false, label: "normal", session: 0, counts: {}, labels: [] });
  const [label, setLabel] = useState("normal");
  const [online, setOnline] = useState(false);
  const [wsUp, setWsUp] = useState(false);
  const [hist, setHist] = useState([]);
  const [minutes, setMinutes] = useState(10);
  const [muted, setMuted] = useState(false);
  const [resettingDataset, setResettingDataset] = useState(false);
  const [datasetError, setDatasetError] = useState("");

  useEffect(() => {
    fetch(API + "/api/state").then((r) => r.json()).then((s) => { setRec(s); setLabel(s.label); setOnline(s.esp_online); }).catch(() => {});
  }, []);

  useEffect(() => {
    let ws, timer, closed = false;
    const connect = () => {
      ws = new WebSocket(`ws://${HOST}:8000/ws`);
      ws.onopen = () => setWsUp(true);
      ws.onclose = () => { setWsUp(false); if (!closed) timer = setTimeout(connect, 2000); };
      ws.onmessage = (e) => {
        const m = JSON.parse(e.data);
        if (m.type === "status") return setOnline(m.esp_online);
        if (m.type === "dataset_reset") {
          setRec((r) => ({ ...r, ...m.state }));
          setHist([]);
          return;
        }
        setLive(m.d);
        setOnline(m.esp_online);
        setRec((r) => ({ ...r, recording: m.recording, label: m.label, session: m.session, counts: m.counts }));
        setSeries((s) => [...s.slice(-299), { t: m.timestamp.slice(11, 19), acc: m.d.acc_mag, accMax: m.d.acc_max, p: m.d.pressure, temp: m.d.temp, hum: m.d.hum }]);
      };
    };
    connect();
    return () => { closed = true; clearTimeout(timer); ws && ws.close(); };
  }, []);

  useEffect(() => {
    const load = () => fetch(`${API}/api/history?minutes=${minutes}`).then((r) => r.json()).then((rows) =>
      setHist(rows.map((x) => ({ ...x, t: x.timestamp.slice(11, 19) })))).catch(() => {});
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, [minutes]);

  const startStop = () => post("/api/record", { label, recording: !rec.recording }).then(setRec);
  const pickLabel = (l) => { setLabel(l); if (rec.recording) post("/api/record", { label: l, recording: true }).then(setRec); };
  const toggleMute = () => post("/api/cmd", { cmd: muted ? "unmute" : "mute" }).then(() => setMuted(!muted));
  const resetDataset = async () => {
    if (!window.confirm("Delete all recorded sensor data, including dataset rows and history? This cannot be undone.")) return;
    setResettingDataset(true);
    setDatasetError("");
    try {
      const response = await fetch(`${API}/api/dataset`, { method: "DELETE" });
      const state = await response.json();
      if (!response.ok) throw new Error(state.detail || "Could not reset dataset");
      setRec(state);
      setHist([]);
    } catch (error) {
      setDatasetError(error.message);
    } finally {
      setResettingDataset(false);
    }
  };
  const alerting = live && live.alert !== "none";
  const total = Object.values(rec.counts || {}).reduce((a, b) => a + b, 0);

  return (
    <div className="wrap">
      <div className="top">
        <h2 style={{ margin: 0 }}>Cargo Monitor</h2>
        <div className="row small">
          <span><Dot on={wsUp} color="#5fd68a" />Backend</span>
          <span><Dot on={online} color="#5fd68a" />ESP32</span>
          <span><Dot on={rec.recording} color="#ff5252" />{rec.recording ? `Recording "${rec.label}" (session ${rec.session})` : "Not recording"}</span>
        </div>
      </div>

      <div className={`banner ${alerting ? "bad" : "ok"}`}>
        {!live ? "Waiting for data..." : alerting ? `ALERT: ${live.alert.toUpperCase()}` : "All OK"}
      </div>

      <div className="grid g4">
        <div className="card"><h3>Acceleration</h3><div className="big">{live ? live.acc_mag.toFixed(1) : "-"}</div><div className="small">m/s² (rest ≈ 9.8)</div></div>
        <div className="card"><h3>Temperature</h3><div className="big">{live && live.temp != null ? live.temp.toFixed(1) : "-"}°C</div><div className="small">Humidity {live && live.hum != null ? live.hum.toFixed(0) : "-"}%</div></div>
        <div className="card"><h3>Pressure</h3><div className="big">{live ? live.pressure : "-"}</div><div className="small">baseline {live ? live.p_base : "-"}</div></div>
        <div className="card"><h3>Actuators</h3>
          <div><Dot on={live?.buzzer} color="#ffb020" />Buzzer</div>
          <div><Dot on={live?.red} color="#ff4d4d" />Red <Dot on={live?.yellow} color="#ffd43b" />Yellow <Dot on={live?.green} color="#40d67a" />Green</div>
        </div>
      </div>

      <div className="grid g2">
        <Chart title="Live acceleration (last 30 s)" data={series} lines={[{ key: "acc", name: "acc_mag", color: "#4dabf7" }, { key: "accMax", name: "acc_max", color: "#ff922b" }]} />
        <Chart title="Live pressure (last 30 s)" data={series} lines={[{ key: "p", name: "pressure", color: "#51cf66" }]} />
      </div>

      <div className="card" style={{ marginTop: 12 }}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h3 style={{ margin: 0 }}>History</h3>
          <div className="row">{[5, 30, 60].map((m) => <button key={m} className={minutes === m ? "on" : ""} onClick={() => setMinutes(m)}>{m} min</button>)}</div>
        </div>
        <ResponsiveContainer width="100%" height={230}>
          <LineChart data={hist}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a3440" />
            <XAxis dataKey="t" tick={{ fontSize: 10, fill: "#9aa7b4" }} minTickGap={50} />
            <YAxis yAxisId="a" tick={{ fontSize: 10, fill: "#9aa7b4" }} />
            <YAxis yAxisId="p" orientation="right" tick={{ fontSize: 10, fill: "#9aa7b4" }} />
            <Tooltip contentStyle={{ background: "#1a2129", border: "1px solid #2a3440" }} /><Legend />
            <Line yAxisId="a" dataKey="acc_max" name="acc_max" stroke="#ff922b" dot={false} isAnimationActive={false} />
            <Line yAxisId="a" dataKey="temp" name="temp °C" stroke="#f06595" dot={false} isAnimationActive={false} />
            <Line yAxisId="p" dataKey="pressure" name="pressure" stroke="#51cf66" dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="card" style={{ marginTop: 12 }}>
        <h3>Dataset recorder</h3>
        <div className="row">
          {(rec.labels || []).map((l) => <button key={l} className={label === l ? "on" : ""} onClick={() => pickLabel(l)}>{l}</button>)}
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <button className={rec.recording ? "rec" : ""} onClick={startStop}>{rec.recording ? "■ Stop recording" : `● Start recording "${label}"`}</button>
          <button onClick={toggleMute}>{muted ? "Unmute buzzer" : "Mute buzzer"}</button>
          <button onClick={() => post("/api/cmd", { cmd: "recal" })}>Recalibrate pressure</button>
          <a className="btn" href={`${API}/api/export.csv`}>Download dataset.csv</a>
          <button className="danger" onClick={resetDataset} disabled={resettingDataset || rec.recording}>
            {resettingDataset ? "Resetting..." : "Reset dataset"}
          </button>
          <span className="small">{total} labeled rows</span>
        </div>
        {datasetError && <p className="error" role="alert">{datasetError}</p>}
        <div className="grid g4" style={{ marginTop: 10 }}>
          {(rec.labels || []).map((l) => {
            const n = (rec.counts || {})[l] || 0;
            return (
              <div key={l}>
                <div className="small">{l}: {n} / {TARGET}</div>
                <div className="bar"><div style={{ width: `${Math.min(100, (n / TARGET) * 100)}%` }} /></div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
