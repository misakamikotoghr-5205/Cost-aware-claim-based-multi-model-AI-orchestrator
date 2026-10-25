# MISAKA AGENT

One desktop client for local OAuth/API conversations, optional cloud-to-local
collaboration, account quota, and a compact smart pointer. MISAKA AGENT retains
the complete pinned Hermes native runtime and adds a cost-aware multi-model
orchestrator. A portable Agent Skill connects other compatible agents to the
same runtime, quota Dashboard and paired nodes.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![status](https://img.shields.io/badge/status-cloud%2Flocal%20client-blue)

> **Origin:** First published **2026-06-25** by **misakamikoto**, built with a
> "vibe coding" workflow. The idea is meant to be open — fork it, build on it.
> Attribution welcome but not required (MIT).

*[中文说明见下方](#中文说明)*

---

## The core idea

> **Cheap models explore. Strong models decide. Expensive models finalize.**

Most multi-model routers judge once and assign once. This one treats a task like
a **market**: it publishes *task cards*, lets agents *claim* the work they fit,
resolves disagreements by *weighted voting*, and scores every candidate with a
transparent, **cost- and quota-aware** function — so a powerful-but-nearly-exhausted
model is saved for the task only it can do.

### Current implementation

The desktop presents **MISAKA AGENT**, its own icons, welcome screen and draft
suggestions, on the complete native Hermes Electron client at a locked revision.
Conversations, streaming, history, approvals, Skills,
MCP, tools, profiles and provider OAuth/API settings remain upstream features.
The earlier Tk task form is retained only with `--legacy`.

Use local provider OAuth/API independently, or connect one cloud to multiple
paired local machines. Cloud transport tokens are separate from model credentials.
**Local judgment, free/limited-free discovery and the smart pointer are three
separate installable extension ZIPs.** They are delivered together, not preinstalled.

| Capability | Status |
|---|---|
| Offline scoring, claims, voting, role assignment and shadow prices | Implemented; deterministic |
| MISAKA AGENT desktop and upstream features | Native identity, icons, welcome screen and preserved Hermes runtime |
| Integrated quota Dashboard | Codex/Claude OAuth windows and OpenRouter/DeepSeek API balances; other or unavailable observations remain unknown |
| Hermes / DeepSeek Harness / Ollama / restricted MCP execution | Implemented; tested with real fixture subprocesses and loopback HTTP |
| Free-model discovery | Separate installation package; explicit online refresh, TTL cache and JSON import |
| Local judgment and smart pointer | Separate packages; bounded judgement and a top-right compact intent-to-draft window |
| Portable Agent Skill | Independent ZIP; native private gateway tickets, quota, routing, optional extensions and shared fleet controls |
| Atomic quota reservations, explicit units and unknown-outcome holds | Implemented; real provider entitlement requires observed account data |
| One cloud and multiple local nodes | Pairing, revocable tokens, capability routing, workspaces and receipts |
| Real-machine acceptance | Linux native GUI, local judgement, package lifecycle and loopback fleet checked; real account/Office deployment requires its actual host and credentials |

See [desktop setup](docs/desktop.md), [cloud/local runtime](docs/cloud-local.md)
and [ROADMAP.md](ROADMAP.md) for scope and validation limits.
Windows and Ubuntu build, extension and Skill instructions are also available in
the [Chinese quick start](docs/misaka-quickstart.zh-CN.md).

## Desktop quick start

```bash
python -m pip install -r requirements.txt
python -m src.desktop
```

The launcher prepares/builds the pinned native source using its locked tooling.
See [native setup](docs/native-client.md) for prerequisites and native build
commands. OAuth/API authentication stays in Hermes's native provider settings.
The default MISAKA profile is `~/.misaka-agent`. Cloud use is optional.
Build all optional packages together, then install only the ZIPs you want:

```bash
python scripts/build_extensions.py --ids all --upstream /path/to/pinned/hermes
```

Uninstalled extensions contribute no UI, hooks, screenshot capture or inference.
Local model weights are downloaded explicitly when requested, rather than being
presented as bundled weights. See [extension setup](docs/native-extensions.md).

For other agents, build the independent standard Skill and copy its folder into
the host's Skills directory; see [installation and supported commands](docs/agent-skill.md):

```bash
python scripts/build_agent_skill.py
```

The Skill reuses the selected MISAKA runtime. It carries no provider credentials,
does not install optional extensions, and does not replace the host agent's
permission policy. See [quota Dashboard](docs/quota-dashboard.md) and
[compact pointer](docs/smart-pointer.md) for the desktop interface.

The advanced, account-free protocol fixture exercises real local HTTP, file
execution, quota reservation and independent verification:

```bash
python examples/cloud-local/offline_demo.py
```

This is explicitly a fixture, not a real subscription or model deployment test.

## Offline planner quick start

```bash
git clone https://github.com/misakamikotoghr-5205/Cost-aware-claim-based-multi-model-AI-orchestrator.git
cd Cost-aware-claim-based-multi-model-AI-orchestrator
pip install -r requirements.txt

# Plan a single task
python -m src.cli plan --task "Build a routing CLI" \
    --type code_generation --value important \
    --requires repository_editing,cli_design

# Plan from a task card file, or a batch
python -m src.cli plan-file examples/code_task.yaml
python -m src.cli plan-file examples/batch.yaml

# Inspect capability/price and quota health + shadow prices
python -m src.cli capabilities
python -m src.cli quota
```

### What you'll see

A code task routes to **Codex** (primary) with **GPT** verifying. A research
task routes to **Gemini/Grok**. A trivial task routes to **DeepSeek** (cheapest,
no overkill). And a *critical* multi-file refactor — which would ideally go to
Claude Code — instead routes to **Codex** when the sample quota shows Claude is
**scarce (shadow price 0.90)**. That last one is the whole point: *strong, but
saved for when it's the only option.*

Every pick comes with a score breakdown so the decision is explainable:

```
codex: total=0.81 | capability_fit=+0.34, specialist_bonus=+0.14, ...
```

## How it works

```
task → synthesize claims → initial assignment (scoring)
     → claims agree?  ── yes ─→ execute
                       └─ no  ─→ weighted vote → re-score → execute
     → assign roles (primary / verifier / fallback / summarizer)
     → (on failure) diagnose → reroute or stop
```

- **Scoring** (`src/scoring_engine.py`): `capability_fit`, claim confidence,
  peer votes, history, quota health, cost efficiency, a strength-scaled
  *specialist bonus*, minus penalties (quota scarcity, overkill, high-price-for-
  low-value, hallucination risk). Value-level multipliers make important tasks
  reward capability and trivial tasks reward cheapness.
- **Quota Shadow Price** (`src/quota_manager.py`): `scarcity × price` — the
  opportunity cost of spending an expensive, near-empty model now.
- **Rerouter** (`src/rerouter.py`): classifies failures and applies the
  reroute/stop rules from `configs/routing_rules.yaml` — no blind retries.

## Layout

```
cost-aware-ai-orchestrator/
├── README.md  ROADMAP.md  LICENSE  SECURITY.md  requirements.txt
├── configs/        capability_cards / scoring_weights / quota_policy / model_prices / routing_rules (.yaml)
├── src/            task_card · registry · quota_manager · scoring_engine · claim_engine · voting_engine · rerouter · orchestrator · cli
├── examples/       code / research / video / image / complex_project / batch (.yaml)
├── tests/          planner, adapters, discovery, ledger, queue, routing and runtime tests
├── docs/           architecture · task-market · claim-system · scoring · quota-policy (.md)
├── skills/         SKILL.md  (drop-in agent skill)
└── integrations/   hermes_pipeline_router.py  (a real-world executor reference)
```

## Configure it for your models

Everything routing-related lives in `configs/*.yaml` — **no code changes, no
secrets**. Tune `capability_cards.yaml` as models improve, set your
`minimum_reserve` per model in `quota_policy.yaml`, and feed live quota via a
`quota_status` block. Credentials never live here; each backend authenticates
itself (see [SECURITY.md](./SECURITY.md)).

## Contributing

Issues and PRs welcome. Never include real keys/tokens in issues or logs.

## License

[MIT](./LICENSE)

---

## 中文说明

一个**成本感知、额度感知、基于任务认领、加权投票**的多模型 AI 调度器。它的目标
不是永远调用最强模型，而是在 GPT / Claude / Gemini / Grok / Codex / DeepSeek 之间，
根据任务类型、模型能力、剩余额度、价格、失败风险和输出质量，自动选择**最高性价比**
的执行路径。

核心哲学：

> **便宜模型负责探索，强模型负责判断，高成本模型负责定稿。**

### 当前实现状态

**MISAKA AGENT** 以 Hermes 完整原生 Electron 客户端为基础，使用自己的名称、图标、
开屏和建议，保留原生聊天、历史、工具、Skills、MCP、审批、多 profile 和 OAuth/API 配置。
无需云端也可独立使用；云端支持配对多台本地机，账户身份与节点身份分离。
集成 Dashboard 展示账号额度和云端工作配额，无法观测的数据保持未知。

**判断模型、免费/限免模型发现、智能指针分别交付独立扩展 ZIP**，用户按需安装、启用、
卸载；不再是预装功能的三个开关。智能指针从选区与关键词给出意图候选，支持补词换一批，
开启指针后隐藏主窗，只保留右上角关键词小框；选定后进入原生聊天草稿，由用户确认发送。
模型权重按需下载，不默认启动模型或读取屏幕。另有独立标准 Agent Skill，可让 Claude Code、
Codex、Hermes 等宿主调用同一套运行时、额度查询和节点任务。

已实现 Hermes/DeepSeek Harness/Ollama/受限 MCP 适配、客户端评分、原子额度预留、
云端任务领取及租约、独立验收和未知结果保留。离线集成测试使用真实子进程和本机 HTTP；
Linux/X11 原生 GUI、本地小模型回复、判断模型、独立包生命周期和 Skill 正向任务链已实际运行；
Windows 图形界面和捕获的结论以本次平台报告为准。真实订阅首次登录、Office 编辑及跨物理机部署需要对应账号和环境。
安装与使用见 [Windows / Ubuntu 中文快速开始](docs/misaka-quickstart.zh-CN.md)、
[桌面客户端](docs/desktop.md) 与 [云端本地配置](docs/cloud-local.md)。

```bash
python -m pip install -r requirements.txt
python -m src.desktop
```

### 快速开始

```bash
pip install -r requirements.txt
python -m src.cli plan-file examples/code_task.yaml   # 代码任务 → Codex 主，GPT 验收
python -m src.cli plan-file examples/research_task.yaml
python -m src.cli quota                               # 查看额度健康度 + 影子价格
```

一个亮点示例：关键的多文件重构本应交给 Claude Code，但当示例额度显示 Claude
**接近耗尽（影子价格 0.90）** 时，系统会自动改用 Codex —— 把最强模型留给只有它能做
的任务。这正是本项目的核心价值。

**仓库不含任何密钥**，所有后端各自通过 OAuth / keyring / 自己的 `.env` 认证，详见
[SECURITY.md](./SECURITY.md)。
