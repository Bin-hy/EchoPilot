# EchoPilot Plan

## 架构概览

系统由两个进程组成：**Tauri 桌面壳**（UI + 窗口管理 + 全局快捷键）和
**Python sidecar**（音频管线 + 问题检测 + LangGraph Agent + 存储）。
两进程通过本地回环 WebSocket（实时流）+ HTTP REST（CRUD）通信。

```
┌─────────────────────────────────────────────────────────────┐
│ Tauri 桌面壳（React 前端 + Rust 壳）                          │
│  ├─ 主窗口：档案管理 / 设置 / 历史记录回看                     │
│  ├─ 提示浮窗：置顶半透明，展示问题 + 提纲 + 完整稿             │
│  └─ Rust 层：全局快捷键、窗口置顶/透明度、sidecar 进程守护      │
└──────────────▲──────────────────────────────▲───────────────┘
               │ WebSocket（转写/提纲/回答流式推送）
               │ HTTP REST（档案 CRUD / 会话控制 / 设置）
┌──────────────┴──────────────────────────────┴───────────────┐
│ Python sidecar（FastAPI + LangGraph）                        │
│  ├─ audio_capture：系统音频（ScreenCaptureKit）+ 麦克风       │
│  │   双通道环形缓冲，通道归属即说话人标签                      │
│  ├─ asr_stream：流式 ASR 适配层（OpenAI 兼容协议，可换供应商）│
│  ├─ question_detector：图外常驻检测器（VAD 静音 + 规则启发式  │
│  │   + 廉价 LLM 完整性兜底），判定通过才触发 LangGraph        │
│  ├─ agent_graph：LangGraph 单轮问答流水线                     │
│  │   分类 → 检索 → 流式生成 → 幻觉校验 → 排版                 │
│  ├─ profile_store：档案结构化（简历卡片 + JD 摘要，LLM 预处理）│
│  └─ storage：SQLite（档案/会话/问答对）+ 钥匙串（API Key）    │
└──────────────┬──────────────────────────────┬───────────────┘
               │ 流式 ASR API                 │ LLM API（OpenAI 兼容）
        ┌──────┴──────┐                ┌──────┴──────┐
        │ 云端 ASR    │                │ 云端 LLM    │
        └─────────────┘                └─────────────┘
```

### 组件职责一句话版

| 组件 | 职责 | 对应 spec |
|------|------|-----------|
| Tauri 主窗口 | 档案 CRUD、设置、历史回看 | F8, F10, N5 |
| Tauri 提示浮窗 | 置顶半透明展示问题/提纲/完整稿/标红警示 | F7, F9, N8 |
| Rust 壳 | 全局快捷键（N7）、浮窗置顶、sidecar 生命周期守护 | N7, N8 |
| audio_capture | 双通道采集与自检 | F1, F3 |
| asr_stream | 双通道流式转写，输出带说话人/时间戳的 segment | F2 |
| question_detector | 判定「期望候选人接话的完整问题」，触发/合并/重检 | F4, F5 |
| agent_graph | 单轮问答 LangGraph 流水线，流式输出两级内容 | F6, F7 |
| profile_store | 简历→卡片、JD→摘要的 LLM 预处理与缓存 | F6, F8 |
| storage | SQLite 本地存档 + macOS 钥匙串存 Key | F10, N5, N6 |

### 关键架构原则
1. **检测在图外**：question_detector 每 300–500ms 高频运行，独立于 LangGraph，
   只有判定 `complete` 才触发图的一次运行（成本与延迟控制）
2. **通道即说话人**：双通道天然分离，声纹不做（spec O1）
3. **一切流式**：ASR 流式进、LLM 流式出，WebSocket 逐 token 推到浮窗
4. **供应商可换**：ASR 与 LLM 均走适配层 + OpenAI 兼容协议（N10）

## 核心数据结构

### ASRSegment（转写片段）
| 字段 | 类型 | 说明 |
|------|------|------|
| segment_id | str | 唯一 id |
| channel | "interviewer" \| "me" | 通道归属即说话人（F1/F2） |
| text | str | 本段识别文本 |
| start_ms / end_ms | int | 相对会话开始的时间戳 |
| is_final | bool | ASR 流式的 interim/final 标记 |

