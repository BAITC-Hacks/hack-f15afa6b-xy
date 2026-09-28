"""Verify the tracked multilingual E5 GPU evidence."""

import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from similarity import SimilarityClient


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
print("PASS: 3 GPU runs, validation-only selection, weight change and improved held-out retrieval metrics")
