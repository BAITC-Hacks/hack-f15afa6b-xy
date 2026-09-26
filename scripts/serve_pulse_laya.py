"""Serve one hash-verified promoted Pulse109 Laya checkpoint."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

try:
    from promote_laya_checkpoint import hash_tree, load_json, require
except ImportError:
    from scripts.promote_laya_checkpoint import hash_tree, load_json, require


def validate_promotion(version_dir):
    version_dir = Path(version_dir).resolve()
    require(version_dir.is_dir(), f"Promoted model directory is missing: {version_dir}")
    manifest_path = version_dir / "promotion.json"
    require(manifest_path.is_file(), f"Promotion manifest is missing: {manifest_path}")
    manifest = load_json(manifest_path)
    require(manifest.get("schema_version") == "pulse109-laya-promotion-v1",
            "Unknown promotion manifest schema")
    require(manifest.get("version") == version_dir.name,
            "Promotion version does not match its directory")
    require(manifest.get("checkpoint", {}).get("directory") == "checkpoint",
            "Promotion manifest has an invalid checkpoint directory")
    checkpoint = version_dir / "checkpoint"
    expected = manifest.get("checkpoint", {}).get("files")
    require(isinstance(expected, dict) and expected, "Promotion manifest has no checkpoint hashes")
    require(hash_tree(checkpoint) == expected, "Promoted checkpoint hash mismatch")
    require(expected["model.safetensors"] == manifest["version"],
            "Promoted model hash does not match its version")
    return checkpoint, manifest


def create_service():
    version_dir = os.environ.get("LAYA_PROMOTED_MODEL") or os.environ.get("LAYA_CHECKPOINT_PATH")
    require(version_dir, "LAYA_PROMOTED_MODEL or LAYA_CHECKPOINT_PATH is required")
    checkpoint, _ = validate_promotion(version_dir)
    device = os.environ.get("LAYA_DEVICE", "cuda")
    import laya
    from laya import Router
    from laya.serve import create_app

    agent = laya.load(str(checkpoint), device=device)
    router = Router(device=device, default="multilingual", max_loaded=1)
    router.attach("multilingual", agent)
    return create_app(router)


def self_check():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        version = "0" * 64
        version_dir = root / version
        checkpoint = version_dir / "checkpoint"
        (checkpoint / "encoder").mkdir(parents=True)
        (checkpoint / "tokenizer").mkdir()
        (checkpoint / "model.safetensors").write_bytes(b"weights")
        (checkpoint / "rl_agent_config.json").write_text("{}\n")
        (checkpoint / "encoder/config.json").write_text("{}\n")
        (checkpoint / "tokenizer/tokenizer.json").write_text("{}\n")
        files = hash_tree(checkpoint)
        actual_version = files["model.safetensors"]
        actual_dir = root / actual_version
        version_dir.rename(actual_dir)
        (actual_dir / "promotion.json").write_text(
            json.dumps({
                "schema_version": "pulse109-laya-promotion-v1",
                "version": actual_version,
                "checkpoint": {"directory": "checkpoint", "files": files},
            }) + "\n")
        validate_promotion(actual_dir)
        (actual_dir / "checkpoint/rl_agent_config.json").write_text('{"tampered":true}\n')
        try:
            validate_promotion(actual_dir)
        except ValueError:
            pass
        else:
            raise AssertionError("Server accepted a modified promoted checkpoint")
    print("PASS: serving manifest validation and checkpoint tamper detection")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--device", default=os.environ.get("LAYA_DEVICE", "cuda"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    require(args.checkpoint is not None, "--checkpoint is required")
    os.environ["LAYA_PROMOTED_MODEL"] = str(args.checkpoint.resolve())
    os.environ["LAYA_DEVICE"] = args.device
    import uvicorn
    uvicorn.run(create_service(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
