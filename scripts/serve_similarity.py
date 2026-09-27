"""Private exact-search service for a fine-tuned Pulse 109 embedding model."""

from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


class Candidate(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=10000)


class RankRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10000)
    candidates: list[Candidate] = Field(min_length=1, max_length=100)


model = tokenizer = device = None
checkpoint_id = os.environ.get("P109_SIMILARITY_CHECKPOINT_ID", "")


def embeddings(texts: list[str], prefix: str):
    import torch
    import torch.nn.functional as functional

    batch = tokenizer([prefix + text for text in texts], padding=True, truncation=True,
                      max_length=256, return_tensors="pt")
    batch = {key: value.to(device) for key, value in batch.items()}
    with torch.no_grad():
        hidden = model(**batch).last_hidden_state
        mask = batch["attention_mask"].unsqueeze(-1)
        return functional.normalize((hidden * mask).sum(1) / mask.sum(1).clamp(min=1), p=2, dim=1)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global model, tokenizer, device
    import torch
    from transformers import AutoModel, AutoTokenizer

    path = os.environ.get("P109_SIMILARITY_MODEL_PATH")
    if not path or not re.fullmatch(r"[0-9a-f]{64}", checkpoint_id):
        raise RuntimeError("Model path and 64-character checkpoint SHA-256 are required")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
    model = AutoModel.from_pretrained(path, local_files_only=True).to(device).eval()
    yield


app = FastAPI(title="Pulse 109 Similarity", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "model": "multilingual-e5-small-tuned", "checkpoint_id": checkpoint_id,
            "device": str(device)}


@app.post("/rank")
def rank(request: RankRequest):
    if len({item.id for item in request.candidates}) != len(request.candidates):
        raise HTTPException(422, "Candidate ids must be unique")
    query = embeddings([request.query], "query: ")[0]
    passages = embeddings([item.text for item in request.candidates], "passage: ")
    scores = query @ passages.T
    ranked = sorted(zip(request.candidates, scores.tolist()), key=lambda item: (-item[1], item[0].id))
    return {"checkpoint_id": checkpoint_id,
            "scores": [{"id": item.id, "score": round(score, 6)} for item, score in ranked]}
