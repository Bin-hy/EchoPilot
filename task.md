# EchoPilot Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `sidecar/spike/` | T1 临时延迟验证脚本（验证后可删） |
| 新建 | `sidecar/pyproject.toml` | uv 项目与依赖声明 |
| 新建 | `sidecar/main.py` | FastAPI 入口、WS/REST 路由装配 |
| 新建 | `sidecar/storage/db.py` | SQLite schema + DAO |
| 新建 | `sidecar/storage/keys.py` | macOS 钥匙串读写 |
| 新建 | `sidecar/llm/provider.py` | LLMProvider（OpenAI 兼容，流式） |
| 新建 | `sidecar/asr/providers.py` | ASRProvider 接口 + 回放/云端适配器 |
| 新建 | `sidecar/asr/stream.py` | M2 双通道 ASR 编排 |
| 新建 | `sidecar/audio/vad.py` | silero VAD |
| 新建 | `sidecar/audio/capture.py` | M1 ScreenCaptureKit + sounddevice 采集 |
| 新建 | `sidecar/detect/rules.py` | 疑问/祈使/附和规则表 |
| 新建 | `sidecar/detect/detector.py` | M3 分层问题检测器 |
| 新建 | `sidecar/agent/prompts.py` | 生成 prompt 模板（事实边界 + 三档字数） |
| 新建 | `sidecar/agent/factcheck.py` | 实体抽取与白名单比对 |
| 新建 | `sidecar/profile/store.py` | M5 档案 CRUD、卡片化、sqlite-vec 检索 |
| 新建 | `sidecar/agent/nodes.py` | classify/retrieve/generate/fact_guard/persist |
| 新建 | `sidecar/agent/graph.py` | M4 LangGraph 图定义 |
| 新建 | `src-tauri/` | Rust 壳（main/hotkey/overlay/conf） |
| 新建 | `apps/web/` | React 前端（主窗口 + 浮窗 + ws-client） |

## 执行顺序

```
T1(spike) → T2 → T3 → T4 ─┐
                T5 ───────┼→ T6 → T7 → T8 → T9 → T10 → T11 → T12
                          │                                  ↓
                          └→ T13 → T14 → T15 → T16 → T17 ────┘
                                                       ↓
                                        T18 → T19 → T20 → T21 → T22 → T23
```

---

## T1: 技术 spike——延迟生死线（Go/No-Go 闸门）

**文件：** `sidecar/spike/`（临时脚本）
**依赖：** 无
**步骤：**
1. 用 PyObjC 调 ScreenCaptureKit 捕获系统音频（播一段播客），确认能拿到 16kHz PCM 流
2. 接一家云端流式 ASR，实测首字延迟与 endpointing 行为（final 片段何时产出）
3. 压测 2 家 OpenAI 兼容旗舰 LLM 的 TTFT（各测 10 次取 P50）
4. 汇总：ASR 延迟 + 静音窗口 800ms + TTFT + 骨架解析，估算端到端 P50

**验证：** 三项实测数据记录到 spike 输出；全链路估算 P50 ≤ 3s 则 Go，
否则按预案降级（仅提纲 + 缩短静音阈值）并记录决策

## T2: sidecar 脚手架

**文件：** `sidecar/pyproject.toml`, `sidecar/main.py`
**依赖：** T1
**步骤：**
1. `uv init`，声明依赖：fastapi, uvicorn, websockets, langgraph, langchain-openai,
   sounddevice, silero-vad, pyobjc-framework-ScreenCaptureKit, keyring, sqlite-vec, pytest
2. `main.py` 建 FastAPI 应用，挂 `GET /health` 返回 `{"ok": true}`

**验证：** `uv run uvicorn sidecar.main:app` 启动成功，`curl localhost:PORT/health` 返回 200

## T3: SQLite schema + DAO

**文件：** `sidecar/storage/db.py`
**依赖：** T2
**步骤：**
1. 建表：profiles / profile_cards / sessions / turns / segments（字段按 plan.md 数据结构）
2. 实现 DAO：insert/get/list/delete 各表；segments 按 session_id 查询
3. 删除 session 级联删 turns/segments（AC12「删除无残留」）

**验证：** pytest 覆盖建表与五表增删查，级联删除断言通过

## T4: 钥匙串读写

**文件：** `sidecar/storage/keys.py`
**依赖：** T2
**步骤：**
1. python-keyring 封装：`set_key(provider, secret)` / `get_key(provider)` / `delete_key(provider)`
2. 进程内缓存，避免重复读钥匙串

**验证：** pytest：写入→读取一致→删除后为 None；`grep` 项目目录无 secret 明文落盘

## T5: LLMProvider

