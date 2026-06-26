# Security Policy

## No secrets in this repository

This project is an **orchestration / decision layer**. It does **not** store,
request, or transmit API keys. Every model backend it routes to (GPT, Claude,
Gemini, Grok, Codex, DeepSeek, and agent CLIs like Hermes / Antigravity)
authenticates through its **own** mechanism — OAuth, OS keyring, or that tool's
own `.env` file outside this repo.

Config files here (`configs/*.yaml`) describe **capabilities, prices, and quota
policy** — never credentials. Keep machine-specific paths or keys in a local
`.env` (git-ignored).

## Reporting a vulnerability

If you find a security issue (e.g. a code path that could leak a credential),
please open a **private** report:

- Use GitHub's **"Report a vulnerability"** (Security tab → Advisories), or
- Open an issue **without** sensitive details and ask for a private channel.

Never paste real API keys, tokens, or logs containing them into public issues.
Redact them as `[REDACTED]`.

## Before you push (checklist)

- [ ] `git status` shows no `.env`, `*.key`, `auth.json`, `runs/`
- [ ] `git grep -nE "sk-|api[_-]?key|token|secret|@gmail"` returns nothing real
- [ ] No personal absolute paths with your username hard-coded
- [ ] `model_prices.yaml` contains list prices only, no account/billing IDs
