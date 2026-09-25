"""Run repeated Laya GPU fine-tuning and verify the resulting evidence bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPLITS = ("train", "validation", "calibration", "test")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_dataset(data_dir):
    manifest_path = data_dir / "dataset_manifest.json"
    manifest = load_json(manifest_path)
    require(manifest.get("schema_version") == "pulse109-laya-training-v1",
            "Unknown dataset manifest schema")
    config = data_dir / manifest["simulation_config"]["path"]
    require(config.is_file() and sha256(config) == manifest["simulation_config"]["sha256"],
            "Simulation config hash mismatch")
    groups = {}
    records = 0
    for split in SPLITS:
        path = data_dir / f"{split}.jsonl"
        expected = manifest["files"][path.name]
        require(path.is_file() and sha256(path) == expected["sha256"], f"{path.name} hash mismatch")
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        require(len(rows) == expected["records"], f"{path.name} record count mismatch")
        split_groups = {row["group_id"] for row in rows}
        require(len(split_groups) == expected["groups"], f"{path.name} group count mismatch")
        for group in split_groups:
            require(group not in groups, f"Group leakage: {group} in {groups.get(group)} and {split}")
            groups[group] = split
        require(all(row.get("split") == split for row in rows), f"{path.name} contains wrong split")
        records += len(rows)
    return {"records": records, "groups": len(groups), "sha256": sha256(manifest_path),
            "data_status": manifest["data_status"]}


def numeric_values(value):
    if isinstance(value, dict):
        for nested in value.values():
            yield from numeric_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from numeric_values(nested)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield float(value)


def verify_run(run_dir, data_manifest_hash, expected_seed=None):
    manifest_path = run_dir / "run_manifest.json"
    manifest = load_json(manifest_path)
    require(manifest.get("schema_version") == "pulse109-laya-run-v1", "Unknown run manifest schema")
    require(manifest.get("status") == "complete", f"Incomplete run: {run_dir.name}")
    if expected_seed is not None:
        require(manifest.get("seed") == expected_seed, f"Wrong seed in {run_dir.name}")
    hashes = manifest.get("artifact_hashes", {})
    require(hashes.get("dataset_manifest.json") == data_manifest_hash,
            f"Dataset hash mismatch in {run_dir.name}")
    checkpoint = run_dir / "checkpoint"
    for key, name in (("trained_model.safetensors", "model.safetensors"),
                      ("trained_config.json", "rl_agent_config.json")):
        path = checkpoint / name
        require(path.is_file() and sha256(path) == hashes.get(key), f"{key} hash mismatch")
    base_hash = hashes.get("base_model.safetensors", "")
    trained_hash = hashes.get("trained_model.safetensors", "")
    require(len(base_hash) == len(trained_hash) == 64 and base_hash != trained_hash,
            "Base and trained weight hashes do not prove a change")
    require(manifest.get("weight_change_proof") is True, "Run did not record weight change proof")
    require(manifest.get("git_commit") and not manifest.get("git_dirty"),
            "Training must run from a committed, clean checkout")

    environment = load_json(run_dir / "environment.json")
    require(environment.get("gpus") and environment.get("cuda_runtime"), "NVIDIA CUDA evidence is missing")
    calibration = load_json(run_dir / "calibration.json")
    temperatures = list(numeric_values(calibration))
    require(temperatures and all(math.isfinite(value) and .5 <= value <= 5 for value in temperatures),
            "Calibration contains missing or unsafe temperatures")
    metrics = load_json(run_dir / "metrics.json")
    required_metrics = {"baseline_raw", "trained_raw", "trained_checkpoint_calibrated",
                        "trained_pulse_calibrated", "validation_best_accuracy"}
    require(required_metrics <= metrics.keys(), "Required metrics are missing")
    history = load_json(run_dir / "training_history.json")
    require(len(history) == manifest.get("epochs") and history, "Training history is incomplete")
    require("Synthetic-only" in manifest.get("claim_boundary", ""), "Claim boundary is missing")
    return {"seed": manifest["seed"], "manifest_sha256": sha256(manifest_path),
            "trained_model_sha256": trained_hash, "metrics": metrics}


def verify_experiment(output):
    dataset = verify_dataset(output / "data")
    experiment = load_json(output / "experiment_manifest.json")
    require(experiment.get("schema_version") == "pulse109-laya-experiment-v1",
            "Unknown experiment manifest schema")
    require(experiment.get("status") == "complete", "Experiment is not complete")
    require(len(experiment.get("runs", [])) >= 2, "At least two independent seeds are required")
    checked = []
    for entry in experiment["runs"]:
        run_dir = output / entry["directory"]
        result = verify_run(run_dir, dataset["sha256"], entry["seed"])
        require(result["manifest_sha256"] == entry["manifest_sha256"], "Run manifest hash mismatch")
        log = output / entry["log"]
        require(log.is_file() and sha256(log) == entry["log_sha256"], "Training log hash mismatch")
        checked.append(result)
    require(len({run["seed"] for run in checked}) == len(checked), "Seeds must be unique")
    require(len({run["trained_model_sha256"] for run in checked}) == len(checked),
            "Independent seeds produced identical checkpoints")
    return {"status": "verified", "runs": len(checked), "seeds": [run["seed"] for run in checked],
            "dataset": dataset, "claim_boundary": experiment["claim_boundary"]}


def metric_summary(results):
    fields = {
        "baseline_category_accuracy": ("baseline_raw", "category_accuracy"),
        "trained_category_accuracy": ("trained_raw", "category_accuracy"),
        "trained_category_macro_f1": ("trained_raw", "category_macro_f1"),
        "checkpoint_calibrated_ece": ("trained_checkpoint_calibrated", "ece"),
        "checkpoint_calibrated_nll": ("trained_checkpoint_calibrated", "nll"),
    }
    summary = {}
    for name, (section, field) in fields.items():
        values = [result["metrics"][section][field] for result in results]
        summary[name] = {"mean": round(statistics.mean(values), 6),
                         "population_std": round(statistics.pstdev(values), 6), "values": values}
    return summary


def gpu_count():
    require(shutil.which("nvidia-smi"), "nvidia-smi is not installed or not on PATH")
    result = subprocess.run(["nvidia-smi", "--query-gpu=index", "--format=csv,noheader"],
                            text=True, capture_output=True)
    require(result.returncode == 0, "nvidia-smi failed: " + result.stderr.strip())
    return len([line for line in result.stdout.splitlines() if line.strip()])


def run_logged(command, log_path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, bufsize=1,
                                   env={**os.environ, "PYTHONUNBUFFERED": "1"})
        for line in process.stdout:
            print(line, end="")
            log.write(line)
        return process.wait()


def run_experiment(args):
    status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                            text=True, capture_output=True, check=True)
    require(not status.stdout.strip(), "Commit the training code before starting the GPU job")
    require(not args.output.exists() or not any(args.output.iterdir()),
            f"Output directory is not empty: {args.output}")
    require(len(args.seeds) >= 2 and len(set(args.seeds)) == len(args.seeds),
            "Use at least two unique seeds")
    available = gpu_count()
    workers = args.gpus or available
    require(1 <= workers <= available, f"Requested {workers} GPUs, but {available} are visible")
    args.output.mkdir(parents=True, exist_ok=True)
    data_dir = args.output / "data"
    build = [sys.executable, str(ROOT / "scripts/build_laya_training_data.py"),
             "--output", str(data_dir), "--variants", str(args.variants), "--seed", str(args.data_seed)]
    if args.approved_jsonl:
        build += ["--approved-jsonl", str(args.approved_jsonl)]
    subprocess.run(build, cwd=ROOT, check=True)
    dataset = verify_dataset(data_dir)
    started = datetime.now(timezone.utc).isoformat()
    entries, results = [], []
    for seed in args.seeds:
        run_dir = args.output / "runs" / f"seed-{seed}"
        log_path = args.output / "logs" / f"seed-{seed}.log"
        command = [sys.executable, "-m", "torch.distributed.run", "--standalone",
                   f"--nproc-per-node={workers}", str(ROOT / "scripts/train_laya_gpu.py"),
                   "--data-dir", str(data_dir), "--output", str(run_dir), "--seed", str(seed),
                   "--epochs", str(args.epochs), "--micro-batch", str(args.micro_batch),
                   "--grad-accum", str(args.grad_accum)]
        if args.model:
            command += ["--model", args.model]
        if args.model_revision:
            command += ["--model-revision", args.model_revision]
        status = run_logged(command, log_path)
        require(status == 0, f"Training seed {seed} failed; see {log_path}")
        result = verify_run(run_dir, dataset["sha256"], seed)
        results.append(result)
        entries.append({"seed": seed, "directory": str(run_dir.relative_to(args.output)),
                        "log": str(log_path.relative_to(args.output)),
                        "log_sha256": sha256(log_path),
                        "manifest_sha256": result["manifest_sha256"], "command": command})
    experiment = {
        "schema_version": "pulse109-laya-experiment-v1", "status": "complete",
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
        "gpu_workers": workers, "seeds": args.seeds, "runs": entries,
        "aggregate_test_metrics": metric_summary(results),
        "data_status": dataset["data_status"],
        "claim_boundary": "Synthetic-only repeated runs prove training and calibration execution, not production accuracy.",
    }
    (args.output / "experiment_manifest.json").write_text(
        json.dumps(experiment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    verified = verify_experiment(args.output)
    print(json.dumps(verified, ensure_ascii=False, indent=2))


def write_self_check_run(root, data_hash, seed):
    run_dir = root / "runs" / f"seed-{seed}"
    checkpoint = run_dir / "checkpoint"
    checkpoint.mkdir(parents=True)
    (checkpoint / "model.safetensors").write_bytes(f"trained-{seed}".encode())
    (checkpoint / "rl_agent_config.json").write_text("{}\n")
    calibration = {"by_type": {"choice": 1.1}, "by_option_count": {"choice:6-10": .9}}
    metrics = {name: {"category_accuracy": .5, "category_macro_f1": .4, "ece": .1, "nll": 1.0}
               for name in ("baseline_raw", "trained_raw", "trained_checkpoint_calibrated",
                            "trained_pulse_calibrated")}
    metrics["validation_best_accuracy"] = .5
    files = {"environment.json": {"gpus": [{"name": "self-check"}], "cuda_runtime": "test"},
             "calibration.json": calibration, "metrics.json": metrics,
             "training_history.json": [{"epoch": 1}]}
    for name, value in files.items():
        (run_dir / name).write_text(json.dumps(value))
    hashes = {"dataset_manifest.json": data_hash,
              "base_model.safetensors": hashlib.sha256(b"base").hexdigest(),
              "trained_model.safetensors": sha256(checkpoint / "model.safetensors"),
              "trained_config.json": sha256(checkpoint / "rl_agent_config.json")}
    manifest = {"schema_version": "pulse109-laya-run-v1", "status": "complete", "seed": seed,
                "epochs": 1, "git_commit": "a" * 40, "git_dirty": False,
                "artifact_hashes": hashes, "weight_change_proof": True,
                "claim_boundary": "Synthetic-only self-check."}
    path = run_dir / "run_manifest.json"
    path.write_text(json.dumps(manifest))
    log = root / "logs" / f"seed-{seed}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("self-check\n")
    return {"seed": seed, "directory": str(run_dir.relative_to(root)),
            "log": str(log.relative_to(root)), "log_sha256": sha256(log),
            "manifest_sha256": sha256(path)}


def self_check():
    sys.path.insert(0, str(ROOT / "scripts"))
    from build_laya_training_data import build, validate_record, write_dataset
    config_path = ROOT / "training/laya_simulation.json"
    config = load_json(config_path)
    rows, questions = build(config, 1, 7)
    with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
        first, second = Path(first), Path(second)
        first_data, second_data = first / "data", second / "data"
        one = write_dataset(rows, questions, first_data, config_path, 7, 1)
        two = write_dataset(*build(config, 1, 7), second_data, config_path, 7, 1)
        require(one["files"] == two["files"], "Dataset generation is not deterministic")
        verify_dataset(first_data)
        broken = dict(rows[0]); broken["state"] = {"text": "ИИН 123456789012", "language": "ru"}
        try:
            validate_record(broken, config["topics"])
        except ValueError:
            pass
        else:
            raise AssertionError("PII self-check did not reject an IIN")
        entries = [write_self_check_run(first, sha256(first_data / "dataset_manifest.json"), seed)
                   for seed in (17, 29)]
        experiment = {"schema_version": "pulse109-laya-experiment-v1", "status": "complete",
                      "runs": entries, "claim_boundary": "Synthetic-only self-check."}
        (first / "experiment_manifest.json").write_text(json.dumps(experiment))
        require(verify_experiment(first)["runs"] == 2, "Evidence verifier failed")
        (first / "runs/seed-17/checkpoint/model.safetensors").write_bytes(b"tampered")
        try:
            verify_experiment(first)
        except ValueError:
            pass
        else:
            raise AssertionError("Evidence verifier accepted a modified checkpoint")
    print("PASS: deterministic data, split isolation, PII gate and evidence tamper detection")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--seeds", type=lambda value: [int(item) for item in value.split(",")],
                        default=[17, 29, 43])
    parser.add_argument("--data-seed", type=int, default=20260925)
    parser.add_argument("--variants", type=int, default=8)
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--micro-batch", type=int, default=8)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--gpus", type=int)
    parser.add_argument("--approved-jsonl", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--model-revision")
    args = parser.parse_args()
    if args.self_check:
        self_check()
    elif args.verify_only:
        require(args.output, "--output is required with --verify-only")
        print(json.dumps(verify_experiment(args.output), ensure_ascii=False, indent=2))
    else:
        require(args.output, "--output is required")
        run_experiment(args)


if __name__ == "__main__":
    main()
