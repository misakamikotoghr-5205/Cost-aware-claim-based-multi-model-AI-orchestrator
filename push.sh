#!/usr/bin/env bash
# First push helper for cost-aware-ai-orchestrator.
# Usage: GH_USER=<you> GIT_NAME="Your Name" GIT_EMAIL="you@example.com" bash push.sh
set -euo pipefail
: "${GH_USER:?set GH_USER to your GitHub username}"
: "${GH_REPO:=cost-aware-ai-orchestrator}"
: "${GIT_NAME:?set GIT_NAME}"
: "${GIT_EMAIL:?set GIT_EMAIL}"
cd "$(dirname "$0")"

echo "==> Secret scan (must be clean)"
if grep -rinE "sk-[a-z0-9]{20}|ghp_|xoxb-|AIza[0-9A-Za-z_-]{20}|password\s*=\s*[\"']" . \
     --exclude-dir=.git --exclude-dir=__pycache__ --exclude=push.sh \
     --exclude=SECURITY.md --exclude=README.md --exclude=.env.example; then
  echo "!! Potential secret found — aborting."; exit 1
fi
echo "   clean."

echo "==> Running tests"
python tests/test_scoring.py >/dev/null && echo "   tests pass."

git init -q
git config user.name  "$GIT_NAME"
git config user.email "$GIT_EMAIL"
git add -A
echo "==> Files to be committed:"; git status --short
git commit -q -m "Initial commit: cost-aware claim-based multi-model AI orchestrator (planner MVP v0.1)" || true
git branch -M main
git remote add origin "https://github.com/${GH_USER}/${GH_REPO}.git" 2>/dev/null || \
  git remote set-url origin "https://github.com/${GH_USER}/${GH_REPO}.git"
git push -u origin main
echo "==> Done: https://github.com/${GH_USER}/${GH_REPO}"
