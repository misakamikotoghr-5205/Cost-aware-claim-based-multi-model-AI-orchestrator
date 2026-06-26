# Quota Policy & Shadow Price

## Why quota is a first-class variable
A subscription call looks "free" per use, but each call **spends a finite pool**
that a more important task might need later. So the system reasons about *quota
health* and an *opportunity cost*, not just raw capability.

## Quota health (0..1)
Headroom above the model's `minimum_reserve`:

```
health = clamp((remaining - reserve) / (1 - reserve), 0, 1)
```
At or below the reserve ⇒ 0 (and a `quota_scarcity` penalty fires).

## Quota Shadow Price
The opportunity cost of spending an expensive, near-empty model now:

```
shadow_price = (1 - health) * unit_cost      # 0..1
```
High when a model is both **expensive and scarce** — discouraging it from
low-value work so it stays available for the task only it can do.

## Reserves (configs/quota_policy.yaml)
| model | minimum_reserve | rationale |
|---|---|---|
| deepseek | 0% | cheap; use aggressively for planning/drafts |
| grok | 10% | keep some video/image generation headroom |
| gemini | 20% | reserve long-context / multimodal verification |
| codex | 20% | reserve for important code work |
| claude_code | 30% | reserve for hard refactors / multi-file reasoning |
| gpt | 20% | reserve for critical final answers / public output |

## Live feeds
`quota_status` in the YAML is a **sample** for the offline demo. In production,
pass real meter readings via a `quota_status` provider (roadmap v0.4). The demo
intentionally ships Claude Code near its reserve so you can see the system route
*around* a scarce strong model — exactly the behavior this project is about.