### TurnState（LangGraph 状态对象，单轮问答）
| 字段 | 类型 | 说明 |
|------|------|------|
| session_id / turn_id | str | 会话与轮次标识 |
| detected_question | str \| None | 重组后的完整问题文本 |
| question_end_ms | int | 判定说完的时间戳（延迟埋点起点，AC8） |
| question_type | enum | self_intro / project / behavioral / technical / salary / smalltalk / other |
| topic_tags | list[str] | 主题标签（用于检索路由） |
| retrieved_cards | list[ProfileCard] | 检索到的档案卡片（top-k ≤ 5） |
| answer_style | "concise" \| "standard" \| "detailed" | 长度档位（F7） |
| answer_skeleton | list[str] | 3–5 条要点提纲（首屏） |
| answer_full | str | 完整回答（流式累积） |
| fact_violations | list[str] | 档案外实体清单（标红警示用，F7） |
| latency_ms | dict | 各节点耗时埋点（N1 优化数据源） |

### Profile / ProfileCard / JDDigest（档案）
- **Profile**：{ profile_id, name, resume_text, jd_text, created_at, updated_at }
- **ProfileCard**（保存档案时由 LLM 从简历压缩生成，每段经历一张）：
  { card_id, title, org, role, period, tech_stack[], achievements[](含数字), keywords[], raw_excerpt }
- **JDDigest**（同理一次性生成并缓存）：
  { requirements[](≤300 tokens 摘要), keywords[], seniority }

### Session / TurnRecord（面试记录，F10）
- **Session**：{ session_id, profile_id, started_at, ended_at, status }
- **TurnRecord**：{ turn_id, session_id, question_text, question_type,
  answer_skeleton[], answer_full, fact_violations[], trigger: "auto"|"manual",
  latency_ms, created_at }
- 转写全文：SQLite 表 `segments(session_id, channel, text, start_ms, end_ms)`

## 进程间接口

### WebSocket 消息协议（sidecar → Tauri 推送）
| 消息 type | 载荷 | 触发时机 |
|-----------|------|----------|
| `asr.segment` | ASRSegment | 每个 interim/final 片段（F2） |
| `question.detected` | { turn_id, text, question_end_ms } | 检测器判定 complete（F4） |
| `answer.skeleton` | { turn_id, items[] } | 提纲生成完成（F7 首屏，N1） |
| `answer.delta` | { turn_id, text_delta } | 完整稿逐 token 流式（F7） |
| `answer.done` | { turn_id, fact_violations[] } | 生成+校验完成 |
| `status.health` | { channel_ok, asr_ok, llm_ok, detail } | 异常 2 秒内必发（N8） |

### HTTP REST（Tauri → sidecar 控制）
| 端点 | 用途 |
|------|------|
| `POST /sessions` { profile_id } | 开始监听（含通道自检 F3） |
| `POST /sessions/{id}/stop` | 结束面试并落盘 |
| `POST /sessions/{id}/trigger` { text?, mode } | 手动触发/重新生成（F5） |
| `POST /sessions/{id}/style` { style } | 切换长度档位 |
| `GET/POST/PUT/DELETE /profiles[...]` | 档案 CRUD（F8，保存时触发卡片化） |
| `GET /sessions/{id}/export` | 历史记录回看数据（F10） |
| `PUT /settings/keys` | 写入 API Key（进钥匙串，N6） |

## 供应商适配接口（N10）
- **ASRProvider**：`open_stream(channel) → AsyncIterator[ASRSegment]`，
  V0.1 实现 1 个流式 ASR 适配器 + 1 个回放适配器（从音频文件读，
  供 AC4/AC8/AC13 测试复用）
- **LLMProvider**：`stream_chat(messages, **opts) → AsyncIterator[str]`，
  OpenAI 兼容协议，base_url/model 可配

## 模块设计

### M1. audio_capture（Python sidecar）
**职责：** 双通道音频采集与自检（F1, F3）
- 面试官通道：ScreenCaptureKit 应用级音频捕获（PyObjC 调用，macOS 13+），
  采样率 16kHz mono 输出到环形缓冲
