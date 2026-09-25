"""Build deterministic, group-separated Pulse 109 Laya training simulations."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

import sys
sys.path.insert(0, str(ROOT))

from decision import CATEGORY_GUIDANCE, laya_triage_questions

SPLITS = ("train", "validation", "calibration", "test")
PII = (
    re.compile(r"\b\d{12}\b"),
    re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}"),
    re.compile(r"(?<!\d)(?:\+?7|8)[\s()-]*\d(?:[\s()-]*\d){9}(?!\d)"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def one_hot(keys, selected):
    return {key: float(key == selected) for key in keys}


def choice_gold(criteria, selected):
    if selected not in criteria:
        raise ValueError(f"Unknown choice label: {selected}")
    return {"label": selected, "probabilities": one_hot(criteria, selected)}


def bool_gold(value):
    selected = "true" if value else "false"
    return {"label": selected, "probabilities": one_hot(("false", "true"), selected)}


def simulated_text(base, language, modifiers, seed, group_id, variant):
    rng = random.Random(f"{seed}:{group_id}:{variant}")
    values = modifiers[language]
    return (rng.choice(values["prefix"]) + base + rng.choice(values["location"])
            + rng.choice(values["suffix"])).strip()


def category_record(topic, language, text, urgency, questions, group_id, split, index):
    selected = copy.deepcopy(questions)
    return {
        "id": f"sim-{group_id}-{index:03d}", "group_id": group_id,
        "origin": "synthetic_simulation", "split": split, "language": language,
        "state": {"text": text, "language": language}, "questions": selected,
        "gold": {
            "category": choice_gold(selected["category"]["criteria"], topic),
            "needs_clarification": bool_gold(False),
            "spam_suspected": bool_gold(False),
            "urgency": choice_gold(selected["urgency"]["criteria"], urgency),
        },
    }


def signal_record(kind, language, text, questions, group_id, split, index):
    qid = "needs_clarification" if kind == "ambiguous" else "spam_suspected"
    selected = {qid: copy.deepcopy(questions[qid])}
    return {
        "id": f"sim-{group_id}-{index:03d}", "group_id": group_id,
        "origin": "synthetic_simulation", "split": split, "language": language,
        "state": {"text": text, "language": language}, "questions": selected,
        "gold": {qid: bool_gold(True)},
    }


def validate_record(record, allowed_topics):
    required = {"id", "group_id", "origin", "split", "language", "state", "questions", "gold"}
    missing = required - record.keys()
    if missing:
        raise ValueError(f"{record.get('id', '<unknown>')} missing {sorted(missing)}")
    if record["split"] not in SPLITS or record["language"] not in {"ru", "kk"}:
        raise ValueError(f"{record['id']} has invalid split or language")
    text = record["state"].get("text", "")
    if not text.strip() or any(pattern.search(text) for pattern in PII):
        raise ValueError(f"{record['id']} has blank text or a blocked PII pattern")
    if set(record["questions"]) != set(record["gold"]):
        raise ValueError(f"{record['id']} question/gold mismatch")
    category = record["gold"].get("category", {}).get("label")
    if category and category not in allowed_topics:
        raise ValueError(f"{record['id']} has unknown category {category}")


def approved_split(group_id, seed):
    value = int(hashlib.sha256(f"{seed}:{group_id}".encode()).hexdigest()[:8], 16) % 100
    return "train" if value < 60 else "validation" if value < 75 else "calibration" if value < 85 else "test"


def load_approved(path, seed):
    if not path:
        return []
    rows = []
    with Path(path).open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                row = json.loads(line)
                row["origin"] = "approved_anonymized"
                row["split"] = approved_split(row["group_id"], seed)
                rows.append(row)
    return rows


def build(config, variants, seed, approved=None):
    topics = config["topics"]
    if set(topics) != set(CATEGORY_GUIDANCE):
        raise ValueError("Simulation taxonomy differs from Pulse CATEGORY_GUIDANCE")
    questions = laya_triage_questions({key: CATEGORY_GUIDANCE[key] for key in topics})
    split_order = config["split_by_core_index"]
    if len(split_order) != 6 or set(split_order) != set(SPLITS):
        raise ValueError("split_by_core_index must cover train, validation, calibration and test")
    rows = []
    for topic, languages in topics.items():
        urgent = set(languages.get("urgent", []))
        for language in ("ru", "kk"):
            for core_index, base in enumerate(languages[language]):
                split = split_order[core_index]
                group_id = f"category-{topic}-{language}-{core_index}"
                for variant in range(variants):
                    text = simulated_text(base, language, config["modifiers"], seed, group_id, variant)
                    rows.append(category_record(topic, language, text,
                                                "urgent" if core_index in urgent else "normal",
                                                questions, group_id, split, variant))
    for kind in ("ambiguous", "spam"):
        for language in ("ru", "kk"):
            for core_index, base in enumerate(config[kind][language]):
                split = split_order[core_index]
                group_id = f"{kind}-{language}-{core_index}"
                for variant in range(variants):
                    text = simulated_text(base, language, config["modifiers"], seed, group_id, variant)
                    rows.append(signal_record(kind, language, text, questions, group_id, split, variant))
    rows.extend(load_approved(approved, seed))
    for row in rows:
        validate_record(row, topics)
    by_group = {}
    for row in rows:
        previous = by_group.setdefault(row["group_id"], row["split"])
        if previous != row["split"]:
            raise ValueError(f"Group leakage: {row['group_id']} is in {previous} and {row['split']}")
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate record ids")
    return rows, questions


def write_dataset(rows, questions, output, config_path, seed, variants, approved=None):
    output.mkdir(parents=True, exist_ok=True)
    config_snapshot = output / "simulation_config.json"
    config_snapshot.write_bytes(config_path.read_bytes())
    files = {}
    for split in SPLITS:
        path = output / f"{split}.jsonl"
        selected = sorted((row for row in rows if row["split"] == split), key=lambda row: row["id"])
        path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                                for row in selected), encoding="utf-8")
        files[path.name] = {"sha256": sha256(path), "records": len(selected),
                            "groups": len({row["group_id"] for row in selected})}
    counts = Counter((row["split"], row["language"], row["origin"]) for row in rows)
    manifest = {
        "schema_version": "pulse109-laya-training-v1", "data_status": "synthetic_only"
        if all(row["origin"] == "synthetic_simulation" for row in rows) else "mixed_approved_and_synthetic",
        "seed": seed, "variants_per_group": variants,
        "simulation_config": {"path": config_snapshot.name, "sha256": sha256(config_snapshot)},
        "approved_input_sha256": sha256(approved) if approved else None,
        "question_schema_sha256": hashlib.sha256(json.dumps(
            questions, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        "files": files,
        "counts": {"|".join(key): value for key, value in sorted(counts.items())},
        "privacy": "Common IIN, email and Kazakhstan phone patterns rejected before write.",
        "claim_boundary": "Synthetic simulations prove pipeline execution, not production accuracy."
    }
    path = output / "dataset_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "training/laya_simulation.json")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/laya-training-data")
    parser.add_argument("--variants", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20260925)
    parser.add_argument("--approved-jsonl", type=Path)
    args = parser.parse_args()
    if not 1 <= args.variants <= 100:
        raise SystemExit("--variants must be between 1 and 100")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    rows, questions = build(config, args.variants, args.seed, args.approved_jsonl)
    manifest = write_dataset(rows, questions, args.output, args.config, args.seed, args.variants,
                             args.approved_jsonl)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
