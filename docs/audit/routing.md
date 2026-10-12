# Model routing for the export-privacy-hardening audit

Preflight against `GET https://openrouter.ai/api/v1/models` (catalog reachable,
HTTP 200, 458 models at the time of the run). Every requested id below was
confirmed present **and** confirmed to advertise tool calling where the lane
uses tools (`supported_parameters` contains `tools`).

No OpenRouter API key is present in this environment (`OPENROUTER_API_KEY`
unset, no `.env`). Lanes were therefore dispatched from the same harness,
pinned to the `openrouter` provider and the exact model id, rather than through
a gateway. No key, request log or token is recorded here or anywhere in the
repo.

| Lane | Requested id | Actual id | Provider | Tools | Variant | Reason |
|---|---|---|---|---|---|---|
| A | `z-ai/glm-5.3-flash` | `z-ai/glm-5.3-flash` | openrouter | yes | high | exact id present |
| B | `deepseek/deepseek-v4-flash-0731` | `deepseek/deepseek-v4-flash-0731` | openrouter | yes | high | exact id present |
| C | `moonshotai/kimi-k3` | `moonshotai/kimi-k3` | openrouter | yes | high | exact id confirmed |
| D | `deepseek/deepseek-v4-flash-0731` | `deepseek/deepseek-v4-flash-0731` | openrouter | yes | high | exact id present |
| E | `minimax/minimax-m3` | `minimax/minimax-m3` | openrouter | n/a | (none) | exact id present; model advertises no reasoning variants |

Lead: the integrating model for this session is `deepseek/deepseek-v4.1-flash`
(harness-selected). The strongest available OpenRouter coding model at dispatch
time, `deepseek/deepseek-v4-pro-0813` (tools yes, 1,048,576 context), was
recorded as the intended lead lane; the harness could not switch the live
session, so the lead ran on `deepseek/deepseek-v4.1-flash`. All production
changes were made by the lead; lanes A–D were read-only or test-only.

No id was missing, so no parent-model substitution was needed.
