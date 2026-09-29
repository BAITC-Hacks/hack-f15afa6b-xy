"""Check report numbers, filters, forecast boundaries and actual PDF output."""

import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analytics import init_analytics, report_data
from app import REGIONS, TOPICS
from report_exports import pdf_report, number, month


def check_pdf(report):
    pdf = pdf_report(report)
    assert pdf.startswith(b"%PDF") and b"/FontFile2" in pdf
    assert len(re.findall(rb"/Type /Page\b", pdf)) == 3
    if shutil.which("pdftotext"):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.pdf"
            path.write_bytes(pdf)
            result = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                                    capture_output=True, check=True, text=True).stdout
        assert "Нагрузка и прогноз" in result and "Где сосредоточена нагрузка" in result
        assert report["region"] in result and report["topic"] in result
        assert report["provenance"]["data_origin"] in result
        if report["forecast"]:
            assert "MAE" in result and "sMAPE" in result and "Диапазон" in result
            for point in report["forecast"]["forecast"]:
                assert number(point["value"]) in result and month(point["month"]) in result
        if not report["history"]:
            assert "наблюдения отсутствуют" in result and "Нет данных" in result


def run():
    names = {row["id"]: row["name_ru"] for row in REGIONS}
    topics = {row["id"]: row["name_ru"] for row in TOPICS}
    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        init_analytics(conn)
        for region, topic in ((None, None), ("KZ-ALA", None), (None, "water_supply"),
                              ("KZ-ALA", "water_supply")):
            report = report_data(conn, names, topics, region, topic)
            latest = report["history"][-1]
            assert sum(row["value"] for row in report["categories"]) == latest["count"]
            assert sum(row["value"] for row in report["regions"]) == latest["count"]
            assert not region or all(row["id"] == region for row in report["regions"])
            assert not topic or all(row["id"] == topic for row in report["categories"])
            assert all(not region or row["region_id"] == region for row in report["alerts"])
            assert all(not topic or row["topic"] == topic for row in report["alerts"])
            assert report["forecast"]["filters"] == {"region_id": region, "topic": topic}
            check_pdf(report)
        print("PASS 1: category/region totals reconcile to the same month; all filters and PDF numbers agree")

        report = report_data(conn, names, topics, origin="organizer")
        assert not report["provenance"]["synthetic"]
        assert report["provenance"]["freshness"]["status"] == "stale"
        assert sum(row["value"] for row in report["categories"]) == report["history"][-1]["count"]
        if report["forecast"]["excluded_partial_month"]:
            assert report["history"][-1]["month"] < report["series"][-1]["month"]
        check_pdf(report)
        print("PASS 2: organizer archive stays separate; excluded month is not charted as forecast input")

        conn.execute("DELETE FROM regional_monthly_counts")
        report = report_data(conn, names, topics)
        assert not report["history"] and report["forecast"] is None
        check_pdf(report)
        print("PASS 3: missing data is explicit, not fabricated zero counts or a fake forecast")

        for count in (0, 100):
            conn.execute("DELETE FROM regional_monthly_counts")
            conn.executemany("INSERT INTO regional_monthly_counts VALUES (?, 'KZ-ALA', 'water_supply', ?, 'synthetic_demo', NULL)",
                             [(f"2025-{i:02d}", count) for i in range(1, 8)])
            report = report_data(conn, names, topics)
            assert report["forecast"] and len(report["forecast"]["forecast"]) == 3
            check_pdf(report)
        conn.execute("UPDATE regional_monthly_counts SET count=1 WHERE month='2025-07'")
        report = report_data(conn, names, topics)
        assert report["forecast"] is None, "Six remaining months cannot support a three-month backtest"
        check_pdf(report)
        conn.execute("DELETE FROM regional_monthly_counts WHERE month>'2025-02'")
        report = report_data(conn, names, topics)
        assert report["forecast"] is None
        check_pdf(report)
        print("PASS 4: zero, constant, short and incomplete histories generate valid readable reports")
    print("ALL 4 ANALYTICS REPORT CHECKS PASSED")


if __name__ == "__main__":
    run()
