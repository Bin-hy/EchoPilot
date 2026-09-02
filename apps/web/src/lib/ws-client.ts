/** WS 客户端：连接/重连状态机 + 消息分发（T21 使用，T17 协议）。 */

export type WSMessage = { type: string; [key: string]: unknown };
export type WSStatus = "connecting" | "open" | "closed";

const WS_URL = `ws://127.0.0.1:18321/ws`;
const RETRY_MS = [500, 1000, 2000, 4000];

export class SidecarWS {
  private ws: WebSocket | null = null;
  private retry = 0;
  private stopped = false;
  private heartbeat: ReturnType<typeof setInterval> | null = null;

  constructor(
    private onMessage: (msg: WSMessage) => void,
    private onStatus: (status: WSStatus) => void,
  ) {}

  connect() {
    if (this.stopped) return;
    this.onStatus("connecting");
    const ws = new WebSocket(WS_URL);
    this.ws = ws;

    ws.onopen = () => {
      this.retry = 0;
      this.onStatus("open");
      // 心跳：sidecar 端 receive_text 循环保活
      this.heartbeat = setInterval(() => ws.send("ping"), 15000);
    };
    ws.onmessage = (ev) => {
      try {
        this.onMessage(JSON.parse(ev.data));
      } catch {
        /* 忽略非 JSON */
      }
    };
    ws.onclose = () => {
      this.cleanup();
      this.onStatus("closed");
      if (!this.stopped) {
        const delay = RETRY_MS[Math.min(this.retry++, RETRY_MS.length - 1)];
        setTimeout(() => this.connect(), delay);
      }
    };
    ws.onerror = () => ws.close();
  }

  private cleanup() {
    if (this.heartbeat) clearInterval(this.heartbeat);
    this.heartbeat = null;
    this.ws = null;
  }

  close() {
    this.stopped = true;
    this.cleanup();
    this.ws?.close();
  }
}
