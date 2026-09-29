"""Always-available Copilot provider built from verified Pulse facts."""

from __future__ import annotations

from copilot_contracts import (
    CopilotContext,
    CopilotOutput,
    CopilotResult,
    CopilotUsage,
    make_result_id,
    validate_copilot_output,
)


class DeterministicCopilot:
    @property
    def provider_name(self) -> str:
        return "deterministic"

    def health(self) -> dict:
        return {
            "configured": True,
            "status": "healthy",
            "mode": "deterministic_fallback",
        }

    def assist(self, context: CopilotContext) -> CopilotResult:
        output = validate_copilot_output(
            CopilotOutput(
                summary=context.fallback_summary,
                reasoning=context.fallback_reasoning,
                suggested_reply=context.fallback_reply,
                clarification_question=context.fallback_question,
                recommended_action=context.fallback_action,
            ),
            context,
        )
        return CopilotResult(
            **output.model_dump(),
            provider=self.provider_name,
            model=None,
            model_revision=None,
            latency_ms=0,
            result_id=make_result_id(output, self.provider_name, None, None, context.request_id),
            request_id=context.request_id,
            mode="deterministic_fallback",
            available=True,
            usage=CopilotUsage(),
        )