**文件：** `sidecar/llm/provider.py`
**依赖：** T2
**步骤：**
1. `stream_chat(messages, model, base_url, **opts) -> AsyncIterator[str]`（OpenAI 兼容，httpx SSE）
2. 支持两档模型配置：cheap（分类/检测兜底）/ flagship（生成）
3. 错误分类：鉴权失败/额度耗尽/网络中断 → 供 status.health 上报（N8）

**验证：** 用测试 key 流式调用真实 API，逐 token 到达且完整拼接无误；伪造 401 断言错误分类

## T6: ASRProvider 接口 + 回放适配器

**文件：** `sidecar/asr/providers.py`
**依赖：** T3
**步骤：**
1. 定义 `ASRProvider.open_stream(channel) -> AsyncIterator[ASRSegment]` 抽象接口
2. 回放适配器：读 WAV + 配套转写 JSON（text, start_ms, end_ms），按时间轴
   模拟产出 interim/final ASRSegment（可加速播放）

**验证：** 回放样本 WAV → segment 流的时间戳/channel/文本与标注一致（pytest）

## T7: 云端流式 ASR 适配器

**文件：** `sidecar/asr/providers.py`
**依赖：** T1, T6
**步骤：**
1. 实现 T1 spike 选定的云端 ASR 适配器（WS 发送 20ms PCM 帧，接收 interim/final）
2. 断线重连 + 错误上报 status.health

**验证：** 对麦克风说 3 句话，interim 滚动更新、final 正确断句；拔网 2 秒内收到健康异常

## T8: silero VAD

**文件：** `sidecar/audio/vad.py`
**依赖：** T2
**步骤：**
1. 加载 silero-vad，输入 20ms 帧流，维护 `speaking` 状态与 `silence_ms` 计数
2. 暴露 `feed(frame)` 与 `state(channel)`

**验证：** 构造含 1s 静音的音频，断言 silence_ms 累计 ≈1000±100ms

## T9: 双通道采集 M1

**文件：** `sidecar/audio/capture.py`
**依赖：** T1
**步骤：**
1. ScreenCaptureKit 系统音频 → 重采样 16kHz mono → 环形缓冲
2. sounddevice 麦克风 → 环形缓冲
3. `check_channels()`：系统音频读电平 + 麦克风读电平，2s 内返回状态与错误原因

**验证：** 播音频 + 对麦说话，两路 `frames()` 持续产出；关权限后 check 返回明确错误（AC2 雏形）

## T10: ASR 编排 M2

**文件：** `sidecar/asr/stream.py`
**依赖：** T6, T7, T8, T9
**步骤：**
1. 每通道：capture frames → VAD → ASR 流 → ASRSegment（channel 标签按通道打）
2. `run(session_id)` 合并两路 segment 流；segments 异步写库（T3 DAO）

**验证：** 双通道同时说话，segment 的 channel 标签无串扰（AC1 雏形）；库中 segments 可查

## T11: 检测规则表

**文件：** `sidecar/detect/rules.py`
**依赖：** 无
**步骤：**
1. 疑问词表（为什么/如何/能不能/介绍一下…）、祈使句式（说说/讲讲/谈一谈…）
2. 附和排除表（嗯/对/好的/然后呢…）+ 长度 < 5 字排除
3. `classify_rule(text) -> hit | reject | unsure`

**验证：** pytest：30 条标注样本（疑问/祈使/附和/陈述）规则分类全部符合预期

## T12: 问题检测器 M3

**文件：** `sidecar/detect/detector.py`
**依赖：** T8, T10, T11, T5
**步骤：**
1. 每 300ms 评估：仅面试官通道 final segment + 静音 ≥ 800ms 才进入判定
2. 规则 hit → 触发；reject → 跳过；unsure → 廉价 LLM 判四分类（输入最近 3 段）
3. 触发时组装 detected_question 与 question_end_ms，回调 on_question
4. 追问合并：上轮仍在跑则取消旧轮、合并上下文重跑

**验证：** 回放 20 段标注样本：召回 ≥ 80%，附和零触发，触发延迟 P50 ≤ 1.5s（AC4/AC5 雏形）

## T13: 生成 prompt 模板

**文件：** `sidecar/agent/prompts.py`
**依赖：** 无
**步骤：**
1. system prompt：角色 + 硬性约束（第一人称/口语化/禁用书面连接词）+
   STAR 结构（经历题）与「结论→原理→实例」（技术题）分支
2. 三档字数区间变量（60–90 / 150–220 / 300–400）+ 骨架头部格式约定
3. 事实边界段：仅用 <profile> 标签内素材，无素材时输出固定话术，禁止编造

**验证：** pytest：渲染三档模板无残留 `{变量}`；模板含全部必备段落（关键词断言）

## T14: 幻觉校验 fact_guard

**文件：** `sidecar/agent/factcheck.py`
**依赖：** T3
**步骤：**
1. 从档案卡片构建实体白名单（公司/学校/项目名/数字+单位/年份）
2. 从回答抽取同类实体（正则 + 简单 NER 规则）
3. 白名单 ∪ 问题文本实体 之外 → fact_violations

