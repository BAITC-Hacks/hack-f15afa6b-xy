"""Build safe monthly aggregates from the organizer CSV package."""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
from collections import Counter
from datetime import datetime
from pathlib import Path


REGIONS = {
    "Акмолинская": "KZ-AKM",
    "Алматинская": "KZ-ALM",
    "Восточно-Казахстанская": "KZ-VKO",
    "Карагандинская": "KZ-KAR",
    "Костанайская": "KZ-KOS",
    "Туркестанская": "KZ-TUR",
    "Павлодарская": "KZ-PAV",
}

TOPIC_WORDS = {
    "street_lighting": ("уличн", "освещ", "фонар", "көше жары"),
    "sewerage": ("канализац", "ливнев", "кәріз", "водоотвед"),
    "water_supply": ("водоснаб", "водоканал", "су арнас", "холодн.*вод", "горяч.*вод"),
    "waste_management": ("тбо", "мусор", "отход", "қоқыс"),
    "public_transport": ("общественн.*транспорт", "пассажирск", "автобус", "маршрут"),
    "heating": ("отоплен", "теплоснаб", "теплосет", "жылу"),
    "roads": ("дорож", "дорог", "асфальт", "ямоч", "шұңқыр"),
    "landscaping": ("благоустр", "озелен", "насажден", "парки?", "сквер"),
    "electricity": ("электроснаб", "электричес", "энергоснаб", "электросет"),
    "housing_maintenance": ("жкх", "жилищ", "коммунальн", "подъезд", "лифт", "кск"),
}

DATE_FIELDS = ("creation_date", "createddate", "created_date", "submission_date")
TEXT_FIELDS = (
    "category", "request_subject", "servicelevel1", "servicelevel2", "servicelevel3",
    "service", "contractor", "current_project", "direction", "sub_category", "executor_gov_org",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def region_id(path: Path) -> str | None:
    return next((code for name, code in REGIONS.items() if name in path.name), None)


def month(value: str) -> str | None:
    value = value.strip()
    for pattern in (r"^(\d{4})-(\d{2})", r"^\d{2}\.(\d{2})\.(\d{4})", r"^(\d{2})/\d{2}/(\d{2,4})"):
        match = re.match(pattern, value)
        if not match:
            continue
        if pattern.startswith("^(\\d{4})"):
            year, number = match.groups()
        elif pattern.startswith("^\\d{2}\\."):
            number, year = match.groups()
        else:
            number, year = match.groups()
            year = f"20{year}" if len(year) == 2 else year
        try:
            return datetime(int(year), int(number), 1).strftime("%Y-%m")
        except ValueError:
            return None
    return None


def topic(row: dict[str, str]) -> str | None:
    text = " ".join(str(row.get(field) or "") for field in TEXT_FIELDS).lower()
    for topic_id, patterns in TOPIC_WORDS.items():
        if any(re.search(pattern, text) for pattern in patterns):
            return topic_id
    return None


def build(source: Path, output: Path) -> dict[str, int]:
    counts: Counter[tuple[str, str, str, str]] = Counter()
    seen_hashes: set[str] = set()
    read_rows = mapped_rows = 0
    for path in sorted(source.glob("*.csv")):
        code = region_id(path)
        if not code:
            continue
        digest = file_sha256(path)
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            date_field = next((field for field in DATE_FIELDS if field in (reader.fieldnames or [])), None)
            if not date_field:
                continue
            for row in reader:
                read_rows += 1
                period, topic_id = month(row.get(date_field, "")), topic(row)
                if period and topic_id:
                    counts[(period, code, topic_id, digest)] += 1
                    mapped_rows += 1
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("month", "region_id", "topic", "count", "source_sha256"))
        for (period, code, topic_id, digest), count in sorted(counts.items()):
            writer.writerow((period, code, topic_id, count, digest))
    return {"files": len(seen_hashes), "rows": read_rows, "mapped": mapped_rows, "aggregates": len(counts)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path("fixtures/organizer_monthly_aggregates.csv"))
    args = parser.parse_args()
    print(build(args.source, args.output))
