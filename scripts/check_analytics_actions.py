"""Run the review workflow against an isolated real HTTP server and SQLite DB."""

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlencode

from check_auth import Client, free_port, wait_for_server
from check_clarification import stop_server


def run():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as temp:
        db = Path(temp) / "analytics.db"
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        env = os.environ | {"DATABASE_PATH": str(db), "PYTHONPATH": str(root),
                            "P109_SIGNUP_INVITE": "analytics-test", "P109_SECURE_COOKIES": "0"}
        env.pop("P109_AUTH_DISABLED", None)

        def start():
            proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
                                     "--port", str(port)], cwd=root, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                wait_for_server(Client(), base)
            except Exception:
                stop_server(proc)
                raise RuntimeError(proc.stderr.read().decode())
            return proc

        process = start()
        try:
            api = base + "/api/analytics/actions"
            anonymous, operator = Client(), Client()
            assert anonymous.request(api)[0] == 401
            assert anonymous.request(api + "/missing", "POST", {})[0] == 401
            status, account, _ = operator.request(base + "/api/auth/signup", "POST", {
                "name": "Analytics tester", "email": "analytics@example.test", "password": "Test-Analytics-48291!",
                "invite_code": "analytics-test", "role": "operator",
            })
            assert status == 201, account
            headers = {"X-CSRF-Token": operator.cookie("pulse109_csrf")}

            def get(**filters):
                status, payload, _ = operator.request(api + "?" + urlencode(filters))
                assert status == 200, payload
                return payload

            def save(item, **changes):
                body = {"data_origin": "synthetic_demo", "revision": 0, "status": "done",
                        "note": "Проверены учебные записи. Сверить повторно после закрытия месяца."} | changes
                return operator.request(api + "/" + item["id"], "POST", body, headers)

            plan = get()
            signals = [item for item in plan["items"] if item["kind"] == "signal"]
            assert len(signals) >= 2
            comparison = plan["comparison"]
            assert comparison["method"] == min(comparison["candidates"], key=lambda key: (
                comparison["candidates"][key]["mae"], comparison["candidates"][key]["smape_percent"]))
            assert plan["feedback"]["reviewed_precision_percent"] is None
            assert all(item["steps"] and item["measure"] for item in plan["items"])
            assert operator.request(api + "?data_origin=invalid")[0] == 422
            assert operator.request(api + "?region_id=invalid")[0] == 422
            assert operator.request(api + "/" + signals[0]["id"], "POST", {})[0] == 403
            print("PASS 1: authenticated scope, CSRF, actionable evidence and existing forecast comparison")

            first, second = signals[:2]
            assert save(first)[0] == 422
            assert save(first, outcome="false_alarm", note="  ")[0] == 422
            assert save(first, outcome="invalid")[0] == 422
            assert save(first, outcome="false_alarm", status="in_progress")[0] == 422
            assert save(first, outcome="false_alarm", note="x" * 1001)[0] == 422
            assert save(first, outcome="false_alarm", data_origin="organizer")[0] == 409
            assert save(plan["items"][0], outcome="confirmed")[0] == 422
            print("PASS 2: incomplete reviews, wrong source, wrong kind and invalid inputs rejected")

            assert save(first, status="in_progress")[0] == 200
            assert save(first, outcome="false_alarm")[0] == 409
            status, saved, _ = save(first, revision=1, outcome="false_alarm")
            assert status == 200 and saved["revision"] == 2 and saved["actor"] == account["user"]["id"]
            assert save(second, outcome="confirmed")[0] == 200
            assert get()["feedback"]["reviewed_precision_percent"] == 50
            assert save(first, revision=2, outcome="data_issue")[0] == 200
            feedback = get()["feedback"]
            assert feedback == {"confirmed": 1, "false_alarm": 0, "data_issue": 1, "reviewed_precision_percent": 100.0}
            print("PASS 3: revisions prevent lost edits; feedback counts latest outcomes and excludes data errors")

            stop_server(process)
            process = start()
            plan = get(region_id=first["region_id"], topic=first["topic"])
            row = next(item for item in plan["items"] if item["id"] == first["id"])
            assert row["review"]["revision"] == 3 and row["review"]["outcome"] == "data_issue"
            assert get(data_origin="organizer")["history"] == []
            with sqlite3.connect(db) as conn:
                assert conn.execute("SELECT COUNT(*) FROM analytics_action_events WHERE action_id=?", (first["id"],)).fetchone()[0] == 3
                assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            print("PASS 4: decisions survive restart, retain audit revisions and stay source/region/topic scoped")

            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE regional_monthly_counts SET count=count+100 WHERE data_origin='synthetic_demo' "
                             "AND region_id=? AND topic=? AND month=?", (first["region_id"], first["topic"], first["period"]))
            assert save(first, revision=3, outcome="confirmed")[0] == 409
            changed = get(region_id=first["region_id"], topic=first["topic"])
            new_signal = next(item for item in changed["items"] if item["kind"] == "signal")
            assert new_signal["id"] != first["id"] and new_signal["review"]["revision"] == 0
            assert any(row["action_id"] == first["id"] for row in changed["history"])
            print("PASS 5: revised evidence needs a new review; previous evidence remains in history")

            with sqlite3.connect(db) as conn:
                conn.execute("DELETE FROM regional_monthly_counts WHERE data_origin='organizer'")
            empty = get(data_origin="organizer")
            assert empty["comparison"] is None and len(empty["items"]) == 1
            assert "нет наблюдений" in empty["items"][0]["evidence"]
            print("PASS 6: absent observations produce a data action, not invented forecast or precision")
        finally:
            stop_server(process)
    print("ALL 6 ANALYTICS ACTION CHECKS PASSED")


if __name__ == "__main__":
    run()
