"""Fine-tune multilingual E5 for complaint retrieval and record reproducible evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path


BASE_MODEL = "intfloat/multilingual-e5-small"
BASE_REVISION = "fd1525a9fd15316a2d503bf26ab031a61d056e98"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_rows(data_dir: Path, split: str) -> list[dict]:
    path = data_dir / f"{split}.jsonl"
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("gold", {}).get("category", {}).get("label") and row.get("state", {}).get("text"):
                rows.append({"id": row["id"], "group_id": row["group_id"], "language": row["language"],
                             "text": row["state"]["text"], "topic": row["gold"]["category"]["label"]})
    return rows


def positive_pairs(rows: list[dict], seed: int) -> list[tuple[str, str]]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["group_id"], []).append(row)
    pairs = []
    for row in rows:
        choices = [item for item in groups[row["group_id"]] if item["id"] != row["id"]]
        if not choices:
            continue
        cross_language = [item for item in choices if item["language"] != row["language"]]
        choices = cross_language or choices
        choices.sort(key=lambda item: item["id"])
        partner = choices[int(hashlib.sha256(f"{seed}:{row['id']}".encode()).hexdigest(), 16) % len(choices)]
        pairs.append((row["text"], partner["text"]))
    return pairs


def state_hash(model) -> str:
    digest = hashlib.sha256()
    for name, value in model.state_dict().items():
        digest.update(name.encode()); digest.update(value.detach().cpu().contiguous().numpy().tobytes())
    return digest.hexdigest()


def embed(model, tokenizer, texts: list[str], prefix: str, device, batch_size=64):
    import torch
    import torch.nn.functional as functional

    result = []
    model.eval()
    with torch.no_grad():
        for start in range(0, len(texts), batch_size):
            batch = tokenizer([prefix + text for text in texts[start:start + batch_size]], padding=True,
                              truncation=True, max_length=256, return_tensors="pt")
            batch = {key: value.to(device) for key, value in batch.items()}
            hidden = model(**batch).last_hidden_state
            mask = batch["attention_mask"].unsqueeze(-1)
            pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1)
            result.append(functional.normalize(pooled, p=2, dim=1).cpu())
    return torch.cat(result)


def retrieval_metrics(model, tokenizer, rows: list[dict], device, cross_language=False) -> dict:
    import torch

    queries = embed(model, tokenizer, [row["text"] for row in rows], "query: ", device)
    documents = embed(model, tokenizer, [row["text"] for row in rows], "passage: ", device)
    similarities = queries @ documents.T
    recalls, ndcgs, reciprocals, evaluated = [], [], [], 0
    for index, row in enumerate(rows):
        relevant = {i for i, other in enumerate(rows) if i != index and other["group_id"] == row["group_id"]
                    and (not cross_language or other["language"] != row["language"])}
        allowed = [i for i, other in enumerate(rows) if i != index and (not cross_language or other["language"] != row["language"])]
        if not relevant:
            continue
        ranked = sorted(allowed, key=lambda i: float(similarities[index, i]), reverse=True)
        recalls.append(len(relevant & set(ranked[:5])) / len(relevant))
        gains = [1 / math.log2(rank + 2) for rank, item in enumerate(ranked[:10]) if item in relevant]
        ideal = sum(1 / math.log2(rank + 2) for rank in range(min(10, len(relevant))))
        ndcgs.append(sum(gains) / ideal)
        first = next((rank + 1 for rank, item in enumerate(ranked) if item in relevant), None)
        reciprocals.append(1 / first if first else 0); evaluated += 1
    mean = lambda values: round(sum(values) / len(values), 4) if values else None
    return {"queries": evaluated, "recall_at_5": mean(recalls), "ndcg_at_10": mean(ndcgs), "mrr": mean(reciprocals)}


def train(args) -> dict:
    import torch
    import torch.nn.functional as functional
    from transformers import AutoModel, AutoTokenizer

    if not torch.cuda.is_available():
        raise SystemExit("CUDA GPU is required")
    random.seed(args.seed); torch.manual_seed(args.seed); torch.cuda.manual_seed_all(args.seed)
    device = torch.device("cuda")
    train_rows = load_rows(args.data_dir, "train")
    validation_rows = load_rows(args.data_dir, "validation")
    test_rows = load_rows(args.data_dir, "test")
    pairs = positive_pairs(train_rows, args.seed)
    if len(pairs) < args.batch_size:
        raise SystemExit("Training data does not contain enough positive pairs")
    tokenizer = AutoTokenizer.from_pretrained(args.model, revision=args.revision)
    model = AutoModel.from_pretrained(args.model, revision=args.revision).to(device)
    base_hash = state_hash(model)
    baseline = {"validation": retrieval_metrics(model, tokenizer, validation_rows, device),
                "test": retrieval_metrics(model, tokenizer, test_rows, device),
                "cross_language_test": retrieval_metrics(model, tokenizer, test_rows, device, True)}
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate)
    scaler = torch.amp.GradScaler("cuda")
    history = []
    for epoch in range(args.epochs):
        random.Random(args.seed + epoch).shuffle(pairs)
        model.train(); losses = []
        for start in range(0, len(pairs) - args.batch_size + 1, args.batch_size):
            batch = pairs[start:start + args.batch_size]
            left = tokenizer(["query: " + item[0] for item in batch], padding=True, truncation=True,
                             max_length=256, return_tensors="pt")
            right = tokenizer(["passage: " + item[1] for item in batch], padding=True, truncation=True,
                              max_length=256, return_tensors="pt")
            left, right = ({k: v.to(device) for k, v in left.items()}, {k: v.to(device) for k, v in right.items()})
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast(device_type="cuda", dtype=torch.float16):
                def pooled(inputs):
                    hidden = model(**inputs).last_hidden_state
                    mask = inputs["attention_mask"].unsqueeze(-1)
                    return functional.normalize((hidden * mask).sum(1) / mask.sum(1).clamp(min=1), p=2, dim=1)
                first, second = pooled(left), pooled(right)
                labels = torch.arange(len(batch), device=device)
                logits = first @ second.T / args.temperature
                loss = (functional.cross_entropy(logits, labels) + functional.cross_entropy(logits.T, labels)) / 2
            scaler.scale(loss).backward(); scaler.step(optimizer); scaler.update(); losses.append(float(loss))
        validation = retrieval_metrics(model, tokenizer, validation_rows, device)
        history.append({"epoch": epoch + 1, "mean_loss": round(sum(losses) / len(losses), 6), **validation})
    trained_hash = state_hash(model)
    trained = {"validation": retrieval_metrics(model, tokenizer, validation_rows, device),
               "test": retrieval_metrics(model, tokenizer, test_rows, device),
               "cross_language_test": retrieval_metrics(model, tokenizer, test_rows, device, True)}
    args.output.mkdir(parents=True, exist_ok=True)
    checkpoint = args.output / "checkpoint"; model.save_pretrained(checkpoint, safe_serialization=True); tokenizer.save_pretrained(checkpoint)
    metrics = {"baseline": baseline, "trained": trained, "history": history}
    (args.output / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n")
    manifest = {
        "schema_version": "pulse109-similarity-run-v1", "status": "complete",
        "created_at": datetime.now(timezone.utc).isoformat(), "seed": args.seed,
        "base_model": args.model, "base_revision": args.revision,
        "data_status": "synthetic_only", "train_pairs": len(pairs),
        "hyperparameters": {"epochs": args.epochs, "batch_size": args.batch_size,
                            "learning_rate": args.learning_rate, "temperature": args.temperature},
        "data_hashes": {f"{split}.jsonl": sha256(args.data_dir / f"{split}.jsonl") for split in ("train", "validation", "test")},
        "base_weights_sha256": base_hash, "trained_weights_sha256": trained_hash,
        "weight_change_proof": base_hash != trained_hash,
        "checkpoint_sha256": sha256(checkpoint / "model.safetensors"),
        "metrics_sha256": sha256(args.output / "metrics.json"),
        "environment": {"pytorch": torch.__version__, "cuda": torch.version.cuda,
                        "gpus": [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]},
        "claim_boundary": "Synthetic-only training proof; metrics do not establish production accuracy.",
    }
    (args.output / "run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def self_check() -> None:
    rows = [
        {"id": "a-ru", "group_id": "a", "language": "ru", "text": "нет воды"},
        {"id": "a-kk", "group_id": "a", "language": "kk", "text": "су жоқ"},
        {"id": "b-ru", "group_id": "b", "language": "ru", "text": "яма"},
        {"id": "b-kk", "group_id": "b", "language": "kk", "text": "шұңқыр"},
    ]
    pairs = positive_pairs(rows, 7)
    assert len(pairs) == 4 and ("нет воды", "су жоқ") in pairs and ("яма", "шұңқыр") in pairs
    print("PASS: group-safe RU/KK positive pair builder")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model", default=BASE_MODEL)
    parser.add_argument("--revision", default=BASE_REVISION)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--temperature", type=float, default=.05)
    parser.add_argument("--seed", type=int, default=20260928)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
    elif not args.data_dir or not args.output:
        parser.error("--data-dir and --output are required")
    else:
        print(json.dumps(train(args), ensure_ascii=False, indent=2))
