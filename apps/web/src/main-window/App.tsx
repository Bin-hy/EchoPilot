/** M7 主窗口（T20）：档案管理 / 设置 / 历史回看 + 监听控制。 */
import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

const API = "http://127.0.0.1:18321";

type Profile = {
  profile_id: string;
  name: string;
  resume_text?: string;
  jd_text?: string;
  updated_at: number;
};
type Session = {
  session_id: string;
  profile_id: string;
  started_at: number;
  ended_at: number | null;
  status: string;
};
type Settings = Record<string, string | number>;

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`);
  return resp.json();
}

export default function App() {
  const [tab, setTab] = useState<"profiles" | "settings" | "history">("profiles");
  const [healthy, setHealthy] = useState(false);
  const [listening, setListening] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const tick = async () => {
      try {
        setHealthy(await invoke<boolean>("sidecar_health"));
      } catch {
        setHealthy(false);
      }
    };
    tick();
    const timer = setInterval(tick, 3000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div style={{ fontFamily: "system-ui", padding: 20, maxWidth: 880, margin: "0 auto" }}>
      <header style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <h2 style={{ margin: 0 }}>EchoPilot</h2>
        <span style={{ color: healthy ? "#16a34a" : "#dc2626" }}>
          {healthy ? "● sidecar 正常" : "● sidecar 异常（守护进程重启中…）"}
        </span>
        {listening && <span style={{ color: "#2563eb" }}>● 监听中</span>}
        <nav style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          {(["profiles", "settings", "history"] as const).map((t) => (
            <button key={t} onClick={() => setTab(t)}
              style={{ fontWeight: tab === t ? 700 : 400 }}>
              {{ profiles: "档案", settings: "设置", history: "历史" }[t]}
            </button>
          ))}
        </nav>
      </header>
      {error && (
        <p style={{ color: "#dc2626" }} onClick={() => setError("")}>{error} ✕</p>
      )}
      <hr />
      {tab === "profiles" && (
        <Profiles listening={listening} setListening={setListening} onError={setError} />
      )}
      {tab === "settings" && <SettingsPage onError={setError} />}
      {tab === "history" && <History onError={setError} />}
    </div>
  );
}

/* ── 档案页（F8）────────────────────────────────────────── */
function Profiles(props: {
  listening: boolean;
  setListening: (v: boolean) => void;
  onError: (msg: string) => void;
}) {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [form, setForm] = useState({ name: "", resume_text: "", jd_text: "" });
  const [cardifyStatus, setCardifyStatus] = useState<Record<string, string>>({});
  const [replayPath, setReplayPath] = useState("");

  const reload = useCallback(async () => {
    setProfiles(await api<Profile[]>("/profiles"));
  }, []);
  useEffect(() => { reload().catch((e) => props.onError(String(e))); }, [reload]);

  const save = async () => {
    try {
      await api("/profiles", { method: "POST", body: JSON.stringify(form) });
      setForm({ name: "", resume_text: "", jd_text: "" });
      await reload();
    } catch (e) { props.onError(String(e)); }
  };

  const cardify = async (pid: string) => {
    setCardifyStatus((s) => ({ ...s, [pid]: "卡片化中…" }));
    try {
      const r = await api<{ cards: number }>(`/profiles/${pid}/cardify`, { method: "POST" });
      setCardifyStatus((s) => ({ ...s, [pid]: `✓ ${r.cards} 张卡片` }));
    } catch (e) {
      setCardifyStatus((s) => ({ ...s, [pid]: `✕ ${String(e).slice(0, 60)}` }));
    }
  };

  const remove = async (pid: string) => {
    await api(`/profiles/${pid}`, { method: "DELETE" });
    await reload();
  };

  const start = async (pid: string) => {
    try {
      await invoke("start_listening", {
        args: {
          profile_id: pid,
          replay_path: replayPath || null,
          replay_speed: replayPath ? 1.0 : null,
        },
      });
      props.setListening(true);
    } catch (e) { props.onError(String(e)); }
  };

  const stop = async () => {
    await invoke("stop_listening");
    props.setListening(false);
  };

  return (
    <div>
      <h3>面试档案</h3>
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        <input placeholder="回放音频转写 JSON 路径（测试用，可空）"
          value={replayPath} onChange={(e) => setReplayPath(e.target.value)}
          style={{ flex: 1 }} />
        {props.listening
          ? <button onClick={stop}>■ 停止监听（⌃⇧X）</button>
          : null}
      </div>
      {profiles.map((p) => (
        <div key={p.profile_id} style={{ border: "1px solid #ddd", borderRadius: 8, padding: 12, marginBottom: 8 }}>
          <b>{p.name}</b>
          <span style={{ marginLeft: 12, color: "#888", fontSize: 12 }}>
            {new Date(p.updated_at * 1000).toLocaleString()}
          </span>
          <span style={{ float: "right", display: "flex", gap: 8 }}>
            <button onClick={() => cardify(p.profile_id)}>卡片化</button>
            <button onClick={() => start(p.profile_id)} disabled={props.listening}>▶ 开始监听</button>
            <button onClick={() => remove(p.profile_id)}>删除</button>
          </span>
          {cardifyStatus[p.profile_id] && (
            <div style={{ fontSize: 12, color: "#666" }}>{cardifyStatus[p.profile_id]}</div>
          )}
        </div>
      ))}
      <h4>新建档案</h4>
      <input placeholder="档案名（如：字节-后端）" value={form.name}
        onChange={(e) => setForm({ ...form, name: e.target.value })}
        style={{ width: "100%", marginBottom: 8 }} />
      <textarea placeholder="简历全文" rows={6} value={form.resume_text}
        onChange={(e) => setForm({ ...form, resume_text: e.target.value })}
        style={{ width: "100%", marginBottom: 8 }} />
      <textarea placeholder="目标岗位 JD" rows={4} value={form.jd_text}
        onChange={(e) => setForm({ ...form, jd_text: e.target.value })}
        style={{ width: "100%", marginBottom: 8 }} />
      <button onClick={save} disabled={!form.name || !form.resume_text}>保存档案</button>
    </div>
  );
}

/* ── 设置页（N6/N10）────────────────────────────────────── */
function SettingsPage(props: { onError: (msg: string) => void }) {
  const [settings, setSettings] = useState<Settings>({});
  const [provider, setProvider] = useState("default");
  const [apiKey, setApiKey] = useState("");

  useEffect(() => {
    api<Settings>("/settings").then(setSettings).catch((e) => props.onError(String(e)));
  }, []);

  const saveKey = async () => {
    try {
      await api("/settings/keys", {
        method: "PUT",
        body: JSON.stringify({ provider, api_key: apiKey }),
      });
      setApiKey("");
      props.onError("✓ Key 已存入系统钥匙串");
    } catch (e) { props.onError(String(e)); }
  };

  const saveField = async (key: string, value: string | number) => {
    const updated = await api<Settings>("/settings", {
      method: "PUT", body: JSON.stringify({ [key]: value }),
    });
    setSettings(updated);
  };

  return (
    <div>
      <h3>设置</h3>
      <h4>API Key（存 macOS 钥匙串，不明文落盘）</h4>
      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        <input placeholder="provider（默认 default）" value={provider}
          onChange={(e) => setProvider(e.target.value)} />
        <input placeholder="sk-..." type="password" value={apiKey}
          onChange={(e) => setApiKey(e.target.value)} style={{ flex: 1 }} />
        <button onClick={saveKey} disabled={!apiKey}>保存</button>
      </div>
      <h4>服务商与模型（OpenAI 兼容）</h4>
      {(
        [
          ["llm_base_url", "Base URL"],
          ["llm_cheap_model", "廉价模型（分类/检测）"],
          ["llm_flagship_model", "旗舰模型（回答生成）"],
        ] as const
      ).map(([key, label]) => (
        <div key={key} style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "center" }}>
          <label style={{ width: 180 }}>{label}</label>
          <input defaultValue={String(settings[key] ?? "")} style={{ flex: 1 }}
            onBlur={(e) => saveField(key, e.target.value)} />
        </div>
      ))}
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <label style={{ width: 180 }}>静音阈值（ms）</label>
        <input type="number" defaultValue={String(settings.silence_threshold_ms ?? 800)}
          onBlur={(e) => saveField("silence_threshold_ms", Number(e.target.value))} />
      </div>
    </div>
  );
}

/* ── 历史页（F10）───────────────────────────────────────── */
function History(props: { onError: (msg: string) => void }) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [detail, setDetail] = useState<Record<string, { segments: { channel: string; text: string; start_ms: number }[]; turns: { question_text: string; answer_skeleton: string[]; answer_full: string; fact_violations: string[] }[] }>>({});

  const reload = useCallback(async () => {
    setSessions(await api<Session[]>("/sessions"));
  }, []);
  useEffect(() => { reload().catch((e) => props.onError(String(e))); }, [reload]);

  const open = async (sid: string) => {
    const d = await api<never>(`/sessions/${sid}/export`);
    setDetail((prev) => ({ ...prev, [sid]: d }));
  };

  const remove = async (sid: string) => {
    await api(`/sessions/${sid}`, { method: "DELETE" });
    await reload();
  };

  return (
    <div>
      <h3>面试记录</h3>
      {sessions.length === 0 && <p style={{ color: "#888" }}>暂无记录</p>}
      {sessions.map((s) => (
        <div key={s.session_id} style={{ border: "1px solid #ddd", borderRadius: 8, padding: 12, marginBottom: 8 }}>
          <b>{new Date(s.started_at * 1000).toLocaleString()}</b>
          <span style={{ marginLeft: 8, color: s.status === "ended" ? "#16a34a" : "#2563eb" }}>
            {s.status}
          </span>
          <span style={{ float: "right", display: "flex", gap: 8 }}>
            <button onClick={() => open(s.session_id)}>回看</button>
            <button onClick={() => remove(s.session_id)}>删除</button>
          </span>
          {detail[s.session_id] && (
            <div style={{ marginTop: 12, fontSize: 13 }}>
              <h4 style={{ margin: "8px 0" }}>问答对（{detail[s.session_id].turns.length}）</h4>
              {detail[s.session_id].turns.map((t, i) => (
                <div key={i} style={{ background: "#f8fafc", borderRadius: 6, padding: 8, marginBottom: 6 }}>
                  <div><b>问：</b>{t.question_text}</div>
                  <div><b>提纲：</b>{t.answer_skeleton.join(" / ")}</div>
                  <div style={{ color: "#555" }}>{t.answer_full}</div>
                  {t.fact_violations.length > 0 && (
                    <div style={{ color: "#dc2626" }}>
                      ⚠️ 档案外实体：{t.fact_violations.join("、")}
                    </div>
                  )}
                </div>
              ))}
              <h4 style={{ margin: "8px 0" }}>完整转写（{detail[s.session_id].segments.length} 段）</h4>
              <div style={{ maxHeight: 240, overflow: "auto" }}>
                {detail[s.session_id].segments.map((seg, i) => (
                  <div key={i}>
                    <b style={{ color: seg.channel === "interviewer" ? "#7c3aed" : "#0891b2" }}>
                      {seg.channel === "interviewer" ? "面试官" : "我"}
                    </b>{" "}
                    {seg.text}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
