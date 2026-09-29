"""Build deterministic, group-separated Pulse 109 Laya training simulations."""

from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
import random
import re
import tempfile
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
        "id": f"sim-{group_id}-{language}-{index:03d}", "group_id": group_id,
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
        "id": f"sim-{group_id}-{language}-{index:03d}", "group_id": group_id,
        "origin": "synthetic_simulation", "split": split, "language": language,
        "state": {"text": text, "language": language}, "questions": selected,
        "gold": {qid: bool_gold(True)},
    }


def validate_record(record, allowed_topics, question_schema=None):
    required = {"id", "group_id", "origin", "split", "language", "state", "questions", "gold"}
    missing = required - record.keys()
    if missing:
        raise ValueError(f"{record.get('id', '<unknown>')} missing {sorted(missing)}")
    if record["split"] not in SPLITS or record["language"] not in {"ru", "kk"}:
        raise ValueError(f"{record['id']} has invalid split or language")
    text = record["state"].get("text", "")
    if not text.strip() or any(pattern.search(text) for pattern in PII):
        raise ValueError(f"{record['id']} has blank text or a blocked PII pattern")
    if record["state"].get("language") != record["language"]:
        raise ValueError(f"{record['id']} has inconsistent language fields")
    if set(record["questions"]) != set(record["gold"]):
        raise ValueError(f"{record['id']} question/gold mismatch")
    if question_schema:
        unknown = set(record["questions"]) - set(question_schema)
        if unknown:
            raise ValueError(f"{record['id']} has unknown questions: {sorted(unknown)}")
        expected = required_labels(question_schema)
        for question_id, gold in record["gold"].items():
            if record["questions"][question_id] != question_schema[question_id]:
                raise ValueError(f"{record['id']} altered canonical question {question_id}")
            probabilities = gold.get("probabilities")
            if not isinstance(probabilities, dict) or set(probabilities) != set(expected[question_id]):
                raise ValueError(f"{record['id']} has invalid {question_id} probability keys")
            if any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(value) or value < 0 for value in probabilities.values()):
                raise ValueError(f"{record['id']} has invalid {question_id} probabilities")
            total = sum(probabilities.values())
            winner = max(expected[question_id], key=lambda label: probabilities[label])
            if not math.isfinite(total) or total <= 0 or winner != gold.get("label"):
                raise ValueError(f"{record['id']} has contradictory {question_id} gold")
    category = record["gold"].get("category", {}).get("label")
    if category and category not in allowed_topics:
        raise ValueError(f"{record['id']} has unknown category {category}")


def approved_splits(rows, seed):
    groups = {}
    for row in rows:
        groups.setdefault(row["group_id"], []).append(row)
    features = {}
    for group, records in groups.items():
        signature = {f"language:{row['language']}" for row in records}
        signature.update(f"label:{qid}:{gold['label']}" for row in records
                         for qid, gold in row["gold"].items())
        features[group] = signature
    totals = Counter(feature for signature in features.values() for feature in signature)
    ordered = sorted(features, key=lambda group: (
        -sum(1 / totals[item] for item in features[group]), stable_key(seed, "approved", group)))
    assigned, seen = {}, Counter()
    for group in ordered:
        selected = min(SPLITS, key=lambda split: (
            sum(seen[(split, feature)] for feature in features[group]),
            sum(1 for value in assigned.values() if value == split), stable_key(seed, group, split)))
        assigned[group] = selected
        for feature in features[group]:
            seen[(selected, feature)] += 1
    return assigned


