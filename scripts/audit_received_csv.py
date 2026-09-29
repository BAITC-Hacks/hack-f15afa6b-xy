"""Local, read-only aggregate audit. Never emits source values or exception text.

Standard-library only. No networking, raw samples, categorical values, or IDs
are written. Candidate distinctness uses hashes retained only in process memory.
Run: python scripts/audit_received_csv.py --source-dir <local CSV folder>
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


DATE_PATTERNS = (
    ("ISO_YYYY-MM-DD_optional_time", re.compile(
        r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?(?:Z|[+-]\d{2}:?\d{2})?)?")),
    ("DMY_DD.MM.YYYY_optional_time", re.compile(
        r"\d{1,2}\.\d{1,2}\.\d{4}(?: \d{1,2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?)?")),
    ("DMY_DD-MM-YYYY_optional_time", re.compile(
        r"\d{1,2}-\d{1,2}-\d{4}(?: \d{1,2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?)?")),
)

UNPARSED_SHAPES = (
    ("dot_date_two_digit_year_century_unverified", re.compile(r"\d{1,2}\.\d{1,2}\.\d{2}(?:[ T].*)?")),
    ("dot_date_year_first_unsupported", re.compile(r"\d{4}\.\d{1,2}\.\d{1,2}(?:[ T].*)?")),
    ("iso_unpadded_date_unsupported", re.compile(r"\d{4}-\d{1,2}-\d{1,2}(?:[ T].*)?")),
    ("dot_date_with_other_time_delimiter_unsupported", re.compile(r"\d{1,2}\.\d{1,2}\.\d{4}[,;].*")),
    ("numeric_integer_10_digits_epoch_unverified", re.compile(r"\d{10}")),
    ("numeric_integer_13_digits_epoch_unverified", re.compile(r"\d{13}")),
    ("numeric_other_encoding_unverified", re.compile(r"[-+]?\d+(?:\.\d+)?")),
    ("slash_date_year_first", re.compile(r"\d{4}/\d{1,2}/\d{1,2}(?:[ T].*)?")),
    ("slash_date_order_ambiguous", re.compile(r"\d{1,2}/\d{1,2}/\d{4}(?:[ T].*)?")),
    ("contains_alphabetic_characters", re.compile(r".*[^\W\d_].*", re.DOTALL)),
)


def fraction(numerator, denominator):
    return round(numerator / denominator, 6) if denominator else None


def date_value(value):
    """Only explicit non-ambiguous date orders; no inferred slash date order."""
    for name, pattern in DATE_PATTERNS:
        if not pattern.fullmatch(value):
            continue
        try:
            if name.startswith("ISO"):
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            else:
                date_part, _, time_part = value.partition(" ")
                separator = "." if "DD.MM" in name else "-"
                day, month, year = map(int, date_part.split(separator))
                time_values = [int(x) for x in time_part.split(".")[0].split(":")] if time_part else []
                hh, mm, ss = (time_values + [0, 0, 0])[:3]
                parsed = datetime(year, month, day, hh, mm, ss)
            return parsed.date().isoformat(), name, parsed.tzinfo is not None
        except (ValueError, OverflowError):
            return None
    return None


class DateProfile:
    def __init__(self):
        self.nonempty = 0
        self.parsed = 0
        self.min_date = None
        self.max_date = None
        self.formats = Counter()
        self.unparsed_shapes = Counter()
        self.explicit_timezone = 0

    def add(self, value):
        if not value:
            return
        self.nonempty += 1
        result = date_value(value)
        if result is None:
            shape = next((name for name, pattern in UNPARSED_SHAPES
                          if pattern.fullmatch(value)), "other_unsupported_or_invalid_shape")
            self.unparsed_shapes[shape] += 1
            return
        date, name, explicit_timezone = result
        self.parsed += 1
        self.formats[name] += 1
        self.explicit_timezone += int(explicit_timezone)
        self.min_date = date if self.min_date is None else min(self.min_date, date)
        self.max_date = date if self.max_date is None else max(self.max_date, date)

    def result(self, records):
        return {
            "nonempty_records": self.nonempty,
            "empty_records": records - self.nonempty,
            "parsed_records": self.parsed,
            "unparsed_nonempty_records": self.nonempty - self.parsed,
            "parsed_fraction_of_nonempty": fraction(self.parsed, self.nonempty),
            "parsed_min_date": self.min_date,
            "parsed_max_date": self.max_date,
            "explicit_format_counts": dict(self.formats),
            "unparsed_shape_counts": dict(self.unparsed_shapes),
            "records_with_explicit_timezone": self.explicit_timezone,
            "timezone_semantics": "unverified",
            "intake_event_semantics": "unverified",
            "history_completeness": "unverified",
        }


class TextProfile:
    def __init__(self, target_columns):
        self.nonempty = 0
        self.lengths = Counter()
        self.digests = set()
        self.both_nonempty = Counter()
        self.equal = Counter()
        self.target_columns = target_columns

    def add(self, value, values):
        if not value:
            return
        self.nonempty += 1
        self.lengths[len(value)] += 1
        self.digests.add(hashlib.sha256(value.encode("utf-8")).digest())
        for column in self.target_columns:
            target = values[column]
            if target:
                self.both_nonempty[column] += 1
                self.equal[column] += int(value == target)

    def percentile(self, percentile):
        if not self.nonempty:
            return None
        rank = max(1, math.ceil(self.nonempty * percentile))
        cumulative = 0
        for length, count in sorted(self.lengths.items()):
            cumulative += count
            if cumulative >= rank:
                return length

    def result(self, records):
        return {
            "semantics": "unverified_candidate_not_validated_original_text_or_resolution",
            "nonempty_records": self.nonempty,
            "nonempty_fraction": fraction(self.nonempty, records),
            "distinct_fraction_of_nonempty": fraction(len(self.digests), self.nonempty),
            "length_characters_nonempty": {
                "min": min(self.lengths) if self.lengths else None,
                "p25": self.percentile(0.25), "p50": self.percentile(0.5),
                "p75": self.percentile(0.75), "p95": self.percentile(0.95),
                "max": max(self.lengths) if self.lengths else None,
            },
            "exact_equality_against_candidate_targets": {
                column: {
                    "both_nonempty_records": self.both_nonempty[column],
                    "equal_records": self.equal[column],
                    "equal_fraction_of_both_nonempty": fraction(
                        self.equal[column], self.both_nonempty[column]),
                } for column in self.target_columns
            },
        }


def audit_file(path, expected_columns, expected_bytes, fields):
    result = {"status": "not_read", "expected_size_bytes": expected_bytes}
    try:
        result["size_bytes"] = path.stat().st_size
        result["size_matches_inventory"] = result["size_bytes"] == expected_bytes
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            header_line = handle.readline()
            selected = None
            for delimiter in (",", ";", "\t"):
                try:
                    candidate = next(csv.reader([header_line], delimiter=delimiter, strict=True))
                except (csv.Error, StopIteration):
                    continue
                if candidate == expected_columns:
                    selected = delimiter
                    break
            result["header_matches_inventory"] = selected is not None
            if selected is None:
                result["status"] = "header_mismatch_content_not_profiled"
                return result
            result["columns"] = expected_columns
            result["encoding"] = "utf-8-sig_strict"
            result["delimiter"] = selected
            handle.seek(0)
            reader = csv.reader(handle, delimiter=selected, strict=True)
            next(reader)
            text_columns = {
                column for key in ("original_text", "resolution")
                for column in (fields.get(key) or {}).get("columns", [])
            }
            # com_exp is structurally ambiguous; this diagnostic does not label it
            # as original intake text. No field values leave the Python process.
            text_columns.update({"com_exp"}.intersection(expected_columns))
            target_columns = {
                column for column in expected_columns if column in {
                    "direction", "category", "category_name", "sub_category",
                    "service", "service_name", "servicelevel1", "servicelevel2",
                    "servicelevel3", "status", "current_project", "contractor",
                    "executor_gov_org", "type", "request_type",
                }
            }
            texts = {column: TextProfile(sorted(target_columns - {column}))
                     for column in sorted(text_columns)}
            dates = {column: DateProfile() for column in
                     (fields.get("event_time") or {}).get("columns", [])}
            parsed_records = valid_records = blank_records = malformed_records = 0
            parsing_complete = True
            while True:
                try:
                    row = next(reader)
                except StopIteration:
                    break
                except csv.Error:
                    parsing_complete = False
                    break
                parsed_records += 1
                if not row or not any(value.strip() for value in row):
                    blank_records += 1
                    continue
                if len(row) != len(expected_columns):
                    malformed_records += 1
                    continue
                valid_records += 1
                values = {column: value.strip() for column, value in zip(expected_columns, row)}
                for column, profile in texts.items():
                    profile.add(values[column], values)
                for column, profile in dates.items():
                    profile.add(values[column])
            result.update({
                "status": "complete" if parsing_complete else "csv_parse_error_partial",
                "parsed_data_records": parsed_records,
                "valid_width_nonblank_records": valid_records,
                "blank_records": blank_records,
                "malformed_width_records": malformed_records,
                "csv_parse_errors": 0 if parsing_complete else 1,
                "complete_record_count_verified": parsing_complete,
                "unique_complaints_verified": False,
                "candidate_field_profiles": {column: profile.result(valid_records)
                                             for column, profile in texts.items()},
                "date_field_profiles": {column: profile.result(valid_records)
                                        for column, profile in dates.items()},
            })
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        # Exception messages can contain source bytes, paths or row values.
        result["status"] = "local_read_or_parse_failure"
        result["error_type"] = type(exc).__name__
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "local_profile.json")
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.source_dir.resolve()):
        raise ValueError("Output must be outside the read-only source directory")
    inventory = json.loads((args.repo / "planning/data_inventory.json").read_text(encoding="utf-8"))
    matrix = json.loads((args.repo / "planning/data_matrix.json").read_text(encoding="utf-8"))
    schemas = {entry["region"]: entry["columns"] for entry in inventory["datasets"]}
    sizes = {entry["file_id"]: entry["size_bytes"]
             for entry in inventory["organizer_source"]["files"]}
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "local_received_files_aggregate_structural_profile_only",
        "method": "stdlib_csv_strict_utf8_streaming_no_raw_values_exported",
        "normalization": "surrounding_whitespace_trimmed_for_nonempty_lengths_and_equality",
        "count_unit": "parsed_CSV_records_not_verified_unique_complaints",
        "distinct_method": "SHA256_distinct_count_in_memory_only_hashes_never_written",
        "language_coverage_verified": False,
        "source_field_semantics_verified": False,
        "publication_permission_verified": False,
        "local_bytes_equal_size_not_full_file_identity": True,
        "files": [],
    }
    csv.field_size_limit(16 * 1024 * 1024)
    for entry in matrix["field_matrix"]:
        source = entry["source"]
        if source is None:
            continue
        names = source.get("file_names", [source.get("file_name")])
        ids = source.get("file_ids", [source.get("file_id")])
        for name, file_id in zip(names, ids):
            result = audit_file(args.source_dir / name, schemas[entry["region"]],
                                sizes[file_id], entry["fields"])
            result.update({"region": entry["region"], "file_name": name, "file_id": file_id})
            report["files"].append(result)
            # Safe progress: only fixed status and count, no source-derived values.
            print(json.dumps({"completed_files": len(report["files"]),
                              "status": result["status"],
                              "records": result.get("parsed_data_records")}, ensure_ascii=True))
    report["complete_files"] = sum(item["status"] == "complete" for item in report["files"])
    report["total_records_complete_files"] = sum(
        item["parsed_data_records"] for item in report["files"] if item["status"] == "complete")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if report["complete_files"] == len(report["files"]) else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "audit_configuration_failure", "error_type": type(exc).__name__}))
        raise SystemExit(2) from None
