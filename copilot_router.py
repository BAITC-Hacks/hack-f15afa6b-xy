"""Model selection, provider failover, retries and circuit breaking."""

from __future__ import annotations

import hashlib
import os
import random
import threading
import time
from dataclasses import dataclass

from copilot_contracts import (
    CopilotContext,
    CopilotError,
    CopilotRateLimitError,
    CopilotResult,
    CopilotTimeout,
    CopilotUnavailable,
    sanitize_context,
)


class CopilotModelRouter:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("P109_COPILOT_MODEL_ROUTING", "1") != "0")

    def select(self, context: CopilotContext, budget_degraded: bool = False) -> str:
        budget_degraded = budget_degraded or "budget_degraded" in context.complexity_signals
        if not self.enabled:
            tier = "primary"
        else:
            signals = set(context.complexity_signals)
            complex_case = bool(
                signals.intersection({"model_disagreement", "multiple_services", "ambiguous_incident"})
                or len(signals) >= 2
                or (context.incident_candidate and context.confidence_band == "low")
            )
            simple_case = bool(
                not signals
                and not context.incident_candidate
                and context.confidence_band == "high"
                and len(context.complaint) <= 1200
            )
            tier = "complex" if complex_case else "fast" if simple_case else "primary"
        if budget_degraded:
            return {"complex": "primary", "primary": "fast", "fast": "fast"}[tier]
        return tier


@dataclass
class CircuitState:
    failures: int = 0
    opened_at: float | None = None
    half_open_in_flight: bool = False


class CircuitBreaker:
    def __init__(self, failure_limit: int = 5, cooldown: float = 30.0):
        if failure_limit < 1 or cooldown <= 0:
            raise ValueError("Circuit breaker limits must be positive")
        self.failure_limit = failure_limit
        self.cooldown = cooldown
        self._states: dict[str, CircuitState] = {}
        self._lock = threading.Lock()

    def allow(self, provider: str) -> bool:
        with self._lock:
            state = self._states.setdefault(provider, CircuitState())
            if state.opened_at is None:
                return True
            if time.monotonic() - state.opened_at < self.cooldown:
                return False
            if state.half_open_in_flight:
                return False
            state.half_open_in_flight = True
            return True

    def success(self, provider: str) -> None:
        with self._lock:
            self._states[provider] = CircuitState()

    def failure(self, provider: str) -> None:
        with self._lock:
            state = self._states.setdefault(provider, CircuitState())
            state.failures += 1
            state.half_open_in_flight = False
            if state.failures >= self.failure_limit:
                state.opened_at = time.monotonic()

    def status(self, provider: str) -> str:
        with self._lock:
            state = self._states.get(provider, CircuitState())
            if state.opened_at is None:
                return "closed"
            if time.monotonic() - state.opened_at >= self.cooldown:
                return "half_open"
            return "open"


class ProviderRouter:
    def __init__(
        self,
        providers: dict[str, object],
        order: list[str],
        circuit_breaker: CircuitBreaker,
        retries: int = 1,
        openai_traffic_percent: int = 100,
        shadow_qwen: bool = False,
    ):
        if "deterministic" not in providers:
            raise ValueError("The deterministic Copilot provider is required")
        self.providers = providers
        self.order = list(dict.fromkeys([*order, "deterministic"]))
        self.circuit_breaker = circuit_breaker
        self.retries = max(0, min(retries, 2))
        self.openai_traffic_percent = max(0, min(openai_traffic_percent, 100))
        self.shadow_qwen = shadow_qwen

    @property
    def provider_name(self) -> str:
        return "router"

    def _in_openai_canary(self, request_id: str) -> bool:
        bucket = int(hashlib.sha256(request_id.encode()).hexdigest()[:8], 16) % 100
        return bucket < self.openai_traffic_percent

    def _call(self, name: str, provider: object, context: CopilotContext) -> CopilotResult:
        attempts = self.retries + 1
        for attempt in range(attempts):
            try:
                return provider.assist(context)
            except (CopilotTimeout, CopilotRateLimitError, CopilotUnavailable):
                if attempt + 1 >= attempts:
                    raise
                delay = 0.2 * (2 ** attempt) + random.uniform(0.0, 0.15)
                time.sleep(delay)
        raise CopilotUnavailable(f"{name} exhausted retries")

    def assist(self, context: CopilotContext) -> CopilotResult:
        context = sanitize_context(context)
        failures: list[str] = []
        for name in self.order:
            provider = self.providers.get(name)
            if provider is None:
                failures.append(f"{name}:not_configured")
                continue
            if name == "openai" and not self._in_openai_canary(context.request_id):
                failures.append("openai:canary_bypass")
                continue
            if name == "openai" and "budget_hard_degrade" in context.complexity_signals:
                failures.append("openai:budget_guard")
                continue
            if name != "deterministic" and not self.circuit_breaker.allow(name):
                failures.append(f"{name}:circuit_open")
                continue
            try:
                result = self._call(name, provider, context)
            except CopilotError as error:
                if name != "deterministic":
                    self.circuit_breaker.failure(name)
                failures.append(f"{name}:{error.failure_type}")
                continue
            if name != "deterministic":
                self.circuit_breaker.success(name)
            if name == "openai" and self.shadow_qwen and self.providers.get("qwen"):
                started = time.perf_counter()
                try:
                    shadow = self.providers["qwen"].assist(context)
                    comparison = {"status": "completed", "openai_action": result.recommended_action,
                                  "qwen_action": shadow.recommended_action,
                                  "agreement": result.recommended_action == shadow.recommended_action,
                                  "latency_ms": round((time.perf_counter() - started) * 1000)}
                except CopilotError as error:
                    comparison = {"status": "unavailable", "failure_type": error.failure_type,
                                  "latency_ms": round((time.perf_counter() - started) * 1000)}
                result = result.model_copy(update={"shadow_comparison": comparison})
            if name == "deterministic":
                reason = ("ИИ-провайдер временно недоступен. Показана безопасная подсказка."
                          if failures else
                          "ИИ-помощник не настроен. Показана безопасная подсказка.")
                return result.model_copy(update={"fallback_reason": reason})
            return result
        raise RuntimeError("Deterministic Copilot provider did not return a result")

    def health(self) -> dict:
        provider_health = {}
        for name in ("openai", "qwen", "deterministic"):
            provider = self.providers.get(name)
            status = provider.health() if provider else {"configured": False, "status": "not_configured"}
            if name != "deterministic":
                status = {**status, "circuit": self.circuit_breaker.status(name)}
            provider_health[name] = status
        configured_order = [name for name in self.order if name in self.providers]
        active = configured_order[0] if configured_order else "deterministic"
        return {
            "status": "healthy",
            "active_provider": active,
            "model_tier": "dynamic",
            "fallbacks": configured_order[1:],
            "shadow_qwen": self.shadow_qwen and "qwen" in self.providers,
            "providers": provider_health,
        }

    def status(self) -> dict:
        return self.health()
