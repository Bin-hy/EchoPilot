/** M7 提示浮窗（T21）：三区布局 + WS 实时渲染 + 幻觉标红 + 健康横幅。
 *
 * 消息协议（T17）：asr.segment / question.detected / answer.skeleton /
 * answer.delta / answer.done / status.health
 */
import { useEffect, useRef, useState } from "react";
import { SidecarWS, WSMessage, WSStatus } from "../lib/ws-client";

type Turn = {
  turnId: string;
  question: string;
  skeleton: string[];
  full: string;
  violations: string[];
  done: boolean;
};

/** 把 answer 文本中的违规实体标红（F7）。 */
function highlight(text: string, violations: string[]) {
  if (!violations.length) return text;
  const parts: (string | JSX.Element)[] = [text];
  violations.forEach((v, vi) => {
    for (let i = parts.length - 1; i >= 0; i--) {
      const p = parts[i];
      if (typeof p !== "string") continue;
      const idx = p.indexOf(v);
      if (idx === -1) continue;
      parts.splice(
        i, 1,
        p.slice(0, idx),
        <mark key={`${vi}-${i}`} style={{ background: "#7f1d1d", color: "#fecaca", borderRadius: 3 }}>{v}</mark>,
        p.slice(idx + v.length),
      );
    }
  });
  return parts;
}

export default function App() {
  const [status, setStatus] = useState<WSStatus>("connecting");
  const [health, setHealth] = useState<{ ok: boolean; detail: string }>({
    ok: true, detail: "",
  });
  const [transcript, setTranscript] = useState<{ channel: string; text: string }[]>([]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [expanded, setExpanded] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const ws = new SidecarWS(handleMessage, setStatus);
    ws.connect();
    return () => ws.close();
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [turns, transcript]);

  function handleMessage(msg: WSMessage) {
    switch (msg.type) {
      case "asr.segment":
        if (msg.is_final) {
          setTranscript((t) => [
            ...t.slice(-30),
            { channel: String(msg.channel), text: String(msg.text) },
          ]);
        }
        break;
      case "question.detected":
        setTurns((ts) => [
          ...ts,
          {
            turnId: `pending-${Date.now()}`,
            question: String(msg.text),
            skeleton: [], full: "", violations: [], done: false,
          },
        ]);
        break;
      case "answer.skeleton":
        updateLastTurn((t) => ({
          ...t,
          turnId: String(msg.turn_id),
          skeleton: msg.items as string[],
        }));
        break;
      case "answer.delta":
        updateLastTurn((t) => ({ ...t, full: t.full + String(msg.text) }));
        break;
      case "answer.done":
        updateLastTurn((t) => ({
          ...t,
          violations: (msg.fact_violations as string[]) ?? [],
          done: true,
        }));
        break;
      case "status.health": {
        const ok = Boolean(msg.channel_ok && msg.asr_ok && msg.llm_ok);
        setHealth({ ok, detail: String(msg.detail ?? "") });
        break;
      }
    }
  }

  function updateLastTurn(fn: (t: Turn) => Turn) {
    setTurns((ts) => {
      if (!ts.length) return ts;
      const copy = [...ts];
      copy[copy.length - 1] = fn(copy[copy.length - 1]);
      return copy;
    });
  }

  const current = turns[turns.length - 1];

  return (
    <div
      style={{
        fontFamily: "system-ui",
        background: "rgba(17, 17, 22, 0.82)",
        color: "#e5e7eb",
        borderRadius: 14,
        height: "100vh",
        boxSizing: "border-box",
        padding: 12,
        display: "flex",
        flexDirection: "column",
        gap: 8,
        backdropFilter: "blur(8px)",
        border: "1px solid rgba(255,255,255,0.08)",
      }}
    >
      {/* 状态条（N8） */}
      <div style={{ display: "flex", fontSize: 12, alignItems: "center", gap: 8 }}>
        <span style={{ color: status === "open" && health.ok ? "#4ade80" : "#f87171" }}>
          ●
        </span>
        <span style={{ color: "#9ca3af" }}>
          {status !== "open"
            ? "sidecar 连接中…"
            : health.ok
              ? "监听中"
              : `异常：${health.detail}`}
        </span>
        <button
          style={{ marginLeft: "auto", background: "none", border: "none", color: "#9ca3af", cursor: "pointer" }}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "收起 ▴" : "展开 ▾"}
        </button>
      </div>

      <div ref={scrollRef} style={{ flex: 1, overflow: "auto", display: "flex", flexDirection: "column", gap: 8 }}>
        {/* 当前问题区 */}
        {current && (
          <div style={{ background: "rgba(124,58,237,0.18)", borderRadius: 8, padding: 8 }}>
            <div style={{ fontSize: 11, color: "#a78bfa" }}>面试官的问题</div>
            <div style={{ fontSize: 14 }}>{current.question}</div>
          </div>
        )}

        {/* 要点提纲区（首屏，≤3s 出现） */}
        {current && current.skeleton.length > 0 && (
          <div style={{ background: "rgba(37,99,235,0.15)", borderRadius: 8, padding: 8 }}>
            <div style={{ fontSize: 11, color: "#93c5fd" }}>参考提纲</div>
            {current.skeleton.map((item, i) => (
              <div key={i} style={{ fontSize: 15, fontWeight: 600, padding: "2px 0" }}>
                • {item}
              </div>
            ))}
          </div>
        )}

        {/* 完整参考回答区（可展开） */}
        {expanded && current && current.full && (
          <div style={{ background: "rgba(255,255,255,0.05)", borderRadius: 8, padding: 8 }}>
            <div style={{ fontSize: 11, color: "#9ca3af" }}>
              完整参考{current.done ? "" : "（生成中…）"}
            </div>
            <div style={{ fontSize: 13, lineHeight: 1.7, whiteSpace: "pre-wrap" }}>
              {highlight(current.full, current.violations)}
            </div>
            {current.violations.length > 0 && (
              <div style={{ fontSize: 11, color: "#fca5a5", marginTop: 4 }}>
                ⚠️ 标红内容未出现在您的档案中，请核实后再使用
              </div>
            )}
          </div>
        )}

        {/* 转写滚动区 */}
        <div style={{ fontSize: 11, color: "#6b7280" }}>
          {transcript.slice(-6).map((seg, i) => (
            <div key={i}>
              <b style={{ color: seg.channel === "interviewer" ? "#a78bfa" : "#67e8f9" }}>
                {seg.channel === "interviewer" ? "面试官" : "我"}：
              </b>
              {seg.text}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
