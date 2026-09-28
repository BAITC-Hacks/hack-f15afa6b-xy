"""Verify the tracked multilingual E5 GPU evidence."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from similarity import SimilarityClient
from triage import related_cases


path = root / "training/evidence/similarity-e5-gpu-20260928.json"
data = json.loads(path.read_text())
assert data["schema_version"] == "pulse109-similarity-evidence-v1" and data["status"] == "verified"
assert data["training_commit"].startswith("744dd5a") and data["data_status"] == "synthetic_only"
assert len(data["runs"]) == 3 and len({run["checkpoint_sha256"] for run in data["runs"]}) == 3
selected = next(run for run in data["runs"] if run["name"] == data["selection"]["selected_run"])
assert selected["validation"]["ndcg_at_10"] == max(run["validation"]["ndcg_at_10"] for run in data["runs"])
assert selected["checkpoint_sha256"] == data["selection"]["checkpoint_sha256"]
for scope in ("test", "cross_language_test"):
    for metric in ("recall_at_5", "ndcg_at_10", "mrr"):
        assert data["trained"][scope][metric] >= data["baseline"][scope][metric]
assert data["weight_change_proof"] is True and "not production accuracy" in data["claim_boundary"]
unavailable = SimilarityClient("http://127.0.0.1:1", "a" * 64, timeout=.05).status()
assert unavailable["configured"] is True and unavailable["status"] == "unavailable"
assert unavailable["mode"] == "lexical_fallback"
with patch("similarity.urllib.request.urlopen", side_effect=ConnectionResetError):
    assert SimilarityClient("http://127.0.0.1:1", "a" * 64).status()["mode"] == "lexical_fallback"


class TrainedStub:
    checkpoint_id = "b" * 64

    def rank(self, query, candidates):
        scores = {"cross-language": .88, "classifier-wrong": .86, "different-problem": .50}
        return [{**row, "similarity": scores[row["id"]]} for row in candidates]


class UnavailableStub:
    checkpoint_id = "c" * 64

    def rank(self, query, candidates):
        raise RuntimeError("offline")


target = {
    "id": "target", "text": "На Абая 44 нет воды во всем доме", "address": "Абая 44",
    "region_id": "KZ-ALA", "district": "Алмалинский", "received_at": "2026-09-28T10:00:00+00:00",
    "ingested_at": "2026-09-28T10:00:00+00:00", "resolved_at": None, "quarantined": 0,
    "incident_dismissed": 0,
}
base = {"region_id": "KZ-ALA", "district": "Алмалинский", "received_at": "2026-09-28T10:20:00+00:00",
        "ingested_at": "2026-09-28T10:20:00+00:00", "resolved_at": None, "quarantined": 0,
        "incident_id": "INC-204", "proposed_topic": None}
rows = [
    {**base, "id": "cross-language", "text": "Абай 46 үйде су жоқ, бүкіл үй сусыз", "address": "Абая 46",
     "topic": "water_supply"},
    {**base, "id": "classifier-wrong", "text": "Абай 44 үйде су жоқ", "address": "Абая 44", "topic": "roads"},
    {**base, "id": "different-problem", "text": "На Абая 44 не работает уличный фонарь", "address": "Абая 44",
     "topic": "street_lighting", "incident_id": "INC-OTHER"},
    {**base, "id": "other-region", "text": "Абай 44 үйде су жоқ", "address": "Абая 44", "topic": "water_supply",
     "region_id": "KZ-AST"},
]
ai = {"category": "water_supply", "extracted_address": "Абая 44"}
found, scoring = related_cases(target, ai, rows, lambda _: (None, None, None), {}, TrainedStub())
assert [item["id"] for item in found] == ["classifier-wrong", "cross-language"], found
assert all(item["requires_human_confirmation"] for item in found)
assert scoring == {"mode": "trained", "checkpoint_id": "b" * 64,
                   "candidate_pool": 3, "human_confirmation_required": True}
assert found[0]["scoring_mode"] == "trained" and found[0]["checkpoint_id"] == "b" * 64
assert "подтверждает оператор" in found[0]["reason"]
without_topic, _ = related_cases(target, {**ai, "category": None}, rows,
                                 lambda _: (None, None, None), {}, TrainedStub())
assert {item["id"] for item in without_topic} == {"classifier-wrong", "cross-language"}

fallback, fallback_scoring = related_cases(target, ai, rows[:1], lambda _: (None, None, None), {}, None)
assert [item["id"] for item in fallback] == ["cross-language"]
assert fallback_scoring["mode"] == "lexical_fallback" and fallback[0]["checkpoint_id"] is None
degraded, degraded_scoring = related_cases(target, ai, rows[:1], lambda _: (None, None, None), {}, UnavailableStub())
assert degraded == fallback and degraded_scoring["mode"] == "lexical_fallback"
print("PASS: trained/fallback duplicate candidates include RU↔KK and classifier misses, reject a different incident")
print("PASS: 3 GPU runs, validation-only selection, weight change and improved held-out retrieval metrics")
