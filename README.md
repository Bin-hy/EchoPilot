# EchoPilot — 实时面试参考助手

远程视频面试（Zoom / 腾讯会议 / 飞书）中，EchoPilot 捕获电脑音频，实时识别面试官的提问，结合你的简历与目标岗位 JD，**3 秒内**在置顶浮窗给出回答提纲与完整参考回答。

- 🎧 **双通道采集**：系统音频 = 面试官，麦克风 = 你，天然区分说话人，无需声纹注册
- ⚡ **两级展示**：先出 3–5 条要点提纲（P50 ≤ 3s），再流式补齐完整参考回答
- 🛡️ **幻觉防线**：回答严格锚定你的简历素材，档案外的公司名/数字/项目名自动标红警示，没有素材就明说
- 🔒 **隐私优先**：全部历史记录仅存本地（SQLite），API Key 存 macOS 钥匙串，无账号体系
- 🔌 **供应商可换**：LLM 与 ASR 均走 OpenAI 兼容协议，不锁定单一服务商

> 当前状态：V0.1 开发者预览版，仅支持 macOS 13+（Apple Silicon / Intel 均可）。

---

## 架构

```
┌────────────────────────────────────────────┐
│ Tauri 桌面壳（React + Rust）                 │
│  主窗口：档案 / 设置 / 历史   浮窗：实时提示  │
└──────────────▲─────────────────────────────┘
               │ WebSocket（实时流）+ REST（控制）
┌──────────────┴─────────────────────────────┐
│ Python sidecar（FastAPI + LangGraph）        │
│  采集 → 流式 ASR → 图外问题检测 → LangGraph   │
│  单轮流水线（分类→检索→生成→幻觉校验→落库）    │
└──────────────┬──────────────┬───────────────┘
          云端流式 ASR      云端 LLM（OpenAI 兼容）
```

详细设计见 [`spec.md`](spec.md)（需求）、[`plan.md`](plan.md)（架构）、[`task.md`](task.md)（任务）、[`checklist.md`](checklist.md)（验收）。

---

## 快速开始（使用打包版）

1. 从 [Releases](../../releases) 下载 `EchoPilot_x64/aarch64.dmg`，拖入「应用程序」。
2. **首次启动被 Gatekeeper 拦截**（未签名）：右键 App →「打开」，或执行：
   ```bash
   xattr -dr com.apple.quarantine /Applications/EchoPilot.app
   ```
3. **授权两项系统权限**（首次启动会弹窗，或手动开启）：
   - 系统设置 → 隐私与安全性 → **屏幕录制** → EchoPilot（系统音频采集必需）
   - 系统设置 → 隐私与安全性 → **麦克风** → EchoPilot
4. 打开主窗口 →「设置」页填入你的 API Key（见下文「环境配置」）。
5. 「档案」页新建档案（粘贴简历全文 + 目标岗位 JD）→ 点「卡片化」→「开始监听」。
6. 开始你的视频会议，浮窗会自动识别面试官提问并给出参考回答。

### 快捷键（面试中无需动鼠标）

| 快捷键 | 功能 |
|--------|------|
| `⌃⇧R` | 对当前问题重新生成回答 |
| `⌃⇧S` | 循环切换回答长度（简洁 60–90 字 / 标准 150–220 字 / 详细 300–400 字） |
| `⌃⇧X` | 停止监听 |

---

## 环境配置在哪？

| 配置项 | 位置 | 说明 |
|--------|------|------|
| **LLM / ASR API Key** | 主窗口「设置」页输入 | 存入 **macOS 钥匙串**（服务名 `EchoPilot`），不明文落盘 |
| **服务商 / 模型** | 主窗口「设置」页 | `llm_base_url`（OpenAI 兼容，默认 DeepSeek）、廉价模型（分类/检测）、旗舰模型（回答生成）、`asr_ws_url`（流式 ASR，默认 OpenAI Realtime transcription） |
| **静音阈值** | 主窗口「设置」页 | 判定"面试官说完了"的静音时长，默认 800ms |
| **面试记录 / 档案数据** | `~/.echopilot/echopilot.db` | SQLite 单文件，纯本地；可用环境变量 `ECHOPILOT_DB_PATH` 覆盖 |
| **浮窗位置 / 透明度** | 浮窗拖拽 | 即时生效 |