def canonical_approved(row, questions, allowed_topics):
    required = {"id", "group_id", "language", "state", "questions", "gold"}
    if not isinstance(row, dict) or required - row.keys():
        raise ValueError("Approved record is missing required fields")
    if not all(isinstance(row[key], str) and row[key].strip() for key in ("id", "group_id")):
        raise ValueError("Approved id and group_id must be non-empty strings")
    if (row["language"] not in {"ru", "kk"} or not isinstance(row["state"], dict)
            or not isinstance(row["questions"], dict) or not isinstance(row["gold"], dict)):
        raise ValueError(f"{row['id']} has invalid language or state")
    if set(row["questions"]) != set(row["gold"]):
        raise ValueError(f"{row['id']} question/gold mismatch")
    canonical_questions, canonical_gold = {}, {}
    for qid, question in row["questions"].items():
        if qid not in questions or question != questions[qid]:
            raise ValueError(f"{row['id']} altered canonical question {qid}")
        gold = row["gold"][qid]
        if not isinstance(gold, dict) or set(gold) != {"label", "probabilities"}:
            raise ValueError(f"{row['id']} has invalid {qid} gold schema")
        canonical_questions[qid] = questions[qid]
        canonical_gold[qid] = {"label": gold["label"], "probabilities": gold["probabilities"]}
    record = {
        "id": row["id"], "group_id": row["group_id"], "origin": "approved_anonymized",
        "split": "train", "language": row["language"],
        "state": {"text": row["state"].get("text"), "language": row["language"]},
        "questions": canonical_questions, "gold": canonical_gold,
    }
    validate_record(record, allowed_topics, questions)
    serialized = json.dumps(record, ensure_ascii=False, sort_keys=True)
    if any(pattern.search(serialized) for pattern in PII):
        raise ValueError(f"{record['id']} contains a blocked PII pattern")
    return record


def load_approved(path, seed, questions, allowed_topics):
    if not path:
        return []
    rows = []
    with Path(path).open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                rows.append(canonical_approved(json.loads(line), questions, allowed_topics))
    assignments = approved_splits(rows, seed)
    for row in rows:
        row["split"] = assignments[row["group_id"]]
    return rows


def stable_key(seed, *parts):
    return hashlib.sha256(":".join(map(str, (seed, *parts))).encode()).hexdigest()


def split_slots(config, core_count):
    counts = config.get("split_group_counts", {})
    if set(counts) != set(SPLITS) or any(not isinstance(counts[split], int)
                                         or counts[split] < 1 for split in SPLITS):
        raise ValueError("split_group_counts must contain positive integers for every split")
    if sum(counts.values()) != core_count:
        raise ValueError("split_group_counts must total the number of semantic cores")
    return counts, tuple(split for split in SPLITS for _ in range(counts[split]))


def category_splits(topics, counts, slots, seed):
    assignments = sorted(set(itertools.permutations(slots)))
    urgency_counts = Counter()
    processed_urgent = 0
    result = {}
    ordered_topics = sorted(topics, key=lambda topic: stable_key(seed, "topic", topic))
    for topic in ordered_topics:
        urgent = set(topics[topic].get("urgent", []))
        processed_urgent += len(urgent)
        expected = {split: processed_urgent * counts[split] / len(slots) for split in SPLITS}

        def score(assignment):
            projected = urgency_counts.copy()
            for index, split in enumerate(assignment):
                projected[split] += index in urgent
            distance = sum((projected[split] - expected[split]) ** 2 for split in SPLITS)
            return distance, stable_key(seed, topic, *assignment)

        selected = min(assignments, key=score)
        result[topic] = selected
        for index, split in enumerate(selected):
            urgency_counts[split] += index in urgent
    return result


def signal_splits(kind, core_count, slots, seed):
    ordered = sorted(range(core_count), key=lambda index: stable_key(seed, kind, index))
    assigned = {}
    for index, split in zip(ordered, slots):
        assigned[index] = split
    return assigned


def required_labels(questions):
    return {
        question_id: tuple(question.get("criteria", {"false": "", "true": ""}))
        for question_id, question in questions.items()
    }


