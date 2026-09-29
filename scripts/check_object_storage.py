"""Small reversible check for the private object storage adapter."""

import argparse
import io
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from object_storage import ObjectStorage


class FakeS3:
    def __init__(self):
        self.objects = {}

    def head_bucket(self, Bucket):
        return {"Bucket": Bucket}

    def put_object(self, **request):
        self.objects[request["Key"]] = request

    def get_object(self, Bucket, Key):
        content = self.objects[Key]["Body"]
        return {"ContentLength": len(content), "Body": io.BytesIO(content)}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)


def check(storage):
    content = b"\x89PNG\r\n\x1a\nprivate-pulse109-check"
    complaint_id = "CHECK-" + uuid.uuid4().hex[:10].upper()
    key = storage.put_media(complaint_id, "photo", "image/png", content)
    assert key and storage.get_media(key) == content
    backup = b"SQLite format 3\0private-backup-check"
    backup_key = storage.put_backup("pulse109-20260927T120000Z.db", backup, "a" * 64)
    assert backup_key and storage.get_backup(backup_key) == backup
    assert storage.check()["status"] == "healthy"
    storage.delete(key)
    storage.delete(backup_key)
    return key


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    storage = ObjectStorage.from_env() if args.live else ObjectStorage(
        "https://objects.example", "pulse109-private", client=FakeS3()
    )
    if args.live and not storage.enabled:
        raise SystemExit("S3 environment is not configured")
    check(storage)
    print("PASS: private object storage put, read, health and delete")


if __name__ == "__main__":
    main()
