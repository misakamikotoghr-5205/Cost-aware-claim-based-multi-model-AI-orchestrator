# Local judge native extension

This independent install package contains the Python adapter, authenticated REST
backend and native Desktop SDK page. It requires `orchestrator-core`; it is not
preinstalled in that package.

Install and enable it through native extension management. Configure the local
Ollama URL/model, test the connection, and explicitly choose **Install selected
local judge model** when weights are missing. Ollama and model weights are not
bundled; the page links the official runtime installer. Model downloads expose
progress and cancellation, and are never started automatically.

Only loopback local inference is used. Failed judgement preserves deterministic
scoring, without paid cloud fallback. Read [native extensions](../../../../docs/native-extensions.md)
for setup, data isolation, cancellation limits and REST details.
