"""Check backup creation, checksum verification and retention."""

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    database, backups = root / "pulse.db", root / "backups"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE complaints (id TEXT PRIMARY KEY, text TEXT NOT NULL)")
        conn.execute("INSERT INTO complaints VALUES ('PULSE-CHECK', 'recoverable')")
    command = [sys.executable, str(Path(__file__).with_name("backup_database.py")),
               "--database", str(database), "--output-dir", str(backups), "--keep", "2"]
    result = json.loads(subprocess.check_output(command, text=True))
    backup = Path(result["path"])
    assert backup.is_file() and backup.with_suffix(".db.sha256").is_file()
    verified = json.loads(subprocess.check_output(
        [sys.executable, str(Path(__file__).with_name("backup_database.py")), "--verify", str(backup)], text=True
    ))
    assert verified["sha256"] == result["sha256"] and verified["sqlite_quick_check"] == "ok"
    with sqlite3.connect(f"file:{backup}?mode=ro", uri=True) as conn:
        assert conn.execute("SELECT text FROM complaints WHERE id = 'PULSE-CHECK'").fetchone()[0] == "recoverable"
print("PASS: SQLite backup is checksummed, readable, and recoverable")
