"""Synthetic boundary checks for aggregate counting and value suppression."""
import json
import unittest
import uuid
from pathlib import Path

from audit_received_csv import TextProfile, audit_file, date_value


class AuditTests(unittest.TestCase):
    def audit(self, contents, columns=None, fields=None):
        columns = columns or ["request_subject", "creation_date", "direction"]
        fields = fields or {
            "original_text": {"columns": ["request_subject"]},
            "event_time": {"columns": ["creation_date"]},
        }
        path = Path(__file__).parent / ("audit-synthetic-" + uuid.uuid4().hex + ".csv")
        try:
            path.write_bytes(contents)
            return audit_file(path, columns, len(contents), fields)
        finally:
            path.unlink(missing_ok=True)

    def test_csv_record_count_handles_quoted_newline(self):
        result = self.audit(b'request_subject,creation_date,direction\n"safe\nsynthetic",2025-01-01,test\n')
        self.assertEqual(result["parsed_data_records"], 1)
        self.assertEqual(result["valid_width_nonblank_records"], 1)
        self.assertNotIn("synthetic", json.dumps(result))

    def test_blank_and_width_counts_are_distinct(self):
        result = self.audit(b'request_subject,creation_date,direction\n\n,,\ninvalid,width\nx,2025-01-01,x\n')
        self.assertEqual(result["parsed_data_records"], 4)
        self.assertEqual(result["blank_records"], 2)
        self.assertEqual(result["malformed_width_records"], 1)
        self.assertEqual(result["valid_width_nonblank_records"], 1)

    def test_header_mismatch_never_returns_unexpected_header(self):
        result = self.audit(b'private-unexpected-header\nprivate-value\n')
        self.assertEqual(result["status"], "header_mismatch_content_not_profiled")
        self.assertNotIn("private", json.dumps(result))
        self.assertNotIn("parsed_data_records", result)

    def test_csv_error_is_partial_not_complete(self):
        result = self.audit(b'request_subject,creation_date,direction\n"private-unclosed\n')
        self.assertEqual(result["status"], "csv_parse_error_partial")
        self.assertFalse(result["complete_record_count_verified"])
        self.assertNotIn("private", json.dumps(result))

    def test_decode_failure_is_sanitized(self):
        result = self.audit(b'request_subject,creation_date,direction\n\xffprivate-value\n')
        self.assertEqual(result["status"], "local_read_or_parse_failure")
        self.assertEqual(result["error_type"], "UnicodeDecodeError")
        self.assertNotIn("private", json.dumps(result))

    def test_explicit_date_formats_and_rejection(self):
        self.assertEqual(date_value("2025-02-03 04:05:06.123456789")[0], "2025-02-03")
        self.assertEqual(date_value("03.02.2025")[0], "2025-02-03")
        self.assertEqual(date_value("03.02.2025 04:05:06.123")[0], "2025-02-03")
        for value in ["2025-02-30", "03/02/2025", "03.02.25", "unknown"]:
            self.assertIsNone(date_value(value))

    def test_unavailable_statistics_stay_null(self):
        result = self.audit(b'request_subject,creation_date,direction\n')
        profile = result["candidate_field_profiles"]["request_subject"]
        self.assertIsNone(profile["nonempty_fraction"])
        self.assertIsNone(profile["length_characters_nonempty"]["p50"])
        self.assertIsNone(result["date_field_profiles"]["creation_date"]["parsed_min_date"])

    def test_equality_denominator_and_percentile(self):
        profile = TextProfile(["direction"])
        for value, target in [("x", "x"), ("long", ""), ("long", "y"), ("", "y")]:
            profile.add(value, {"direction": target})
        result = profile.result(4)
        self.assertEqual(result["nonempty_fraction"], 0.75)
        self.assertEqual(result["length_characters_nonempty"]["p50"], 4)
        self.assertEqual(result["exact_equality_against_candidate_targets"]["direction"]["equal_fraction_of_both_nonempty"], 0.5)


if __name__ == "__main__":
    unittest.main()
