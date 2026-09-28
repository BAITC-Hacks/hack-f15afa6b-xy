"""Optional trained complaint retrieval with a deterministic local fallback."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request


def _tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[\w]+", text.lower()) if len(word) > 2}


def lexical_score(query: str, candidate: str, same_topic: bool) -> float:
    left, right = _tokens(query), _tokens(candidate)
    overlap = len(left & right) / max(1, len(left | right))
    return round(min(1.0, overlap + (0.18 if same_topic else 0)), 4)


class SimilarityClient:
    def __init__(self, base_url: str, checkpoint_id: str, timeout: float = 8):
        if not re.fullmatch(r"[0-9a-f]{64}", checkpoint_id):
            raise ValueError("P109_SIMILARITY_CHECKPOINT_ID must be a 64-character SHA-256")
        self.base_url = base_url.rstrip("/")
        self.url = self.base_url + "/rank"
        self.checkpoint_id = checkpoint_id
        self.timeout = timeout

    def status(self) -> dict:
        result = {"configured": True, "mode": "lexical_fallback", "status": "unavailable",
                  "checkpoint_id": self.checkpoint_id}
        try:
            with urllib.request.urlopen(self.base_url + "/health", timeout=min(self.timeout, 1)) as response:
                data = json.loads(response.read())
            if data.get("status") == "ok" and data.get("checkpoint_id") == self.checkpoint_id:
                result.update({"mode": "trained", "status": "healthy"})
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, AttributeError):
            pass
        return result

    @classmethod
    def from_env(cls):
        base_url = os.environ.get("P109_SIMILARITY_URL")
        checkpoint = os.environ.get("P109_SIMILARITY_CHECKPOINT_ID")
        return cls(base_url, checkpoint, float(os.environ.get("P109_SIMILARITY_TIMEOUT", "8"))) if base_url and checkpoint else None

    def rank(self, query: str, candidates: list[dict]) -> list[dict]:
        body = json.dumps({"query": query, "candidates": [{"id": row["id"], "text": row["text"]} for row in candidates]}).encode()
        request = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise RuntimeError("Similarity service unavailable") from error
        scores = data.get("scores")
        allowed = {row["id"] for row in candidates}
        if not isinstance(scores, list) or any(
            not isinstance(item, dict) or item.get("id") not in allowed
            or isinstance(item.get("score"), bool) or not isinstance(item.get("score"), (int, float))
            or not -1 <= item["score"] <= 1 for item in scores
        ):
            raise RuntimeError("Similarity service returned an invalid response")
        by_id = {item["id"]: float(item["score"]) for item in scores}
        return [{**row, "similarity": round(by_id[row["id"]], 4)} for row in candidates if row["id"] in by_id]


def rank_candidates(query: str, topic: str | None, candidates: list[dict], client: SimilarityClient | None, limit: int):
    if client:
        try:
            ranked = client.rank(query, candidates)
            return sorted(ranked, key=lambda row: (-row["similarity"], row["id"]))[:limit], "trained", client.checkpoint_id
        except RuntimeError:
            pass
    ranked = [
        {**row, "similarity": lexical_score(query, row["text"], bool(topic and row.get("topic") == topic))}
        for row in candidates
    ]
    return sorted(ranked, key=lambda row: (-row["similarity"], row["id"]))[:limit], "lexical_fallback", None
