# Pulse 109 Copilot architecture

```text
                    ┌ OpenAI Responses (primary)
                    │
Operator → Pulse → ProviderRouter
                    ├ Qwen/Brev (optional)
                    │
                    └ Deterministic (always available)

Laya → classification signal
E5 → similarity
Pulse → routing, incidents, SLA, audit and actions
Human → final important decision
```

Pulse asks for one safe `CopilotResult`; it does not depend on a specific model. Providers receive an explicit `CopilotContext` whitelist with sanitized complaint text, confirmed Pulse facts and no operator identity, account ID, phone, email, IIN or full audit history. Every provider result passes the same Pydantic contract and checks for unsupported actions, invented deadlines, fake completion claims and inconsistent clarification.

## Provider and model routing

`P109_COPILOT_PROVIDER` selects the primary provider and `P109_COPILOT_FALLBACKS` defines the ordered chain. `deterministic` is appended even if omitted. Transient timeout, 429 and 5xx failures have at most two bounded retries with jitter. Authentication, invalid requests and schema failures are not retried. Repeated provider failures open an in-process circuit breaker; the next request uses the next provider.

OpenAI uses the official Python SDK and Responses API `responses.parse(..., text_format=CopilotOutput)`. Current documented defaults are `gpt-5.6-luna` for fast work, `gpt-5.6-terra` for normal operator cases and `gpt-6-astra` for complex cases. All IDs are environment values. See the official [Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs) and [model catalog](https://developers.openai.com/api/docs/models).

## Privacy, images and audit

The backend removes phone, email, IIN and the known address before external inference. An image is included only when the operator selects “Учесть фото”; accepted formats are JPEG, PNG and WebP up to 4 MB. The model may return neutral `visual_evidence`, but cannot confirm an incident, cause, service, severity or resolution.

Audit records provider, model, tier, prompt version, request/result IDs, token counts, configured cost estimate, latency, action and shadow agreement. Prompt text and generated reply are not stored in the Copilot audit event. Operator feedback stores result ID, provider, model, yes/no and an optional reason.

## Voice

For live transcription, the browser streams PCM to the Pulse WebSocket endpoint; Pulse proxies allowed audio events to OpenAI. The API key never reaches JavaScript. The current transcription default is `gpt-live-transcribe`; `gpt-realtime-2.1` is the current speech-to-speech model documented by OpenAI. Partial text is debounced before analysis, and stale analysis is ignored. If Realtime fails, Pulse uses the existing STT endpoint; if STT also fails, the form remains editable. See [Realtime](https://developers.openai.com/api/docs/guides/realtime) and [Realtime transcription](https://developers.openai.com/api/docs/guides/realtime-transcription).

## Rollout

Start with:

```env
P109_COPILOT_PROVIDER=openai
P109_COPILOT_FALLBACKS=deterministic
P109_OPENAI_TRAFFIC_PERCENT=100
P109_COPILOT_SHADOW_QWEN=0
```

Add `qwen` to fallbacks only when the private service is configured. Shadow mode records action agreement and latency only; its reply never reaches the operator. The benchmark script contains 20 RU, 20 KK and 10 mixed cases and reports schema, language, action, unsafe-claim, latency, fallback and configured-cost metrics.