- 用户通道：sounddevice 采集麦克风，同样进环形缓冲
- 自检：`check_channels()` 播测试音/读麦克风电平，2 秒内返回各通道状态

**对外接口：**
- `start() -> None` / `stop() -> None`
- `check_channels() -> { system: bool, mic: bool, error: str | None }`
- `frames(channel) -> AsyncIterator[bytes]`（20ms 帧）

**依赖：** pyobjc-framework-ScreenCaptureKit, sounddevice

### M2. asr_stream（Python sidecar）
**职责：** 双通道流式转写，输出 ASRSegment（F2）
- 每通道维护一条 ASR 流；interim 结果也推送（浮窗实时可见）
- 内置 VAD（silero）为检测器提供静音信号

**对外接口：**
- `run(session_id) -> AsyncIterator[ASRSegment]`
- `vad_state(channel) -> { speaking: bool, silence_ms: int }`

**依赖：** M1, ASRProvider 适配层

### M3. question_detector（Python sidecar，图外常驻）
**职责：** 判定「期望候选人接话的完整问题」（F4, F5）
分层漏斗，每 300ms 对面试官通道评估一次：
1. 通道过滤：只评估 `channel == "interviewer"` 的 final segment
2. VAD：静音 ≥ 800ms（可配）才进入判定
3. 规则快筛（零成本）：疑问词/祈使句式命中 +1；长度 < 5 字或纯附和
   （"嗯/对/好的/然后呢"正则表）直接排除
4. 廉价 LLM 兜底：规则置信度不足时，用最小档模型判
   { complete_question, incomplete, statement, backchannel }，
   输入仅最近 3 个 segment（控制延迟 < 400ms）
- 输出 `complete` → 组装 detected_question，触发 M4 一次运行
- 支持追问合并：新问题到来时若上一轮仍在生成，取消旧轮、合并上下文重跑

**对外接口：**
- `feed(segment: ASRSegment) -> None`
- `on_question: Callable[[str, int], None]`（回调：问题文本 + 结束时间戳）

**依赖：** M2, LLMProvider

### M4. agent_graph（LangGraph 单轮流水线）
**职责：** 个性化参考回答生成（F6, F7）
节点（→ 为条件边路由）：
1. `classify`：廉价 LLM 输出 question_type + topic_tags（JSON 模式）
2. `retrieve`：按 question_type 路由——
   project/behavioral → 档案卡片 embedding top-5；
   technical → 卡片 + JD 关键词；smalltalk → 跳过检索直接生成
3. `generate`：旗舰 LLM 流式生成，prompt 强制「先输出 3–5 条要点骨架，
   空一行，再输出完整回答」；system prompt 含事实边界约束与
   长度档位字数区间（简洁 60–90 / 标准 150–220 / 详细 300–400）
4. `fact_guard`（程序化，零 LLM 成本）：从回答抽取公司名/数字/项目名/
   年份，与档案实体白名单 + 问题中实体比对，输出 fact_violations
5. `persist`（异步，不阻塞主路径）：TurnRecord + 埋点写 SQLite

**对外接口：**
- `run_turn(state: TurnState) -> AsyncIterator[event]`
  （skeleton / delta / done 三类事件，经 WebSocket 推给浮窗）

**依赖：** profile_store, LLMProvider, storage

### M5. profile_store（Python sidecar）
**职责：** 档案管理与 LLM 预处理（F6, F8）
- 保存档案时调用 LLM 一次性生成 ProfileCard[] + JDDigest，存 SQLite
- 卡片文本生成 embedding 存本地向量索引（sqlite-vec），供 retrieve 用

**对外接口：** REST 档案 CRUD；`search_cards(profile_id, query, k) -> list[ProfileCard]`

**依赖：** LLMProvider, storage

### M6. storage（Python sidecar）
**职责：** 本地持久化（F10, N5, N6）
- SQLite（stdlib sqlite3 + sqlite-vec）：profiles/cards/sessions/turns/segments
- API Key：python-keyring 写 macOS 钥匙串，进程内缓存，不明文落盘

