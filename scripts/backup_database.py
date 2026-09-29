"""Create and verify recoverable SQLite backups."""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from object_storage import object_storage


def check_backup(path, expected=None):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if expected and digest != expected:
        raise ValueError("Backup checksum does not match")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("SQLite quick_check failed")
    return digest


def backup(database, output_dir, keep):
    if not database.is_file():
        raise FileNotFoundError(f"Database does not exist: {database}")
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = datetime.now(timezone.utc).strftime("pulse109-%Y%m%dT%H%M%SZ.db")
    target, temporary = output_dir / name, output_dir / (name + ".tmp")
    try:
        with sqlite3.connect(database) as source, sqlite3.connect(temporary) as destination:
            source.backup(destination)
        os.chmod(temporary, 0o600)
        digest = check_backup(temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    checksum = target.with_suffix(target.suffix + ".sha256")
    checksum.write_text(f"{digest}  {target.name}\n", encoding="ascii")
    os.chmod(checksum, 0o600)
    storage = object_storage()
    object_key = storage.put_backup(target.name, target.read_bytes(), digest) if storage.enabled else None
    old = sorted(output_dir.glob("pulse109-*.db"), reverse=True)[keep:]
    for path in old:
        path.unlink(missing_ok=True);path.with_suffix(path.suffix + ".sha256").unlink(missing_ok=True)
    return {"status": "ok", "path": str(target), "sha256": digest,
            "sqlite_quick_check": "ok", "object_key": object_key}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path(os.environ.get("DATABASE_PATH", "data/pulse109.db")))
    parser.add_argument("--output-dir", type=Path, default=Path(os.environ.get("P109_BACKUP_DIR", "data/backups")))
    parser.add_argument("--keep", type=int, default=7)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.keep < 1:
        parser.error("--keep must be at least 1")
    if args.verify:
        checksum = args.verify.with_suffix(args.verify.suffix + ".sha256")
        expected = checksum.read_text(encoding="ascii").split()[0]
        result = {"status": "ok", "path": str(args.verify),
                  "sha256": check_backup(args.verify, expected), "sqlite_quick_check": "ok"}
    else:
        result = backup(args.database, args.output_dir, args.keep)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
