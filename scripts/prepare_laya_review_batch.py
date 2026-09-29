"""Prepare private Laya review batches and export only completed approvals."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from build_laya_training_data import (
    PII,
    bool_gold,
    canonical_approved,
    choice_gold,
    sha256,
)

from decision import CATEGORY_GUIDANCE, laya_triage_questions


SCHEMA = "pulse109-laya-review-v1"
SOURCE_KINDS = {"field_pilot", "operator_feedback", "organizer"}
STATUSES = {"pending", "approved", "rejected"}


def read_jsonl(path):
    rows = []
    with Path(path).open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number} is not valid JSON") from error
    return rows


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                            for row in rows), encoding="utf-8")


def require_distinct_paths(*paths):
    resolved = [Path(path).expanduser().resolve() for path in paths]
    if len(resolved) != len(set(resolved)):
        raise ValueError("Input, output and manifest paths must be different")


def candidate_fields(row):
    return {key: row[key] for key in ("id", "group_id", "source_kind", "language", "text")}


def candidate_hash(row):
    encoded = json.dumps(candidate_fields(row), ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_candidate(row):
    required = {"id", "group_id", "source_kind", "language", "text"}
    if not isinstance(row, dict) or set(row) != required:
        raise ValueError("Candidate rows must contain only id, group_id, source_kind, language and text")
    if not all(isinstance(row[key], str) and row[key].strip()
               for key in ("id", "group_id", "source_kind", "language", "text")):
        raise ValueError("Candidate fields must be non-empty strings")
    if row["source_kind"] not in SOURCE_KINDS:
        raise ValueError(f"{row['id']} has unsupported source_kind")
    if row["language"] not in {"ru", "kk"}:
        raise ValueError(f"{row['id']} has unsupported language")
    if len(row["text"]) > 10_000:
        raise ValueError(f"{row['id']} text exceeds 10000 characters")
    serialized = json.dumps(row, ensure_ascii=False, sort_keys=True)
    if any(pattern.search(serialized) for pattern in PII):
        raise ValueError(f"{row['id']} contains a blocked PII pattern")


def validate_unique(rows):
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Candidate ids must be unique")
    normalized = [" ".join(row["text"].casefold().split()) for row in rows]
    if len(normalized) != len(set(normalized)):
        raise ValueError("Candidate texts must be unique")
    sources = {}
    for row in rows:
        previous = sources.setdefault(row["group_id"], row["source_kind"])
        if previous != row["source_kind"]:
            raise ValueError(f"{row['group_id']} mixes source kinds")


def prepare_batch(input_path, output_path):
    require_distinct_paths(input_path, output_path)
    candidates = read_jsonl(input_path)
    if not candidates:
        raise ValueError("Candidate batch is empty")
    for row in candidates:
        validate_candidate(row)
    validate_unique(candidates)
    prepared = []
    for row in candidates:
        prepared.append({
            "schema_version": SCHEMA,
            **candidate_fields(row),
            "candidate_sha256": candidate_hash(row),
            "status": "pending",
            "labels": {
                "category": None,
                "urgency": None,
                "needs_clarification": None,
                "spam_suspected": None,
            },
            "reviewer": None,
            "reviewed_at": None,
            "training_use_approved": None,
            "approval_reference": None,
        })
    write_jsonl(output_path, prepared)
    return {"status": "prepared", "records": len(prepared),
            "groups": len({row["group_id"] for row in prepared}),
            "output_sha256": sha256(Path(output_path))}


def valid_review_time(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def validate_review(row):
    required = {
        "schema_version", "id", "group_id", "source_kind", "language", "text",
        "candidate_sha256", "status", "labels", "reviewer", "reviewed_at",
        "training_use_approved", "approval_reference",
    }
    if not isinstance(row, dict) or set(row) != required or row.get("schema_version") != SCHEMA:
        raise ValueError("Review row does not match the review schema")
    validate_candidate(candidate_fields(row))
    if row["candidate_sha256"] != candidate_hash(row):
        raise ValueError(f"{row['id']} candidate fields changed after preparation")
    if row["status"] not in STATUSES:
        raise ValueError(f"{row['id']} has unsupported review status")
    labels = row["labels"]
    expected = {"category", "urgency", "needs_clarification", "spam_suspected"}
    if not isinstance(labels, dict) or set(labels) != expected:
        raise ValueError(f"{row['id']} has invalid labels")
    if row["status"] == "pending":
        return
    if not isinstance(row["reviewer"], str) or not row["reviewer"].strip():
        raise ValueError(f"{row['id']} is missing reviewer")
    if not valid_review_time(row["reviewed_at"]):
        raise ValueError(f"{row['id']} is missing a timezone-aware reviewed_at")
    if row["status"] == "rejected":
        return
    if row["training_use_approved"] is not True:
        raise ValueError(f"{row['id']} is not approved for training use")
    if not isinstance(row["approval_reference"], str) or not row["approval_reference"].strip():
        raise ValueError(f"{row['id']} is missing a training-use approval reference")
    if not isinstance(labels["needs_clarification"], bool) or not isinstance(labels["spam_suspected"], bool):
        raise ValueError(f"{row['id']} requires reviewed boolean signal labels")
    if labels["needs_clarification"] or labels["spam_suspected"]:
        if labels["category"] is not None or labels["urgency"] is not None:
            raise ValueError(f"{row['id']} must omit category and urgency for signal-only review")
    elif labels["category"] not in CATEGORY_GUIDANCE or labels["urgency"] not in {"normal", "urgent"}:
        raise ValueError(f"{row['id']} requires valid category and urgency labels")


def approved_record(row, questions):
    labels = row["labels"]
    selected = ["needs_clarification", "spam_suspected"]
    if not labels["needs_clarification"] and not labels["spam_suspected"]:
        selected = ["category", *selected, "urgency"]
    gold = {
        "needs_clarification": bool_gold(labels["needs_clarification"]),
        "spam_suspected": bool_gold(labels["spam_suspected"]),
    }
    if "category" in selected:
        gold["category"] = choice_gold(questions["category"]["criteria"], labels["category"])
        gold["urgency"] = choice_gold(questions["urgency"]["criteria"], labels["urgency"])
    candidate = {
        "id": row["id"], "group_id": row["group_id"], "language": row["language"],
        "state": {"text": row["text"], "language": row["language"]},
        "questions": {question_id: questions[question_id] for question_id in selected},
        "gold": {question_id: gold[question_id] for question_id in selected},
    }
    return canonical_approved(candidate, questions, set(CATEGORY_GUIDANCE))


def approved_coverage(rows):
    return {
        "approved_groups": len({row["group_id"] for row in rows}),
        "approved_language_counts": dict(sorted(Counter(row["language"] for row in rows).items())),
        "approved_category_language_counts": dict(sorted(Counter(
            f"{row['gold']['category']['label']}|{row['language']}" for row in rows
            if "category" in row["gold"]).items())),
        "approved_urgency_counts": dict(sorted(Counter(
            row["gold"]["urgency"]["label"] for row in rows if "urgency" in row["gold"]).items())),
    }


def export_approved(input_path, output_path, manifest_path=None):
    output_path = Path(output_path)
    manifest_path = Path(manifest_path) if manifest_path else output_path.with_suffix(
        output_path.suffix + ".manifest.json")
    require_distinct_paths(input_path, output_path, manifest_path)
    reviews = read_jsonl(input_path)
    if not reviews:
        raise ValueError("Review batch is empty")
    for row in reviews:
        validate_review(row)
    validate_unique(reviews)
    pending = [row["id"] for row in reviews if row["status"] == "pending"]
    if pending:
        raise ValueError(f"Review batch still has pending rows: {', '.join(pending[:3])}")
    questions = laya_triage_questions(CATEGORY_GUIDANCE)
    approved = [approved_record(row, questions) for row in reviews if row["status"] == "approved"]
    if not approved:
        raise ValueError("Review batch has no approved rows")
    write_jsonl(output_path, approved)
    status_counts = Counter(row["status"] for row in reviews)
    approved_rows = [row for row in reviews if row["status"] == "approved"]
    manifest = {
        "schema_version": "pulse109-laya-approved-export-v1",
        "review_input_sha256": sha256(Path(input_path)),
        "approved_output_sha256": sha256(output_path),
        "records": len(reviews),
        "groups": len({row["group_id"] for row in reviews}),
        **approved_coverage(approved),
        "status_counts": dict(sorted(status_counts.items())),
        "approved_source_counts": dict(sorted(Counter(row["source_kind"] for row in approved_rows).items())),
        "reviewer_count": len({row["reviewer"] for row in reviews if row["reviewer"]}),
        "claim_boundary": "Reviewer declarations and hashes prove the recorded workflow, not label correctness or production accuracy.",
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify_export(approved_path, manifest_path):
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "pulse109-laya-approved-export-v1":
        raise ValueError("Unknown approved-export manifest schema")
    if manifest.get("approved_output_sha256") != sha256(Path(approved_path)):
        raise ValueError("Approved export hash does not match its review manifest")
    rows = read_jsonl(approved_path)
    if not rows or len(rows) != manifest.get("status_counts", {}).get("approved"):
        raise ValueError("Approved export count does not match its review manifest")
    if manifest.get("status_counts", {}).get("pending", 0):
        raise ValueError("Review manifest still contains pending rows")
    coverage = approved_coverage(rows)
    if any(manifest.get(key) != value for key, value in coverage.items()):
        raise ValueError("Approved export coverage does not match its review manifest")
    return manifest


def self_check():
    candidates = [
        {"id": "candidate-ru", "group_id": "group-electricity", "source_kind": "field_pilot",
         "language": "ru", "text": "После скачка напряжения в доме отключился свет."},
        {"id": "candidate-kk", "group_id": "group-sewerage", "source_kind": "operator_feedback",
         "language": "kk", "text": "Кәріз құдығынан лас су көшеге ағып жатыр."},
    ]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source, review, approved = root / "candidates.jsonl", root / "review.jsonl", root / "approved.jsonl"
        write_jsonl(source, candidates)
        try:
            prepare_batch(source, source)
        except ValueError as error:
            assert "paths must be different" in str(error)
        else:
            raise AssertionError("Candidate source could overwrite itself")
        prepare_batch(source, review)
        rows = read_jsonl(review)
        try:
            export_approved(review, approved)
        except ValueError as error:
            assert "pending" in str(error)
        else:
            raise AssertionError("Pending review rows were exported")
        rows[0].update(status="approved", reviewer="reviewer-1", reviewed_at="2026-09-26T12:00:00+06:00")
        rows[0]["labels"].update(category="electricity", urgency="normal",
                                  needs_clarification=False, spam_suspected=False)
        rows[1].update(status="rejected", reviewer="reviewer-2", reviewed_at="2026-09-26T12:05:00+06:00")
        write_jsonl(review, rows)
        try:
            export_approved(review, approved)
        except ValueError as error:
            assert "not approved for training use" in str(error)
        else:
            raise AssertionError("A row without training-use approval was exported")
        rows[0].update(training_use_approved=True, approval_reference="approval-ticket-1")
        write_jsonl(review, rows)
        try:
            export_approved(review, approved, approved)
        except ValueError as error:
            assert "paths must be different" in str(error)
        else:
            raise AssertionError("Manifest could overwrite the approved export")
        manifest = export_approved(review, approved)
        manifest_path = approved.with_suffix(".jsonl.manifest.json")
        verify_export(approved, manifest_path)
        original_manifest = manifest_path.read_bytes()
        changed_manifest = json.loads(original_manifest)
        changed_manifest["approved_groups"] = 999
        manifest_path.write_text(json.dumps(changed_manifest))
        try:
            verify_export(approved, manifest_path)
        except ValueError as error:
            assert "coverage does not match" in str(error)
        else:
            raise AssertionError("Forged approved coverage passed verification")
        manifest_path.write_bytes(original_manifest)
        original_approved = approved.read_bytes()
        approved.write_bytes(original_approved + b"\n")
        try:
            verify_export(approved, approved.with_suffix(".jsonl.manifest.json"))
        except ValueError as error:
            assert "hash does not match" in str(error)
        else:
            raise AssertionError("A changed approved export passed verification")
        approved.write_bytes(original_approved)
        exported = read_jsonl(approved)
        assert len(exported) == 1 and exported[0]["gold"]["category"]["label"] == "electricity"
        assert manifest["status_counts"] == {"approved": 1, "rejected": 1}
        assert manifest["approved_category_language_counts"] == {"electricity|ru": 1}
        tampered = read_jsonl(review)
        tampered[0]["text"] += " Изменено."
        write_jsonl(review, tampered)
        try:
            export_approved(review, approved)
        except ValueError as error:
            assert "changed after preparation" in str(error)
        else:
            raise AssertionError("Changed candidate text was exported")
        private = dict(candidates[0], id="private", text="Мой ИИН 123456789012")
        write_jsonl(source, [private])
        try:
            prepare_batch(source, review)
        except ValueError as error:
            assert "PII" in str(error)
        else:
            raise AssertionError("PII candidate was prepared for review")
    print("PASS: private candidate gate, completed review and hashed approved export")


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--input", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    export = commands.add_parser("export")
    export.add_argument("--input", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--manifest", type=Path)
    commands.add_parser("self-check")
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_batch(args.input, args.output)
    elif args.command == "export":
        result = export_approved(args.input, args.output, args.manifest)
    else:
        self_check()
        return
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