**验证：** pytest：含档案外实体的回答被检出；实体来自问题本身时不误报

## T15: 档案卡片化 M5

**文件：** `sidecar/profile/store.py`
**依赖：** T3, T5
**步骤：**
1. 保存档案时 LLM 压缩：简历 → ProfileCard[]，JD → JDDigest（JSON 模式）
2. 卡片 embedding → sqlite-vec 索引
3. `search_cards(profile_id, query, k=5)`

**验证：** 保存样例简历 → 卡片字段完整；用「Kafka 项目」查询返回对应卡片（AC10 雏形）

## T16: LangGraph 图 M4

**文件：** `sidecar/agent/nodes.py`, `sidecar/agent/graph.py`
**依赖：** T12, T13, T14, T15
**步骤：**
1. 五节点：classify（cheap LLM JSON）→ retrieve（按类型路由，smalltalk 跳过）
   → generate（旗舰流式，先骨架后完整稿）→ fact_guard（T14）→ persist（异步）
2. 节点入口写 latency_ms 时间戳；`run_turn(state)` 产出 skeleton/delta/done 事件
3. 同时只允许一轮在跑，新轮取消旧轮

**验证：** 注入测试档案跑一轮：事件序 skeleton→delta*→done；latency_ms 各节点齐全；
骨架 3–5 条、字数符合档位区间

## T17: REST/WS 装配

**文件：** `sidecar/main.py`
**依赖：** T10, T12, T16
**步骤：**
1. 挂 plan.md 的 8 个 REST 端点（sessions/profiles/trigger/style/export/keys）
2. WS 端点推送 6 类消息（asr.segment/question.detected/answer.*/status.health）
3. 开始监听时跑 check_channels，异常立即 status.health

**验证：** curl 打全部 REST；wscat 观察回放会话的 6 类消息按时序到达

## T18: Tauri 脚手架 + sidecar 守护

**文件：** `src-tauri/`
**依赖：** T17
**步骤：**
1. `tauri init`，React+Vite 前端骨架（apps/web）
2. Rust：spawn sidecar 进程，健康检查轮询，崩溃自动重启 + 通知前端

**验证：** 启动 App 自动拉起 sidecar（/health 通）；`kill` sidecar 后 5s 内自动恢复

## T19: 全局快捷键 + 浮窗窗口

**文件：** `src-tauri/src/hotkey.rs`, `src-tauri/src/overlay.rs`
**依赖：** T18
**步骤：**
1. globalShortcut 注册四键：开始/停止/重新生成/切档位 → 转发对应 REST
2. 浮窗：always-on-top + 半透明 + 可拖拽 + 透明度/字号设置持久化

**验证：** 焦点在别的应用时快捷键生效；浮窗置顶于 Zoom 窗口之上，调整即时生效（AC11 雏形）

## T20: 主窗口 UI

**文件：** `apps/web/src/main-window/`
**依赖：** T18
**步骤：**
1. 档案页：列表/新建/编辑（简历+JD 文本域）/删除/卡片化状态显示
2. 设置页：API Key、服务商 base_url、模型档位、静音阈值、默认回答档位
3. 历史页：会话列表 → 双通道转写 + 问答对回看 + 删除（AC12）

**验证：** 档案 CRUD 全流程可用；保存历史后回看数据完整；删除后记录消失

## T21: 浮窗 UI + WS 客户端

**文件：** `apps/web/src/overlay-window/`, `apps/web/src/lib/ws-client.ts`
**依赖：** T19, T17
**步骤：**
1. ws-client：连接/重连状态机，消息分发到 store
2. 浮窗三区：当前问题 / 要点提纲（skeleton 到达即渲染）/ 可展开完整稿（delta 流式追加）
3. answer.done 后 fact_violations 实体标红；健康异常横幅（N8）

**验证：** 回放会话中提纲先出、完整稿流式补齐；构造违规实体看到标红

## T22: 端到端联调（回放链路）

**文件：** —（联调）
**依赖：** T20, T21
**步骤：**
1. 回放完整模拟面试音频（10 分钟，含多轮问答）
2. 走全链路：回放 → 检测 → 生成 → 浮窗展示 → 落库 → 历史回看
3. 修复联调中发现的问题

**验证：** 全链路无人值守跑通，浮窗展示与库中记录一致

## T23: 延迟实测

**文件：** —（测量）
**依赖：** T22
**步骤：**
1. 用 latency_ms 埋点统计 question_end_ms → skeleton 推送的端到端延迟
2. 20 次取样，算 P50/P90
3. 不达标时按决策表优化（静音阈值/骨架解析/供应商切换）

**验证：** 输出实测报告：P50 ≤ 3s、P90 ≤ 5s（AC8 数据）