**对外接口：** 各模块内部 DAO，不暴露 REST

### M7. Tauri 桌面壳
**职责：** 全部 UI 与桌面能力（F7–F10, N7, N8）
- 主窗口（React）：档案管理、设置（Key/服务商/档位/快捷键）、历史回看
- 提示浮窗（独立 Tauri 窗口）：always-on-top + 透明 + 忽略鼠标穿透可选；
  三区布局：当前问题 / 要点提纲 / 可展开完整稿；fact_violations 实体标红
- Rust 层：globalShortcut（开始/停止/重新生成/切档位）、
  sidecar 进程 spawn/守护/崩溃重启、WebSocket/HTTP 客户端

**依赖：** sidecar 全部对外接口

## 模块交互

### 主链路：面试官提问 → 浮窗出现提纲（F1–F7, N1）
```
ScreenCaptureKit ──┐
                   ├→ M1 audio_capture ─→ M2 asr_stream ─→ ASRSegment
麦克风 ────────────┘                         │（WS 推浮窗：实时转写）
                                             ├→ M3 question_detector
                                             │   (VAD静音→规则→LLM兜底)
                                             │   判定 complete
                                             ▼
                                    M4 agent_graph 一次运行：
                                    classify(廉价LLM)
                                      → retrieve(sqlite-vec top-5)
                                      → generate(旗舰LLM流式)
                                          ├→ 骨架完成 → WS answer.skeleton（首屏，≤3s）
                                          └→ 逐token → WS answer.delta
                                      → fact_guard(实体白名单比对)
                                      → WS answer.done(含标红清单)
                                      → persist(异步写 SQLite)
```

### 控制链路：开始监听（F3, F8）
```
Tauri 主窗口「开始监听」
  → POST /sessions { profile_id }
  → M1.check_channels() ──异常──→ WS status.health（明确原因+修复指引）
  → 正常：M1.start() → M2.run() → M3.feed() 常驻
  → Rust 注册全局快捷键（停止/重生成/切档位 → 对应 REST）
```

### 手动兜底链路（F5）
```
全局快捷键 → POST /sessions/{id}/trigger
  → mode=manual：取面试官通道最近 N 秒转写作为问题 → M4.run_turn()
  → mode=regen：取消当前轮，沿用原 detected_question 重跑 M4
```

### 档案准备链路（F8，面试前离线完成，不占实时预算）
```
保存档案 → LLM 压缩：简历→ProfileCard[] / JD→JDDigest
        → 卡片 embedding → sqlite-vec → 返回「卡片化完成」状态
```

### 会话结束链路（F10）
```
POST /sessions/{id}/stop → 停采集/停ASR流 → 汇总落盘 → 主窗口可回看
```

## 文件组织

