# Cost-Aware Claim-Based Multi-Model AI Orchestrator

> A **cost-aware, quota-aware, claim-based, weighted-voting** multi-model AI
> orchestrator. It does not always reach for the strongest model — it picks the
> best **cost/performance** path across GPT, Claude, Gemini, Grok, Codex and
> DeepSeek based on task type, model capability, remaining quota, price, failure
> risk and output quality.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![status](https://img.shields.io/badge/status-MVP%20v0.1%20(planner)-orange)

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

### What's actually implemented (v0.1, honest status)

This is a **decision/planner MVP**. It is **deterministic and local — it makes
no model calls** to decide. That is deliberate: scoring every task by polling 6
models would burn the very quota this project exists to conserve.

| Capability | Status |
|---|---|
| Task cards, value levels, budgets | ✅ implemented |
| Capability cards + price + quota configs | ✅ implemented |
| Cost/quota-aware scoring with transparent breakdown | ✅ implemented |
| Quota **Shadow Price** (opportunity cost) | ✅ implemented |
| Claim synthesis + agreement check | ✅ implemented (offline stand-in) |
| Weighted voting tie-break | ✅ implemented (offline stand-in) |
| Role assignment (primary/verifier/fallback/summarizer) | ✅ implemented |
| Failure diagnosis + reroute/stop rules | ✅ implemented |
| **Live** capability discovery (Grok/Gemini) | 🔜 roadmap (`--discover`) |
| **Live** multi-model voting (real calls) | 🔜 roadmap (`--live-vote`) |
| Real executor adapters (Hermes/Codex/Claude) | 🔜 roadmap (`integrations/`) |

See [ROADMAP.md](./ROADMAP.md) for the v0.1 → v1.0 path.

## Quick start

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
├── tests/          test_scoring.py  (9 tests, no model calls)
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

### 当前实现状态（v0.1，诚实标注）

这是一个**决策/规划 MVP**，**本地确定性运行、决策时不调用任何模型**。这是有意为之：
为了给每个任务投票而真的去调用 6 个模型，本身就会烧掉这个项目想节省的额度。

- ✅ 已实现：任务卡片、价值分级、能力卡/价格/额度配置、成本与额度感知评分（带透明
  打分明细）、额度影子价格、认领合成与一致性判断、加权投票仲裁、角色分配、失败诊断
  与重路由/停止规则。
- 🔜 路线图：用 Grok/Gemini 做**实时能力发现**（`--discover`）、**真实多模型投票**
  （`--live-vote`）、以及 `integrations/` 里的真实执行器适配（Hermes/Codex/Claude）。

详见 [ROADMAP.md](./ROADMAP.md)。

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
