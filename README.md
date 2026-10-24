# Cost-Aware Claim-Based Multi-Model AI Orchestrator

> A **cost-aware, quota-aware, claim-based, weighted-voting** multi-model AI
> orchestrator. It does not always reach for the strongest model — it picks the
> best **cost/performance** path across GPT, Claude, Gemini, Grok, Codex and
> DeepSeek based on task type, model capability, remaining quota, price, failure
> risk and output quality.

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

The original deterministic offline planner remains available. The repository now
also includes **one Windows/Ubuntu desktop client**, opt-in executor adapters,
a cloud queue and quota ledger, local verification and durable receipts.
**Free-model discovery and a local judging model are optional extensions**, both
disabled by default; operator-configured subscription/local models work without
them. Choosing a model still defaults to the existing zero-model-call scorer.

| Capability | Status |
|---|---|
| Offline scoring, claims, voting, role assignment and shadow prices | Implemented; deterministic |
| Unified desktop configuration and task execution | Implemented; Linux Tk/X virtual-display smoke passed; real Windows deployment still pending |
| Hermes / DeepSeek Harness / Ollama / restricted MCP execution | Implemented; tested with real fixture subprocesses and loopback HTTP |
| Model evidence import, TTL/expiry cache and change events | Implemented; optional extension |
| Optional loopback Ollama judge | Implemented; cannot override admission policy |
| Atomic quota reservations, explicit units and unknown-outcome holds | Implemented; real provider entitlement requires observed account data |
| Authenticated cloud tasks, leases, reconnect IDs and local receipts | Implemented; single-client functional path |
| Real subscription/GUI/Computer Use deployment acceptance | Not performed in this development environment |

See [desktop setup](docs/desktop.md), [cloud/local runtime](docs/cloud-local.md)
and [ROADMAP.md](ROADMAP.md) for scope and validation limits.

## Desktop quick start

```bash
python -m pip install -r requirements.txt
python -m src.desktop
```

Configure your workspace and executor in one client. Enable free-model discovery
or the local judging extension only when you want them. External executors own
their own installation and authentication. Tk support may need the OS package
`python3-tk` on Ubuntu; Windows Python installers normally include it.

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

原有离线规划器继续保留。现在新增**统一的 Windows/Ubuntu 桌面客户端**，集中配置
工作目录、执行器、额度、云端连接和任务回执。**免费模型发现与客户端判断模型都作为
可选扩展，默认关闭**；不安装它们也能使用自己配置的订阅或本地执行器。

已实现 Hermes/DeepSeek Harness/Ollama/受限 MCP 适配、客户端评分、原子额度预留、
云端任务领取及租约、独立验收和未知结果保留。离线集成测试使用真实子进程和本机 HTTP；
真实订阅登录、Windows/Ubuntu 真机桌面、Computer Use 等部署验收尚未完成，不能当作
已经实测。详见 [桌面客户端](docs/desktop.md) 与 [云端本地配置](docs/cloud-local.md)。

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