def dataset_contract(rows, questions):
    labels = {split: {question_id: Counter() for question_id in questions} for split in SPLITS}
    languages = {split: Counter() for split in SPLITS}
    synthetic_groups = {}
    origin_coverage = {}
    for row in rows:
        split = row["split"]
        languages[split][row["language"]] += 1
        for question_id, gold in row["gold"].items():
            labels[split][question_id][gold["label"]] += 1
        if row["origin"] == "synthetic_simulation":
            synthetic_groups.setdefault(row["group_id"], set()).add(row["language"])
        coverage = origin_coverage.setdefault(row["origin"], {
            "groups": set(), "languages": Counter(),
            "labels": {question_id: Counter() for question_id in questions},
        })
        coverage["groups"].add(row["group_id"])
        coverage["languages"][row["language"]] += 1
        for question_id, gold in row["gold"].items():
            coverage["labels"][question_id][gold["label"]] += 1

    required = required_labels(questions)
    for split in SPLITS:
        if set(languages[split]) != {"ru", "kk"}:
            raise ValueError(f"{split} does not contain both ru and kk records")
        for question_id, expected in required.items():
            missing = set(expected) - set(labels[split][question_id])
            if missing:
                raise ValueError(f"{split} is missing {question_id} labels: {sorted(missing)}")

    approved = origin_coverage.get("approved_anonymized")
    approved_features = Counter()
    if approved:
        for row in rows:
            if row["origin"] == "approved_anonymized":
                approved_features[(row["group_id"], f"language:{row['language']}")] += 1
                for qid, gold in row["gold"].items():
                    approved_features[(row["group_id"], f"label:{qid}:{gold['label']}")] += 1
    group_feature_counts = Counter(feature for _, feature in approved_features)
    approved_full_coverage_expected = bool(approved and len(approved["groups"]) >= len(SPLITS)
        and all(group_feature_counts[f"language:{language}"] >= len(SPLITS) for language in ("ru", "kk"))
        and all(group_feature_counts[f"label:{qid}:{label}"] >= len(SPLITS)
                for qid, labels_for_question in required.items() for label in labels_for_question))
    if approved_full_coverage_expected:
        for split in SPLITS:
            split_rows = [row for row in rows if row["origin"] == "approved_anonymized"
                          and row["split"] == split]
            if {row["language"] for row in split_rows} != {"ru", "kk"}:
                raise ValueError(f"Approved data lacks both languages in {split}")
            for qid, expected in required.items():
                present = {row["gold"][qid]["label"] for row in split_rows if qid in row["gold"]}
                if not set(expected) <= present:
                    raise ValueError(f"Approved data lacks {qid} labels in {split}")
    unpaired = [group for group, present in synthetic_groups.items() if present != {"ru", "kk"}]
    if unpaired:
        raise ValueError(f"Synthetic semantic groups missing a language counterpart: {unpaired[:3]}")

    return {
        "strategy": "deterministic_group_stratified_v2",
        "group_unit": "semantic_core_with_ru_kk_counterparts",
        "required_labels": {key: list(value) for key, value in required.items()},
        "all_required_labels_present": True,
        "all_synthetic_groups_bilingual": True,
        "origin_coverage": {origin: {
            "groups": len(data["groups"]),
            "language_counts": dict(sorted(data["languages"].items())),
            "label_counts": {qid: dict(sorted(counter.items()))
                             for qid, counter in data["labels"].items()},
            "full_split_coverage_expected": (approved_full_coverage_expected
                if origin == "approved_anonymized" else True),
        } for origin, data in origin_coverage.items()},
        "label_counts": {
            split: {question_id: dict(sorted(count.items()))
                    for question_id, count in labels[split].items()}
            for split in SPLITS
        },
        "language_counts": {split: dict(sorted(count.items()))
                            for split, count in languages.items()},
    }