```
EchoPilot/
├── spec.md / plan.md / task.md / checklist.md
├── src-tauri/                      # Rust 壳
│   ├── src/main.rs                 # 进程入口、sidecar spawn/守护
│   ├── src/hotkey.rs               # 全局快捷键注册与转发
│   ├── src/overlay.rs              # 浮窗创建（置顶/透明/穿透）
│   └── tauri.conf.json
├── apps/web/                       # React 前端（Vite）
│   ├── src/main-window/            # 档案/设置/历史回看
│   ├── src/overlay-window/         # 浮窗：问题/提纲/完整稿/标红
│   └── src/lib/ws-client.ts        # WebSocket 状态机 + 重连
└── sidecar/                        # Python（FastAPI + LangGraph）
    ├── pyproject.toml
    ├── sidecar/main.py             # FastAPI 入口、WS/REST 路由
    ├── sidecar/audio/capture.py    # M1: ScreenCaptureKit + sounddevice
    ├── sidecar/audio/vad.py        # silero VAD
    ├── sidecar/asr/stream.py       # M2: 双通道 ASR 编排
    ├── sidecar/asr/providers.py    # ASRProvider 适配层（云端 + 回放）
    ├── sidecar/detect/detector.py  # M3: 分层问题检测器
    ├── sidecar/detect/rules.py     # 疑问词/祈使句/附和正则表
    ├── sidecar/agent/graph.py      # M4: LangGraph 图定义
    ├── sidecar/agent/nodes.py      # classify/retrieve/generate/fact_guard/persist
    ├── sidecar/agent/prompts.py    # system prompt 模板（事实边界+档位）
    ├── sidecar/agent/factcheck.py  # 实体抽取与白名单比对
    ├── sidecar/profile/store.py    # M5: 档案 CRUD、卡片化、向量检索
    ├── sidecar/storage/db.py       # M6: SQLite schema + DAO
    ├── sidecar/storage/keys.py     # 钥匙串读写
    └── sidecar/llm/provider.py     # LLMProvider（OpenAI 兼容）
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 桌面壳 | Tauri 2 + React | 体积小、窗口能力（置顶/透明）满足 F9；Rust 全局快捷键成熟；sidecar 架构下壳可替换，为 O10 跨平台留路 |
| Agent 运行时 | Python sidecar + LangGraph | 用户指定 LangGraph；Python 音频/LLM 生态最全；独立进程使 UI 与实时链路解耦，崩溃互不影响（N3） |
| 进程通信 | 本地回环 WebSocket（实时）+ REST（控制） | 转写/回答需逐 token 推送，WS 最合适；CRUD 用 REST 简单直接；回环地址不暴露外部（N5） |
| 系统音频 | ScreenCaptureKit（PyObjC） | macOS 13+ 原生 API，无需用户安装虚拟声卡（BlackHole 上手成本高）；满足 N9；风险：PyObjC 绑定稳定性 → T1 spike 最先验证 |
| 麦克风 | sounddevice | 跨平台 PortAudio 绑定，API 简单 |
| VAD | silero-vad（本地） | 轻量（~2MB）、CPU 实时、为检测器提供静音信号，零云端成本 |
| 说话人区分 | 通道归属制，不做声纹 | spec O1：双通道天然分离，规避 diarization 冷启动风险 |
| 问题检测位置 | LangGraph 图外常驻 | 检测每 300ms 高频运行，入图会导致 LLM 调用爆炸；图只承载「一轮问答」的低频高价值链路 |
| 检测策略 | VAD 静音(800ms) + 规则 + 廉价 LLM 兜底 | 规则零成本覆盖 80% 场景；LLM 兜底处理祈使句/边界样本；比纯 VAD 准确率高 3 倍，仅多 200ms |
| 回答生成模型 | 云端旗舰（OpenAI 兼容协议，用户配 Key） | 质量直接决定产品价值；N10 不锁供应商；检测/分类用廉价小模型控制成本 |
| 两级展示实现 | 单 prompt 结构化输出（骨架头部 + 完整稿） | 比两次调用省一次 TTFT；骨架解析完成即推首屏，满足 N1/N2 |
| 幻觉防护 | prompt 事实边界 + 程序化实体白名单比对 | 零额外延迟；LLM 自检严格模式留待后续（spec O5 同类取舍） |
| 档案检索 | sqlite-vec 本地向量索引 top-k | 不引入独立向量库；档案体量小（卡片数十张），性能充足 |
| 本地存储 | SQLite（stdlib + sqlite-vec） | 单文件、零依赖、满足 F10 回看与删除；符合 N5 纯本地 |
| Key 存储 | python-keyring → macOS 钥匙串 | N6 不明文落盘；sidecar 内部闭环，Tauri 只调 REST |
| 测试链路 | 回放 ASR 适配器（音频文件 → ASRSegment） | 练习模式延后（O3）后，AC4/AC8/AC13 的可重复验证手段 |
| Python 依赖管理 | uv | 快、锁文件、单二进制，sidecar 分发友好 |

### 遗留风险（开发期验证项）
| 风险 | 验证时机 |
|------|----------|
| PyObjC 调 ScreenCaptureKit 的音频流稳定性 | T1 spike（最高优先） |
| 云端 ASR 流式的 endpointing 行为与延迟 | T1 spike 一并实测 |
| 旗舰 LLM TTFT 能否稳定 < 1.2s | spike 阶段压测 2 家供应商 |
| 浮窗置顶与会议全屏的兼容性 | UI 联调阶段实测 Zoom/腾讯会议 |
