"""Decision providers and the Pulse triage contract."""

from __future__ import annotations

import os
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, Field

from laya_client import LayaClient, LayaError, LayaResponseError
from triage import HIGH_CONFIDENCE, MEDIUM_CONFIDENCE, SERVICE_NAMES, analyze


DecisionMode = Literal[
    "AUTO_PRESELECT", "VERIFY", "CLARIFY_OR_HUMAN_REVIEW", "MODEL_DISAGREEMENT"
]

CATEGORY_GUIDANCE = {
    "heating": "Нет отопления, холодные батареи / жылу жоқ, батарея суық",
    "water_supply": "Нет или слабая подача питьевой воды / ауыз су жоқ немесе қысымы төмен",
    "electricity": "Нет электричества, авария электросети / электр қуаты жоқ, желі апаты",
    "roads": "Ямы и повреждения проезжей части / жолдағы шұңқырлар мен зақым",
    "street_lighting": "Не работают уличные фонари / көше шамдары жанбайды",
    "waste_management": "Переполненные баки, не вывезен мусор / қоқыс жәшігі толы, қоқыс шығарылмаған",
    "public_transport": "Автобус, маршрут, остановка, интервал / автобус, бағыт, аялдама, аралық",
    "housing_maintenance": "Подъезд, крыша, лифт, общедомовое имущество / кіреберіс, шатыр, лифт, үй мүлкі",
    "landscaping": "Парк, деревья, газон, площадка / саябақ, ағаш, көгал, алаң",
    "sewerage": "Ливневая канализация, стоки, затопление / нөсер кәрізі, ағын су, су басу",
}


class Alternative(BaseModel):
    value: str
    confidence: float = Field(ge=0, le=1)
    provider: str


class ChoiceDecision(BaseModel):
    value: str | None
    confidence: float = Field(ge=0, le=1)
    alternatives: list[Alternative] = Field(default_factory=list)


class BooleanDecision(BaseModel):
    value: bool
    confidence: float = Field(ge=0, le=1)
    probability_true: float = Field(ge=0, le=1)


class TriageContract(BaseModel):
    category: ChoiceDecision
    needs_clarification: BooleanDecision
    spam_suspected: BooleanDecision
    urgency: ChoiceDecision
    language: str
    provider: str
    provider_version: str
    decision: DecisionMode
    latency_ms: int = 0
    triage_total_latency_ms: int | None = None
    verification: dict[str, Any] | None = None
    fallback_reason: str | None = None
    source_decisions: dict[str, Any] | None = None


class DecisionGate:
    def __init__(self, high: float = HIGH_CONFIDENCE, medium: float = MEDIUM_CONFIDENCE):
        if not 0 <= medium < high <= 1:
            raise ValueError("Confidence thresholds must satisfy 0 <= medium < high <= 1")
        self.high = high
        self.medium = medium

    def choose(self, confidence: float, needs_clarification: bool) -> DecisionMode:
        if needs_clarification or confidence < self.medium:
            return "CLARIFY_OR_HUMAN_REVIEW"
        if confidence < self.high:
            return "VERIFY"
        return "AUTO_PRESELECT"


class DecisionProvider(ABC):
    @abstractmethod
    def classify(self, complaint: dict[str, Any], extra_text: str = "",
                 baseline: dict[str, Any] | None = None) -> TriageContract:
        raise NotImplementedError

    @abstractmethod
    def verify(self, complaint: dict[str, Any], category: str, extra_text: str = "") -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def health(self) -> dict[str, Any]:
        raise NotImplementedError


class ExistingClassifierProvider(DecisionProvider):
    def __init__(self, classifier, topic_services: dict[str, str], gate: DecisionGate):
        self.classifier = classifier
        self.topic_services = topic_services
        self.gate = gate

    def classify(self, complaint, extra_text="", baseline=None) -> TriageContract:
        view = baseline or analyze(complaint, self.classifier, self.topic_services, extra_text)
        category = view["category"]
        confidence = float(view["category_confidence"])
        alternatives = [Alternative(value=item["category"], confidence=item["confidence"],
                                    provider="existing_classifier") for item in view["alternatives"]]
        needs = view["confidence_band"] == "low"
        return TriageContract(
            category=ChoiceDecision(value=category, confidence=confidence, alternatives=alternatives),
            needs_clarification=BooleanDecision(value=needs, confidence=1.0,
                                                probability_true=1.0 if needs else 0.0),
            spam_suspected=BooleanDecision(value=False, confidence=1.0, probability_true=0.0),
            urgency=ChoiceDecision(value=view["urgency"], confidence=view["urgency_confidence"]),
            language=complaint.get("language") or "unknown", provider="existing_classifier",
            provider_version="pulse-demo-rules-v1", decision=self.gate.choose(confidence, needs),
        )

    def verify(self, complaint, category, extra_text="") -> dict[str, Any]:
        return {"available": False, "reason": "existing classifier has no verification endpoint"}

    def health(self) -> dict[str, Any]:
        return {"status": "healthy", "provider": "existing_classifier"}


