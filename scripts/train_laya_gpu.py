"""Fine-tune, calibrate and evaluate Laya on an NVIDIA GPU with evidence artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import random
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
import torch.distributed as dist
from safetensors.torch import load_file, save_file
from torch.nn.parallel import DistributedDataParallel as DDP
from transformers import AutoTokenizer
from huggingface_hub import snapshot_download

from laya.agent import _fix_tokenizer_config
from laya.common import QTYPES, build_model, build_sequence, proper_reward, render_options, temp_bucket

DEFAULT_MODEL_REVISION = "e4e9ddf21a7b1903b7acffd8814ad4307bf63a67"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_revision(path):
    resolved = Path(path).resolve()
    return resolved.name if resolved.parent.name == "snapshots" else None


def read_jsonl(path):
    with Path(path).open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def build_item(tokenizer, config, record, qid, question, gold):
    qtype = question["type"]
    criteria = question.get("criteria", {})
    if qtype == "choice":
        keys = list(criteria)
    elif qtype == "noul":
        keys = ["false", "true"]
    elif qtype == "score":
        keys = [str(i) for i in range(len(criteria) if isinstance(criteria, list) else 4)]
    else:
        raise ValueError(f"Unsupported question type: {qtype}")
    target = [float(gold["probabilities"].get(key, 0)) for key in keys]
    total = sum(target)
    if total <= 0:
        raise ValueError(f"{record['id']}:{qid} has no target probability")
    target = [value / total for value in target]
    internal = {"t": qtype, "ins": question["instructions"], "crit": criteria}
    sequence, markers = build_sequence(tokenizer, record["state"], internal,
                                       config["max_len"], config["head_max_len"])
    if len(markers) != len(render_options(internal)):
        raise ValueError(f"{record['id']}:{qid} option markers were truncated")
    return {
        "ids": sequence, "markers": markers, "qtype": QTYPES[qtype], "qtype_name": qtype,
        "target": target, "label": int(np.argmax(target)), "qid": qid,
        "language": record["language"], "record_id": record["id"],
        "group_id": record["group_id"], "keys": keys,
    }


def load_items(path, tokenizer, config):
    items = []
    for record in read_jsonl(path):
        for qid, question in record["questions"].items():
            items.append(build_item(tokenizer, config, record, qid, question, record["gold"][qid]))
    return items


def collate(items, pad_id):
    rows, length = len(items), max(len(item["ids"]) for item in items)
    options = max(len(item["markers"]) for item in items)
    ids = torch.full((rows, length), pad_id, dtype=torch.long)
    attention = torch.zeros((rows, length), dtype=torch.long)
    marker_pos = torch.zeros((rows, options), dtype=torch.long)
    marker_mask = torch.zeros((rows, options), dtype=torch.bool)
    target = torch.zeros((rows, options), dtype=torch.float32)
    for index, item in enumerate(items):
        ids[index, :len(item["ids"])] = torch.tensor(item["ids"])
        attention[index, :len(item["ids"])] = 1
        count = len(item["markers"])
        marker_pos[index, :count] = torch.tensor(item["markers"])
        marker_mask[index, :count] = True
        target[index, :count] = torch.tensor(item["target"])
    return {"input_ids": ids, "attention_mask": attention, "marker_pos": marker_pos,
            "marker_mask": marker_mask, "target": target,
            "qtype": torch.tensor([item["qtype"] for item in items])}


def forward_rows(model, items, pad_id, device, batch_size, amp_dtype):
    model.eval()
    rows = []
    with torch.no_grad():
        for start in range(0, len(items), batch_size):
            chunk = items[start:start + batch_size]
            batch = collate(chunk, pad_id)
            with torch.autocast("cuda", dtype=amp_dtype):
                logits, _ = model(batch["input_ids"].to(device), batch["attention_mask"].to(device),
                                  batch["marker_pos"].to(device), batch["marker_mask"].to(device),
                                  batch["qtype"].to(device))
            values = logits.float().cpu().numpy()
            for index, item in enumerate(chunk):
                count = len(item["target"])
                rows.append({key: item[key] for key in (
                    "qid", "language", "record_id", "group_id", "qtype", "qtype_name", "label", "keys")})
                rows[-1].update({"logits": values[index, :count].tolist(), "target": item["target"]})
    model.train()
    return rows


def fit_temperature(rows, minimum=10):
    if len(rows) < minimum:
        return 1.0
    width = max(len(row["logits"]) for row in rows)
    logits = torch.full((len(rows), width), -1e4)
    targets = torch.zeros((len(rows), width))
    for index, row in enumerate(rows):
        count = len(row["logits"])
        logits[index, :count] = torch.tensor(row["logits"])
        targets[index, :count] = torch.tensor(row["target"])
    log_temperature = torch.zeros(1, requires_grad=True)
    optimizer = torch.optim.LBFGS([log_temperature], lr=.1, max_iter=100)

    def closure():
        optimizer.zero_grad()
        loss = -(targets * torch.log_softmax(logits / log_temperature.exp(), -1)).sum(-1).mean()
        loss.backward()
        return loss

    optimizer.step(closure)
    value = float(torch.clamp(log_temperature.exp(), .5, 5).item())
    return round(value, 6) if math.isfinite(value) else 1.0


def temperature_map(rows):
    by_type, by_bucket, by_decision, by_language = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    for row in rows:
        by_type[row["qtype_name"]].append(row)
        bucket = temp_bucket(row["qtype"], len(row["logits"]))
        by_bucket[bucket].append(row)
        by_decision[f"{row['qid']}:{row['language']}"].append(row)
        by_language[(row["language"], bucket)].append(row)
    return {
        "by_type": {key: fit_temperature(value) for key, value in sorted(by_type.items())},
        "by_option_count": {key: fit_temperature(value) for key, value in sorted(by_bucket.items())},
        "by_decision_language": {key: fit_temperature(value) for key, value in sorted(by_decision.items())},
        "by_language_option_count": {
            language: {bucket: fit_temperature(value)
                       for (lang, bucket), value in sorted(by_language.items()) if lang == language}
            for language in sorted({key[0] for key in by_language})
        },
    }


def row_temperature(row, calibration, mode):
    if mode == "pulse":
        return calibration["by_decision_language"].get(f"{row['qid']}:{row['language']}", 1.0)
    if mode == "checkpoint":
        return calibration["by_option_count"].get(temp_bucket(row["qtype"], len(row["logits"])), 1.0)
    return 1.0


def evaluate(rows, calibration=None, mode="raw"):
    correct, confidences, briers, losses = [], [], [], []
    category_pairs = []
    per_slice = defaultdict(list)
    for row in rows:
        temperature = row_temperature(row, calibration or {}, mode)
        logits = np.asarray(row["logits"], dtype=float) / temperature
        probabilities = np.exp(logits - logits.max())
        probabilities /= probabilities.sum()
        target = np.asarray(row["target"], dtype=float)
        predicted, actual = int(probabilities.argmax()), int(target.argmax())
        hit = int(predicted == actual)
        confidence = float(probabilities[predicted])
        correct.append(hit); confidences.append(confidence)
        briers.append(float(np.square(probabilities - target).sum()))
        losses.append(float(-(target * np.log(np.clip(probabilities, 1e-12, 1))).sum()))
        per_slice[f"{row['qid']}:{row['language']}"].append(hit)
        if row["qid"] == "category":
            category_pairs.append((row["keys"][actual], row["keys"][predicted]))
    ece = 0.0
    for lower in np.linspace(0, .9, 10):
        selected = [i for i, confidence in enumerate(confidences) if lower <= confidence < lower + .1 + 1e-12]
        if selected:
            ece += len(selected) / len(rows) * abs(np.mean([correct[i] for i in selected])
                                                   - np.mean([confidences[i] for i in selected]))
    labels = sorted({value for pair in category_pairs for value in pair})
    f1s = []
    for label in labels:
        tp = sum(actual == predicted == label for actual, predicted in category_pairs)
        fp = sum(actual != label and predicted == label for actual, predicted in category_pairs)
        fn = sum(actual == label and predicted != label for actual, predicted in category_pairs)
        precision = tp / (tp + fp) if tp + fp else 0
        recall = tp / (tp + fn) if tp + fn else 0
        f1s.append(2 * precision * recall / (precision + recall) if precision + recall else 0)
    return {
        "decisions": len(rows), "accuracy": round(float(np.mean(correct)), 6),
        "category_accuracy": round(float(np.mean([a == b for a, b in category_pairs])), 6) if category_pairs else None,
        "category_macro_f1": round(float(np.mean(f1s)), 6) if f1s else None,
        "ece": round(float(ece), 6), "brier": round(float(np.mean(briers)), 6),
        "nll": round(float(np.mean(losses)), 6),
        "slices": {key: {"count": len(value), "accuracy": round(float(np.mean(value)), 6)}
                   for key, value in sorted(per_slice.items())},
    }


def git_value(root, *args):
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    return result.stdout.strip() if result.returncode == 0 else None


def environment_info():
    gpus = [{"index": index, "name": torch.cuda.get_device_name(index),
             "memory_bytes": torch.cuda.get_device_properties(index).total_memory,
             "capability": list(torch.cuda.get_device_capability(index))}
            for index in range(torch.cuda.device_count())]
    command = ["nvidia-smi", "--query-gpu=name,uuid,driver_version,memory.total", "--format=csv,noheader"]
    result = subprocess.run(command, text=True, capture_output=True) if shutil.which("nvidia-smi") else None
    import laya
    return {"python": sys.version, "platform": platform.platform(), "torch": torch.__version__,
            "cuda_runtime": torch.version.cuda, "laya": laya.__version__, "gpus": gpus,
            "nvidia_smi": result.stdout.strip().splitlines() if result and result.returncode == 0 else []}


def save_checkpoint(model, tokenizer, config, output):
    output.mkdir(parents=True, exist_ok=True)
    save_file({key: value.detach().half().contiguous().cpu()
               for key, value in model.state_dict().items()}, output / "model.safetensors")
    model.encoder.config.save_pretrained(output / "encoder")
    tokenizer.save_pretrained(output / "tokenizer")
    (output / "rl_agent_config.json").write_text(json.dumps(config, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="convaiinnovations/laya-multilingual")
    parser.add_argument("--model-revision", default=DEFAULT_MODEL_REVISION)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--micro-batch", type=int, default=8)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--encoder-lr", type=float, default=2.5e-5)
    parser.add_argument("--head-lr", type=float, default=1e-4)
    parser.add_argument("--group-size", type=int, default=4)
    parser.add_argument("--max-train-items", type=int)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        rows = [{"qid": "category", "language": "ru", "qtype": 0, "qtype_name": "choice",
                 "label": 0, "keys": ["a", "b"], "logits": [3.0, 0.0], "target": [1.0, 0.0]}] * 12
        calibration = temperature_map(rows)
        assert evaluate(rows, calibration, "pulse")["accuracy"] == 1.0
        print("PASS: calibration and metric self-check")
        return
    if not torch.cuda.is_available():
        raise SystemExit("CUDA is required; no NVIDIA GPU is visible")
    if args.epochs < 1 or args.micro_batch < 1 or args.grad_accum < 1:
        raise SystemExit("epochs, micro-batch and grad-accum must be positive")

    world_size = int(os.environ.get("WORLD_SIZE", "1"))
    rank = int(os.environ.get("RANK", "0"))
    local_rank = int(os.environ.get("LOCAL_RANK", "0"))
    if world_size > 1:
        dist.init_process_group("nccl")
    torch.cuda.set_device(local_rank)
    device = torch.device("cuda", local_rank)
    random.seed(args.seed + rank); np.random.seed(args.seed + rank); torch.manual_seed(args.seed + rank)
    torch.cuda.manual_seed_all(args.seed + rank)
    started = datetime.now(timezone.utc).isoformat()

    model_dir = Path(args.model) if Path(args.model).exists() else Path(snapshot_download(
        args.model, revision=args.model_revision,
        allow_patterns=["rl_agent_config.json", "model.safetensors", "tokenizer/*", "encoder/*"]))
    _fix_tokenizer_config(str(model_dir))
    config = json.loads((model_dir / "rl_agent_config.json").read_text())
    config.update({"gradient_checkpointing": True, "max_tokens_per_batch": 4096,
                   "max_len": 1024, "head_max_len": 256})
    tokenizer = AutoTokenizer.from_pretrained(model_dir / "tokenizer")
    model = build_model(config, encoder_dir=str(model_dir / "encoder"), pretrained=False)
    model.load_state_dict(load_file(model_dir / "model.safetensors"), strict=True)
    model.encoder.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.head_checkpointing = True
    model.to(device)
    amp_dtype = torch.float16

    train_items = load_items(args.data_dir / "train.jsonl", tokenizer, config)
    validation_items = load_items(args.data_dir / "validation.jsonl", tokenizer, config)
    calibration_items = load_items(args.data_dir / "calibration.jsonl", tokenizer, config)
    test_items = load_items(args.data_dir / "test.jsonl", tokenizer, config)
    train_item_count = len(train_items)
    if args.max_train_items:
        train_items = train_items[:args.max_train_items]
    usable = len(train_items) - len(train_items) % world_size
    local_items = train_items[:usable][rank::world_size]
    if not local_items:
        raise SystemExit("No training items assigned to this GPU rank")
    if rank == 0:
        args.output.mkdir(parents=True, exist_ok=True)
        baseline_rows = forward_rows(model, test_items, tokenizer.pad_token_id, device,
                                     args.micro_batch, amp_dtype)
        baseline = evaluate(baseline_rows)
    if world_size > 1:
        dist.barrier()

    wrapped = DDP(model, device_ids=[local_rank], find_unused_parameters=True) if world_size > 1 else model
    encoder = [parameter for name, parameter in wrapped.named_parameters() if "encoder." in name]
    head = [parameter for name, parameter in wrapped.named_parameters() if "encoder." not in name]
    optimizer = torch.optim.AdamW([{"params": encoder, "lr": args.encoder_lr},
                                   {"params": head, "lr": args.head_lr}], weight_decay=.01)
    batches = math.ceil(len(local_items) / args.micro_batch)
    updates = math.ceil(batches / args.grad_accum) * args.epochs
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(1, updates), eta_min=1e-6)
    scaler = torch.amp.GradScaler("cuda", enabled=True)
    history = []
    best_validation, best_state = -1.0, None
    for epoch in range(args.epochs):
        random.Random(args.seed + epoch + rank).shuffle(local_items)
        wrapped.train(); optimizer.zero_grad(set_to_none=True)
        losses = []
        for batch_index in range(0, len(local_items), args.micro_batch):
            chunk = local_items[batch_index:batch_index + args.micro_batch]
            batch = collate(chunk, tokenizer.pad_token_id)
            with torch.autocast("cuda", dtype=amp_dtype):
                logits, act = wrapped(batch["input_ids"].to(device), batch["attention_mask"].to(device),
                                      batch["marker_pos"].to(device), batch["marker_mask"].to(device),
                                      batch["qtype"].to(device))
            logits = logits.float(); mask = batch["marker_mask"].to(device)
            target = batch["target"].to(device); option_count = mask.sum(-1, keepdim=True).float()
            progress = epoch / max(1, args.epochs - 1); sigma = .4 + (.1 - .4) * progress
            noise = torch.randn((args.group_size,) + logits.shape, device=device) * sigma * mask
            noise = (noise - noise.sum(-1, keepdim=True) / option_count) * mask
            sampled = logits.detach().unsqueeze(0) + noise
            distributions = torch.softmax(sampled.masked_fill(~mask, -1e4), -1)
            with torch.no_grad():
                rewards = proper_reward(distributions, target.unsqueeze(0), batch["qtype"].to(device),
                                        mask, w_sph=.75, w_rps=1.0)
                advantage = rewards - rewards.mean(0, keepdim=True)
                advantage /= advantage.std() + 1e-6
            log_probability = -(((sampled - logits.unsqueeze(0)) ** 2) * mask).sum(-1) / (2 * sigma ** 2)
            rl_loss = -(advantage * log_probability).mean()
            ce_loss = -(target * torch.log_softmax(logits.masked_fill(~mask, -1e4), -1)).sum(-1).mean()
            loss = (rl_loss + ce_loss) / args.grad_accum + 0.0 * act.sum()
            scaler.scale(loss).backward(); losses.append(float((loss * args.grad_accum).detach()))
            last = batch_index + args.micro_batch >= len(local_items)
            if ((batch_index // args.micro_batch + 1) % args.grad_accum == 0) or last:
                scaler.unscale_(optimizer); torch.nn.utils.clip_grad_norm_(wrapped.parameters(), 1.0)
                scaler.step(optimizer); scaler.update(); scheduler.step(); optimizer.zero_grad(set_to_none=True)
        if world_size > 1:
            dist.barrier()
        if rank == 0:
            validation_rows = forward_rows(model, validation_items, tokenizer.pad_token_id, device,
                                           args.micro_batch, amp_dtype)
            validation = evaluate(validation_rows)
            history.append({"epoch": epoch + 1, "mean_loss": round(float(np.mean(losses)), 6),
                            "validation": validation})
            print(json.dumps(history[-1], ensure_ascii=False))
            if validation["accuracy"] > best_validation:
                best_validation = validation["accuracy"]
                best_state = {key: value.detach().half().contiguous().cpu()
                              for key, value in model.state_dict().items()}
        if world_size > 1:
            dist.barrier()

    if rank == 0:
        model.load_state_dict(best_state, strict=True); model.to(device)
        calibration_rows = forward_rows(model, calibration_items, tokenizer.pad_token_id, device,
                                        args.micro_batch, amp_dtype)
        calibration = temperature_map(calibration_rows)
        config["fine_tuned"] = True; config["model_name"] = "pulse109-laya"
        config["temperature"] = [calibration["by_type"].get(name, 1.0)
                                 for name in ("choice", "score", "noul")]
        config["temperature_by_options"] = calibration["by_option_count"]
        test_rows = forward_rows(model, test_items, tokenizer.pad_token_id, device,
                                args.micro_batch, amp_dtype)
        metrics = {"baseline_raw": baseline, "trained_raw": evaluate(test_rows),
                   "trained_checkpoint_calibrated": evaluate(test_rows, calibration, "checkpoint"),
                   "trained_pulse_calibrated": evaluate(test_rows, calibration, "pulse"),
                   "validation_best_accuracy": best_validation}
        checkpoint = args.output / "checkpoint"
        save_checkpoint(model, tokenizer, config, checkpoint)
        files = {"dataset_manifest.json": args.data_dir / "dataset_manifest.json",
                 "base_model.safetensors": model_dir / "model.safetensors",
                 "trained_model.safetensors": checkpoint / "model.safetensors",
                 "trained_config.json": checkpoint / "rl_agent_config.json"}
        environment = environment_info()
        (args.output / "environment.json").write_text(json.dumps(environment, indent=2) + "\n")
        (args.output / "calibration.json").write_text(json.dumps(calibration, indent=2) + "\n")
        (args.output / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
        (args.output / "training_history.json").write_text(json.dumps(history, indent=2) + "\n")
        root = Path(__file__).resolve().parents[1]
        manifest = {
            "schema_version": "pulse109-laya-run-v1", "status": "complete",
            "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
            "seed": args.seed, "epochs": args.epochs, "world_size": world_size,
            "base_model": args.model, "base_model_revision": snapshot_revision(str(model_dir)),
            "git_commit": git_value(root, "rev-parse", "HEAD"),
            "git_dirty": bool(git_value(root, "status", "--porcelain")),
            "data_status": json.loads((args.data_dir / "dataset_manifest.json").read_text())["data_status"],
            "hyperparameters": {"micro_batch": args.micro_batch, "grad_accum": args.grad_accum,
                                "encoder_lr": args.encoder_lr, "head_lr": args.head_lr,
                                "group_size": args.group_size, "amp_dtype": "float16",
                                "max_train_items": args.max_train_items},
            "item_counts": {"train_dataset": train_item_count, "train_used": usable,
                            "validation": len(validation_items), "calibration": len(calibration_items),
                            "test": len(test_items)},
            "artifact_hashes": {name: sha256(path) for name, path in files.items()},
            "weight_change_proof": files["base_model.safetensors"].exists()
            and sha256(files["base_model.safetensors"]) != sha256(files["trained_model.safetensors"]),
            "claim_boundary": "Synthetic-only metrics prove execution, not production accuracy."
        }
        (args.output / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps({"run_manifest": str(args.output / "run_manifest.json"), "metrics": metrics}, indent=2))
    if world_size > 1:
        dist.destroy_process_group()


if __name__ == "__main__":
    main()
