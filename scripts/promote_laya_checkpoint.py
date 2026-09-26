"""Verify and atomically promote a Pulse109 Laya checkpoint."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

try:
    from run_laya_gpu_experiments import load_json, require, sha256, verify_experiment
except ImportError:
    from scripts.run_laya_gpu_experiments import load_json, require, sha256, verify_experiment

STAGES = ("shadow", "canary", "production")
REVIEW_MINIMUMS = {
    "canary": {"groups": 40, "category_language": 2, "binary_label": 4},
    "production": {"groups": 200, "category_language": 10, "binary_label": 20},
}


def hash_tree(root):
    root = Path(root)
    require(root.is_dir(), f"Checkpoint directory is missing: {root}")
    files = {}
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), f"Checkpoint contains a symlink: {path}")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = sha256(path)
    require("model.safetensors" in files, "Checkpoint has no model.safetensors")
    require("rl_agent_config.json" in files, "Checkpoint has no rl_agent_config.json")
    require(any(name.startswith("encoder/") for name in files), "Checkpoint has no encoder files")
    require(any(name.startswith("tokenizer/") for name in files), "Checkpoint has no tokenizer files")
    return files


def eligible_runs(experiment_dir):
    experiment_dir = Path(experiment_dir).resolve()
    experiment = load_json(experiment_dir / "experiment_manifest.json")
    candidates = []
    for entry in experiment.get("runs", []):
        run_dir = (experiment_dir / entry["directory"]).resolve()
        require(run_dir.is_relative_to(experiment_dir), "Run directory escapes the experiment bundle")
        metrics = load_json(run_dir / "metrics.json")
        selection = metrics.get("validation_checkpoint_selection", {})
        if selection.get("eligible") is True:
            candidates.append((selection.get("rank", []), entry, run_dir))
    return candidates


def select_run(experiment_dir, seed=None):
    candidates = eligible_runs(experiment_dir)
    require(candidates, "No run passed validation checkpoint guardrails")
    if seed is not None:
        candidates = [item for item in candidates if item[1].get("seed") == seed]
        require(candidates, f"Seed {seed} is not a guardrail-eligible experiment run")
    return max(candidates, key=lambda item: (item[0], item[1].get("seed", -1)))[1:]


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def previous_version(destination, stage):
    pointer = Path(destination) / "stages" / f"{stage}.json"
    if not pointer.is_file():
        return None
    value = load_json(pointer).get("version")
    return value if isinstance(value, str) and value else None


def validate_stage_data(stage, data_status):
    if stage in {"canary", "production"}:
        require(data_status != "synthetic_only",
                f"{stage} promotion refuses synthetic-only training data")
        require(data_status == "mixed_approved_and_synthetic",
                f"Unsupported {stage} training data status: {data_status}")


def validate_review_coverage(stage, experiment_dir):
    if stage == "shadow":
        return
    minimum = REVIEW_MINIMUMS[stage]
    dataset = load_json(Path(experiment_dir) / "data/dataset_manifest.json")
    review = load_json(Path(experiment_dir) / "data/approved_review_manifest.json")
    contract = dataset.get("split_contract", {})
    approved = contract.get("origin_coverage", {}).get("approved_anonymized", {})
    require(review.get("approved_groups", 0) >= minimum["groups"],
            f"{stage} requires at least {minimum['groups']} approved semantic groups")

    categories = contract.get("required_labels", {}).get("category", [])
    category_language = review.get("approved_category_language_counts", {})
    missing = [f"{category}|{language}" for category in categories for language in ("ru", "kk")
               if category_language.get(f"{category}|{language}", 0) < minimum["category_language"]]
    require(categories and not missing,
            f"{stage} lacks approved category/language coverage: {', '.join(missing[:4])}")

    label_counts = approved.get("label_counts", {})
    required_labels = contract.get("required_labels", {})
    sparse = [f"{question}:{label}" for question in ("needs_clarification", "spam_suspected", "urgency")
              for label in required_labels.get(question, [])
              if label_counts.get(question, {}).get(label, 0) < minimum["binary_label"]]
    require(not sparse, f"{stage} lacks approved binary-label coverage: {', '.join(sparse[:4])}")


def verify_existing_version(version_dir, source_hashes, experiment_hash, run_hash):
    manifest = load_json(version_dir / "promotion.json")
    require(manifest.get("schema_version") == "pulse109-laya-promotion-v1",
            "Existing checkpoint has an unknown promotion manifest")
    require(manifest.get("version") == version_dir.name,
            "Existing checkpoint version does not match its directory")
    require(manifest.get("checkpoint", {}).get("files") == source_hashes,
            "Existing checkpoint records different source hashes")
    require(hash_tree(version_dir / "checkpoint") == source_hashes,
            "Existing promoted checkpoint hash mismatch")
    source = manifest.get("source", {})
    require(source.get("experiment_manifest_sha256") == experiment_hash
            and source.get("run_manifest_sha256") == run_hash,
            "Existing checkpoint has different training provenance")


def promote(experiment_dir, destination, stage, seed=None):
    experiment_dir = Path(experiment_dir).resolve()
    destination = Path(destination).resolve()
    require(stage in STAGES, f"Unknown promotion stage: {stage}")
    verified = verify_experiment(experiment_dir)
    data_status = verified["dataset"]["data_status"]
    validate_stage_data(stage, data_status)
    validate_review_coverage(stage, experiment_dir)

    entry, run_dir = select_run(experiment_dir, seed)
    run_manifest_path = run_dir / "run_manifest.json"
    run_manifest = load_json(run_manifest_path)
    checkpoint = run_dir / "checkpoint"
    source_hashes = hash_tree(checkpoint)
    model_hash = source_hashes["model.safetensors"]
    require(model_hash == run_manifest.get("artifact_hashes", {}).get("trained_model.safetensors"),
            "Selected checkpoint model hash does not match its run manifest")

    version = model_hash
    version_dir = destination / version
    experiment_hash = sha256(experiment_dir / "experiment_manifest.json")
    run_hash = sha256(run_manifest_path)
    destination.mkdir(parents=True, exist_ok=True)
    prior = previous_version(destination, stage)
    if prior == version:
        verify_existing_version(version_dir, source_hashes, experiment_hash, run_hash)
        return {"status": "already_promoted", "stage": stage, "version": version,
                "path": str(version_dir), "previous_version": prior}
    created_at = datetime.now(timezone.utc).isoformat()
    if version_dir.exists():
        verify_existing_version(version_dir, source_hashes, experiment_hash, run_hash)
    else:
        temporary = Path(tempfile.mkdtemp(prefix=".promote-", dir=destination))
        try:
            copied_checkpoint = temporary / "checkpoint"
            shutil.copytree(checkpoint, copied_checkpoint)
            require(hash_tree(copied_checkpoint) == source_hashes,
                    "Copied checkpoint does not match the selected checkpoint")
            manifest = {
                "schema_version": "pulse109-laya-promotion-v1",
                "version": version,
                "created_at": created_at,
                "source": {
                    "experiment_manifest_sha256": experiment_hash,
                    "run_manifest_sha256": run_hash,
                    "seed": entry["seed"],
                    "git_commit": run_manifest.get("git_commit"),
                    "data_status": data_status,
                },
                "checkpoint": {"directory": "checkpoint", "files": source_hashes},
            }
            atomic_json(temporary / "promotion.json", manifest)
            os.replace(temporary, version_dir)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    pointer = {
        "schema_version": "pulse109-laya-stage-pointer-v1",
        "stage": stage,
        "version": version,
        "previous_version": prior,
        "promoted_at": created_at,
    }
    atomic_json(destination / "stages" / f"{stage}.json", pointer)
    return {"status": "promoted", "stage": stage, "version": version,
            "path": str(version_dir), "previous_version": prior}


def self_check():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        experiment = root / "experiment"
        run = experiment / "runs/seed-17"
        checkpoint = run / "checkpoint"
        (checkpoint / "encoder").mkdir(parents=True)
        (checkpoint / "tokenizer").mkdir()
        (checkpoint / "model.safetensors").write_bytes(b"weights")
        (checkpoint / "rl_agent_config.json").write_text("{}\n")
        (checkpoint / "encoder/config.json").write_text("{}\n")
        (checkpoint / "tokenizer/tokenizer.json").write_text("{}\n")
        expected = hash_tree(checkpoint)
        (run / "metrics.json").write_text(json.dumps({
            "validation_checkpoint_selection": {"eligible": True, "rank": [.8, .8, .8]},
        }))
        (run / "run_manifest.json").write_text(json.dumps({
            "git_commit": "a" * 40,
            "artifact_hashes": {"trained_model.safetensors": expected["model.safetensors"]},
        }))
        (experiment / "experiment_manifest.json").write_text(json.dumps({
            "runs": [{"seed": 17, "directory": "runs/seed-17"}],
        }))

        original_verifier = globals()["verify_experiment"]
        globals()["verify_experiment"] = lambda _: {"dataset": {"data_status": "synthetic_only"}}
        try:
            destination = root / "registry"
            first = promote(experiment, destination, "shadow")
            second = promote(experiment, destination, "shadow")
            require(first["status"] == "promoted" and second["status"] == "already_promoted",
                    "Shadow promotion is not idempotent")
            require(hash_tree(Path(first["path"]) / "checkpoint") == expected,
                    "Promoted checkpoint changed during copy")
            try:
                promote(experiment, destination, "production")
            except ValueError:
                pass
            else:
                raise AssertionError("Production gate accepted synthetic-only data")

            globals()["verify_experiment"] = lambda _: {
                "dataset": {"data_status": "mixed_approved_and_synthetic"}}
            (experiment / "data").mkdir()
            (experiment / "data/dataset_manifest.json").write_text(json.dumps({
                "split_contract": {
                    "required_labels": {"category": ["water"], "needs_clarification": ["false", "true"],
                                        "spam_suspected": ["false", "true"], "urgency": ["normal", "urgent"]},
                    "origin_coverage": {"approved_anonymized": {"label_counts": {
                        "needs_clarification": {"false": 4, "true": 4},
                        "spam_suspected": {"false": 4, "true": 4},
                        "urgency": {"normal": 4, "urgent": 4},
                    }}},
                },
            }))
            review = {"approved_groups": 1, "approved_category_language_counts": {}}
            (experiment / "data/approved_review_manifest.json").write_text(json.dumps(review))
            try:
                promote(experiment, destination, "canary")
            except ValueError:
                pass
            else:
                raise AssertionError("Canary gate accepted insufficient approved coverage")
            review.update(approved_groups=40,
                          approved_category_language_counts={"water|ru": 2, "water|kk": 2})
            (experiment / "data/approved_review_manifest.json").write_text(json.dumps(review))
            require(promote(experiment, destination, "canary")["status"] == "promoted",
                    "Canary promotion rejected sufficient review coverage")
        finally:
            globals()["verify_experiment"] = original_verifier
    print("PASS: atomic promotion, idempotency and staged approved-data gates")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--stage", choices=STAGES, default="shadow")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    require(args.experiment is not None, "--experiment is required")
    require(args.destination is not None, "--destination is required")
    print(json.dumps(promote(args.experiment, args.destination, args.stage, args.seed),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