### 环境变量（开发 / 调试）

| 变量 | 作用 |
|------|------|
| `ECHOPILOT_DB_PATH` | 覆盖 SQLite 数据文件路径 |
| `ECHOPILOT_FAKE_LLM=1` | 联调模式：不依赖真实 Key，用内置假 LLM 跑全链路 |

---

## 开发

### 环境要求

- macOS 13+、Rust 1.75+、Node 22+（pnpm）、[uv](https://docs.astral.sh/uv/)

### 启动（开发模式）

```bash
# 1. 安装 sidecar 依赖
cd sidecar && uv sync

# 2. 安装前端依赖
cd ../apps/web && pnpm install

# 3. 构建前端 + 启动 App（Rust 壳会自动拉起 sidecar 并守护）
cd ../.. && pnpm --dir apps/web build
cd src-tauri && cargo run
```

### 测试

```bash
# sidecar 全部单元/集成测试（86 个）
cd sidecar && uv run pytest tests/ -q

# 端到端回放联调（需 App 以 ECHOPILOT_FAKE_LLM=1 运行）
../.venv 不需要——直接在另一个终端：
ECHOPILOT_FAKE_LLM=1 cargo run   # src-tauri 目录
python ../scripts/e2e_replay.py  # sidecar/.venv 的 python
```

回放测试音频转写样本：`samples/interview_replay.json`（格式：`[{text, start_ms, end_ms}, ...]`，可加速回放）。

### 目录结构

```
├── spec.md / plan.md / task.md / checklist.md   # Spec 驱动开发四文档
├── src-tauri/        # Rust 壳（sidecar 守护 / 全局快捷键 / 浮窗）
├── apps/web/         # React 前端（主窗口 + 浮窗，Vite 多页）
├── sidecar/          # Python sidecar（FastAPI + LangGraph）
│   ├── sidecar/audio/    # 采集（ScreenCaptureKit + 麦克风）与 VAD
│   ├── sidecar/asr/      # ASR 适配层（云端流式 + 回放）
│   ├── sidecar/detect/   # 图外问题检测器
│   ├── sidecar/agent/    # LangGraph 图 / prompt / 幻觉校验
│   ├── sidecar/profile/  # 档案卡片化与向量检索
│   └── sidecar/storage/  # SQLite + 钥匙串
├── samples/          # 回放测试样本
└── scripts/          # e2e 联调脚本
```

---

## CI / CD

| 工作流 | 触发 | 内容 |
|--------|------|------|
| `.github/workflows/ci.yml` | push / PR | sidecar pytest（86 个）、前端构建、Rust 编译 |
| `.github/workflows/release.yml` | tag `v*` | PyInstaller 打 sidecar 单文件 → Tauri bundle → DMG 上传到 GitHub Release |

发版：

```bash
git tag v0.1.0 && git push origin v0.1.0
```

> 注：Release 产物未做 Apple 开发者签名（需付费账号），用户首次打开需按「快速开始」第 2 步绕过 Gatekeeper。

---

## 隐私与合规

- 原始音频仅流经你配置的云端 ASR 做流式识别，**不落盘、不持久化**；转写文本与档案内容会发送至你配置的 LLM 服务商用于生成回答——请自行评估敏感程度。
- 部分司法辖区录音需双方同意，请在遵守当地法律与面试平台规则的前提下使用。
- 本项目定位桌面远程面试辅助，不提供任何形式的"代面"能力。

## 路线图

- [ ] 声纹注册与多面试官区分
- [ ] AI 复盘报告（回答质量点评）
- [ ] 练习模式（AI 扮演面试官）
- [ ] 浮窗防截屏 / 防屏幕共享
- [ ] Windows 支持

## License

MIT