def _probability(value: Any, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= float(value) <= 1:
        raise LayaResponseError(f"Laya returned invalid probability for {field}")
    return float(value)


def _sanitized_text(text: str) -> str:
    text = re.sub(r"\b\d{12}\b", "[ИИН удалён]", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}", "[email удалён]", text)
    return re.sub(r"(?<!\d)(?:\+?7|8)[\s()-]*\d(?:[\s()-]*\d){9}(?!\d)", "[телефон удалён]", text)


def laya_triage_questions(criteria: dict[str, str]) -> dict[str, dict[str, Any]]:
    return {
        "category": {
            "type": "choice",
            "instructions": "Выбери одну основную категорию обращения гражданина.",
            "criteria": criteria,
        },
        "needs_clarification": {
            "type": "noul",
            "instructions": "Нужно ли запросить у гражданина уточнение, чтобы определить категорию?",
        },
        "spam_suspected": {
            "type": "noul",
            "instructions": "Похоже ли сообщение на спам, рекламу или злоупотребление?",
        },
        "urgency": {
            "type": "choice",
            "instructions": "Какой операционный приоритет рекомендуется?",
            "criteria": {"normal": "обычная обработка", "urgent": "возможна непосредственная опасность"},
        },
    }


class LayaDecisionProvider(DecisionProvider):
    def __init__(self, client: LayaClient, topics: list[dict[str, str]], gate: DecisionGate):
        self.client = client
        self.gate = gate
        self.criteria = {topic["id"]: CATEGORY_GUIDANCE.get(
            topic["id"], f"{topic['name_ru']} / {topic['name_kk']}") for topic in topics}
        self.allowed = set(self.criteria)

    @staticmethod
    def _state(complaint: dict[str, Any], extra_text: str) -> dict[str, str]:
        text = complaint["text"] + (("\n" + extra_text) if extra_text else "")
        return {"text": _sanitized_text(text), "language": complaint.get("language") or "unknown"}

    def classify(self, complaint, extra_text="", baseline=None) -> TriageContract:
        questions = laya_triage_questions(self.criteria)
        result = self.client.predict(self._state(complaint, extra_text), questions)
        category_answer = result.answers["category"]
        category = category_answer.get("choice")
        probabilities = category_answer.get("probabilities")
        if category not in self.allowed or not isinstance(probabilities, dict):
            raise LayaResponseError("Laya returned an unknown category")
        confidence = _probability(probabilities.get(category), "category")
        alternatives = sorted(
            (Alternative(value=value, confidence=_probability(probability, "category alternative"), provider="laya")
             for value, probability in probabilities.items() if value in self.allowed and value != category),
            key=lambda item: item.confidence, reverse=True,
        )[:3]
        needs_probability = _probability(result.answers["needs_clarification"].get("noul"),
                                         "needs_clarification")
        spam_probability = _probability(result.answers["spam_suspected"].get("noul"), "spam_suspected")
        urgency_answer = result.answers["urgency"]
        urgency = urgency_answer.get("choice")
        urgency_probs = urgency_answer.get("probabilities")
        if urgency not in {"normal", "urgent"} or not isinstance(urgency_probs, dict):
            raise LayaResponseError("Laya returned an unknown urgency")
        urgency_confidence = _probability(urgency_probs.get(urgency), "urgency")
        needs = needs_probability >= 0.5
        version = str(result.routing.get("repo") or result.routing.get("model") or result.model)
        return TriageContract(
            category=ChoiceDecision(value=category, confidence=confidence, alternatives=alternatives),
            needs_clarification=BooleanDecision(value=needs, confidence=max(needs_probability, 1 - needs_probability),
                                                probability_true=needs_probability),
            spam_suspected=BooleanDecision(value=spam_probability >= 0.5,
                                           confidence=max(spam_probability, 1 - spam_probability),
                                           probability_true=spam_probability),
            urgency=ChoiceDecision(value=urgency, confidence=urgency_confidence),
            language=complaint.get("language") or "unknown", provider="laya",
            provider_version=version, decision=self.gate.choose(confidence, needs),
            latency_ms=result.latency_ms,
        )

    def verify(self, complaint, category, extra_text="") -> dict[str, Any]:
        if category not in self.allowed:
            raise ValueError("Unknown verification category")
        questions = {"category_confirmed": {
            "type": "noul",
            "instructions": f"Относится ли обращение к категории {self.criteria[category]}?",
        }}
        result = self.client.predict(self._state(complaint, extra_text), questions)
        probability = _probability(result.answers["category_confirmed"].get("noul"), "verification")
        return {"available": True, "value": probability >= 0.5,
                "probability_true": probability, "confidence": max(probability, 1 - probability),
                "latency_ms": result.latency_ms}

    def health(self) -> dict[str, Any]:
        return self.client.health()


class DecisionService:
    MODES = {"mock", "existing_classifier", "laya", "hybrid"}

    def __init__(self, classifier, topic_services: dict[str, str], topics: list[dict[str, str]]):
        self.mode = os.environ.get("P109_DECISION_PROVIDER", "mock").strip().lower()
        if self.mode not in self.MODES:
            raise ValueError("P109_DECISION_PROVIDER must be mock, existing_classifier, laya, or hybrid")
        self.enable_hybrid = os.environ.get("P109_ENABLE_HYBRID", "1").lower() in {"1", "true", "yes", "on"}
        if self.mode == "hybrid" and not self.enable_hybrid:
            raise ValueError("P109_ENABLE_HYBRID must be enabled when P109_DECISION_PROVIDER=hybrid")
        self.gate = DecisionGate(
            float(os.environ.get("P109_HIGH_CONFIDENCE", HIGH_CONFIDENCE)),
            float(os.environ.get("P109_MEDIUM_CONFIDENCE", MEDIUM_CONFIDENCE)),
        )
        self.classifier = classifier
        self.topic_services = topic_services
        self.existing = ExistingClassifierProvider(classifier, topic_services, self.gate)
        self.laya = LayaDecisionProvider(LayaClient(
            os.environ.get("P109_LAYA_BASE_URL", "http://127.0.0.1:8000"),
            float(os.environ.get("P109_LAYA_TIMEOUT", "8")),
            os.environ.get("P109_LAYA_MODEL", "multilingual"),
            os.environ.get("P109_LAYA_API_KEY"),
        ), topics, self.gate)
        self.enable_verification = os.environ.get("P109_ENABLE_VERIFICATION", "1").lower() in {"1", "true", "yes", "on"}
        self.demo_fallback = os.environ.get("P109_LAYA_DEMO_FALLBACK", "0").lower() in {"1", "true", "yes", "on"}

    def _baseline(self, complaint, extra_text=""):
        return analyze(complaint, self.classifier, self.topic_services, extra_text)

    def classify(self, complaint: dict[str, Any], extra_text: str = "") -> dict[str, Any]:
        started = time.perf_counter()
        baseline = self._baseline(complaint, extra_text)
        existing = self.existing.classify(complaint, extra_text, baseline)
        if self.mode in {"mock", "existing_classifier"}:
            selected = existing.model_copy(update={"provider": self.mode})
        else:
            try:
                laya = self.laya.classify(complaint, extra_text)
                if self.mode == "hybrid":
                    sources = {
                        "existing_classifier": {"category": existing.category.value,
                                                "confidence": existing.category.confidence},
                        "laya": {"category": laya.category.value, "confidence": laya.category.confidence},
                    }
                    if existing.category.value and existing.category.value != laya.category.value:
                        selected = laya.model_copy(update={"provider": "hybrid", "decision": "MODEL_DISAGREEMENT",
                                                           "source_decisions": sources})
                    else:
                        selected = laya.model_copy(update={"provider": "hybrid", "source_decisions": sources})
                else:
                    selected = laya
                if selected.decision == "VERIFY" and self.enable_verification:
                    try:
                        verification = self.laya.verify(complaint, selected.category.value, extra_text)
                        if verification["value"] and verification["probability_true"] >= self.gate.high:
                            decision = "AUTO_PRESELECT"
                        elif not verification["value"]:
                            decision = "CLARIFY_OR_HUMAN_REVIEW"
                        else:
                            decision = "VERIFY"
                        selected = selected.model_copy(update={"verification": verification, "decision": decision})
                    except LayaError as error:
                        selected = selected.model_copy(update={"verification": {"available": False,
                                                                                "reason": str(error)}})
            except LayaError as error:
                provider = "demo_fallback" if self.demo_fallback else "existing_classifier"
                selected = existing.model_copy(update={"provider": provider, "fallback_reason": str(error)})
        view = self.view_from_contract(complaint, extra_text, selected.model_dump(mode="json"), baseline)
        view["triage_total_latency_ms"] = round((time.perf_counter() - started) * 1000)
        return view

    def existing_view(self, complaint: dict[str, Any], extra_text: str = "") -> dict[str, Any]:
        baseline = self._baseline(complaint, extra_text)
        contract = self.existing.classify(complaint, extra_text, baseline).model_copy(update={"provider": "mock"})
        return self.view_from_contract(complaint, extra_text, contract.model_dump(mode="json"), baseline)

    def view_from_contract(self, complaint: dict[str, Any], extra_text: str,
                           raw: dict[str, Any], baseline: dict[str, Any] | None = None) -> dict[str, Any]:
        contract = TriageContract.model_validate(raw)
        view = dict(baseline or self._baseline(complaint, extra_text))
        category = contract.category.value
        alternatives = [item.model_dump() for item in contract.category.alternatives]
        if category and not any(item["value"] == category for item in alternatives):
            alternatives.insert(0, {"value": category, "confidence": contract.category.confidence,
                                    "provider": contract.provider})
        if contract.decision == "MODEL_DISAGREEMENT" and contract.source_decisions:
            alternatives = []
            for provider, item in contract.source_decisions.items():
                if item.get("category") and not any(x["value"] == item["category"] for x in alternatives):
                    alternatives.append({"value": item["category"], "confidence": item["confidence"],
                                         "provider": provider})
        band = ("high" if contract.decision == "AUTO_PRESELECT" else
                "medium" if contract.decision in {"VERIFY", "MODEL_DISAGREEMENT"} else "low")
        service = self.topic_services.get(category)
        view.update({
            "mode": contract.provider, "training_status": "generic_base_not_pulse_evaluated"
            if contract.provider in {"laya", "hybrid"} else "not_trained",
            "checkpoint_id": contract.provider_version if contract.provider in {"laya", "hybrid"} else None,
            "confidence_kind": "laya_probability" if contract.provider in {"laya", "hybrid"} else "synthetic_demo",
            "category": category, "category_confidence": contract.category.confidence,
            "urgency": contract.urgency.value, "urgency_confidence": contract.urgency.confidence,
            "suggested_service": service,
            "service_name": SERVICE_NAMES.get(service, "Старший оператор") if complaint["region_id"] == "KZ-ALA"
            else "Региональная очередь — служба требует проверки",
            "confidence_band": band,
            "alternatives": [{"category": item["value"], "confidence": item["confidence"],
                              "provider": item["provider"]} for item in alternatives],
            "provider": contract.provider, "provider_version": contract.provider_version,
            "language": contract.language,
            "decision_mode": contract.decision,
            "needs_clarification": contract.needs_clarification.model_dump(),
            "spam_suspected": contract.spam_suspected.model_dump(),
            "verification": contract.verification, "fallback_reason": contract.fallback_reason,
            "source_decisions": contract.source_decisions,
            "laya_latency_ms": contract.latency_ms if contract.provider in {"laya", "hybrid"} else None,
            "triage_total_latency_ms": contract.triage_total_latency_ms,
            "verification_latency_ms": (contract.verification or {}).get("latency_ms"),
        })
        if band == "low" and not view.get("clarification_question"):
            view["clarification_question"] = "Уточните, что именно произошло и где находится проблема."
        return view

    @staticmethod
    def contract_from_view(view: dict[str, Any]) -> dict[str, Any]:
        return {
            "category": {"value": view["category"], "confidence": view["category_confidence"],
                         "alternatives": [{"value": item["category"], "confidence": item["confidence"],
                                           "provider": item.get("provider", view["provider"])}
                                          for item in view["alternatives"] if item["category"] != view["category"]]},
            "needs_clarification": view["needs_clarification"],
            "spam_suspected": view["spam_suspected"],
            "urgency": {"value": view["urgency"], "confidence": view["urgency_confidence"], "alternatives": []},
            "language": view.get("language", "unknown"), "provider": view["provider"],
            "provider_version": view["provider_version"], "decision": view["decision_mode"],
            "latency_ms": view.get("laya_latency_ms") or 0,
            "triage_total_latency_ms": view.get("triage_total_latency_ms"),
            "verification": view.get("verification"),
            "fallback_reason": view.get("fallback_reason"), "source_decisions": view.get("source_decisions"),
        }

    def audit_payload(self, view: dict[str, Any]) -> dict[str, Any]:
        contract = self.contract_from_view(view)
        return {
            "contract_version": "pulse-triage-v1", "provider": view["provider"],
            "provider_version": view["provider_version"], "question_type": "triage",
            "suggested_category": view["category"], "confidence": view["category_confidence"],
            "alternatives": view["alternatives"], "decision_mode": view["decision_mode"],
            "operator_override": None, "confirmed_category": None,
            "latency_ms": view.get("triage_total_latency_ms", 0),
            "laya_latency_ms": view.get("laya_latency_ms"),
            "verification_latency_ms": view.get("verification_latency_ms"),
            "language": view.get("language", "unknown"), "triage_contract": contract,
        }

    def health(self) -> dict[str, Any]:
        if self.mode not in {"laya", "hybrid"}:
            return {"status": "disabled"}
        return self.laya.health()