def build(config, variants, seed, approved=None):
    topics = config["topics"]
    if set(topics) != set(CATEGORY_GUIDANCE):
        raise ValueError("Simulation taxonomy differs from Pulse CATEGORY_GUIDANCE")
    questions = laya_triage_questions({key: CATEGORY_GUIDANCE[key] for key in topics})
    core_counts = {len(languages["ru"]) for languages in topics.values()}
    core_counts.update(len(languages["kk"]) for languages in topics.values())
    if len(core_counts) != 1:
        raise ValueError("Every category must have the same number of ru and kk semantic cores")
    core_count = core_counts.pop()
    counts, slots = split_slots(config, core_count)
    category_assignment = category_splits(topics, counts, slots, seed)
    rows = []
    for topic, languages in topics.items():
        urgent = set(languages.get("urgent", []))
        if not urgent <= set(range(core_count)):
            raise ValueError(f"{topic} contains an invalid urgent core index")
        for language in ("ru", "kk"):
            for core_index, base in enumerate(languages[language]):
                split = category_assignment[topic][core_index]
                group_id = f"category-{topic}-{core_index}"
                for variant in range(variants):
                    text = simulated_text(base, language, config["modifiers"], seed, group_id, variant)
                    rows.append(category_record(topic, language, text,
                                                "urgent" if core_index in urgent else "normal",
                                                questions, group_id, split, variant))
    for kind in ("ambiguous", "spam"):
        lengths = {len(config[kind][language]) for language in ("ru", "kk")}
        if lengths != {core_count}:
            raise ValueError(f"{kind} must have {core_count} paired ru and kk semantic cores")
        assignment = signal_splits(kind, core_count, slots, seed)
        for language in ("ru", "kk"):
            for core_index, base in enumerate(config[kind][language]):
                split = assignment[core_index]
                group_id = f"{kind}-{core_index}"
                for variant in range(variants):
                    text = simulated_text(base, language, config["modifiers"], seed, group_id, variant)
                    rows.append(signal_record(kind, language, text, questions, group_id, split, variant))
    rows.extend(load_approved(approved, seed, questions, topics))
    for row in rows:
        validate_record(row, topics, questions)
    by_group = {}
    for row in rows:
        previous = by_group.setdefault(row["group_id"], row["split"])
        if previous != row["split"]:
            raise ValueError(f"Group leakage: {row['group_id']} is in {previous} and {row['split']}")
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate record ids")
    dataset_contract(rows, questions)
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
    contract = dataset_contract(rows, questions)
    data_status = ("synthetic_only" if all(row["origin"] == "synthetic_simulation" for row in rows)
                   else "mixed_approved_and_synthetic")
    manifest = {
        "schema_version": "pulse109-laya-training-v1", "data_status": data_status,
        "seed": seed, "variants_per_group": variants,
        "simulation_config": {"path": config_snapshot.name, "sha256": sha256(config_snapshot)},
        "approved_input_sha256": sha256(approved) if approved else None,
        "question_schema_sha256": hashlib.sha256(json.dumps(
            questions, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        "files": files,
        "counts": {"|".join(key): value for key, value in sorted(counts.items())},
        "split_contract": contract,
        "privacy": "Only whitelisted approved fields are written; common IIN, email and Kazakhstan phone patterns are rejected.",
        "claim_boundary": ("Synthetic simulations prove pipeline execution, not production accuracy."
                           if data_status == "synthetic_only" else
                           "Mixed approved and synthetic data describes only this recorded dataset, not production accuracy.")
    }
    path = output / "dataset_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def self_check(config):
    first, questions = build(config, 1, 7)
    second, _ = build(config, 1, 7)
    if [(row["id"], row["group_id"], row["split"]) for row in first] != [
            (row["id"], row["group_id"], row["split"]) for row in second]:
        raise AssertionError("Dataset generation is not deterministic")
    groups = {}
    for row in first:
        if row["origin"] == "synthetic_simulation":
            groups.setdefault(row["group_id"], set()).add((row["split"], row["language"]))
    for group, values in groups.items():
        if len({split for split, _ in values}) != 1:
            raise AssertionError(f"Group leakage in {group}")
        if {language for _, language in values} != {"ru", "kk"}:
            raise AssertionError(f"Unpaired semantic group {group}")
    contract = dataset_contract(first, questions)
    approved = copy.deepcopy(first[0])
    approved.update(id="approved-pilot", group_id="approved-pilot-group", origin="approved_anonymized",
                    private_email="hidden@example.kz")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "approved.jsonl"
        path.write_text(json.dumps(approved, ensure_ascii=False) + "\n", encoding="utf-8")
        pilot, _ = build(config, 1, 7, path)
        saved = next(row for row in pilot if row["id"] == "approved-pilot")
        if "private_email" in saved:
            raise AssertionError("Approved extras were not removed by canonical whitelist")
        for name, mutate in (
            ("altered question", lambda row: row["questions"]["category"].update(instructions="changed")),
            ("contradictory gold", lambda row: row["gold"]["category"].update(label="roads")),
            ("nonfinite gold", lambda row: row["gold"]["category"]["probabilities"].update(heating=float("nan"))),
        ):
            invalid = copy.deepcopy(approved)
            mutate(invalid)
            path.write_text(json.dumps(invalid, ensure_ascii=False, allow_nan=True) + "\n", encoding="utf-8")
            try:
                build(config, 1, 7, path)
            except ValueError:
                continue
            raise AssertionError(f"Approved {name} was accepted")
    print(json.dumps(contract, ensure_ascii=False, indent=2))
    print("PASS: deterministic stratification, bilingual group isolation, gold validation and approved-data whitelist")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "training/laya_simulation.json")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/laya-training-data")
    parser.add_argument("--variants", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20260925)
    parser.add_argument("--approved-jsonl", type=Path)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.variants <= 100:
        raise SystemExit("--variants must be between 1 and 100")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.self_check:
        self_check(config)
        return
    rows, questions = build(config, args.variants, args.seed, args.approved_jsonl)
    manifest = write_dataset(rows, questions, args.output, args.config, args.seed, args.variants,
                             args.approved_jsonl)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
