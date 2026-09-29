"""Pulse 109 — operator queue listing check: views, filters, pagination, stable order.

Uses the real API on an isolated synthetic database. The first scenario covers
an old pending complaint remaining reachable among more than 30 newer confirmed
complaints; then pages, filters, counters and ordering rules are verified.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from check_clarification import start_server, stop_server
from smoke import find_free_port, http_request


def items_of(base_url: str, query: str) -> dict:
    status, data = http_request(f"{base_url}/api/complaints?{query}")
    assert status == 200, f"Queue request '{query}' returned {status}"
    assert isinstance(data.get("items"), list), f"Envelope must expose items: {list(data)[:5]}"
    return data


def run_check():
    repo_root = Path(__file__).resolve().parents[1]
    tmp_dir = tempfile.TemporaryDirectory()
    db_path = Path(tmp_dir.name) / "queue.db"
    proc, base_url = start_server(repo_root, db_path, find_free_port())
    print(f"[*] Queue check server: {base_url}")
    print(f"[*] Isolated temporary database: {db_path}\n")

    try:
        # 1. Old pending complaint stays reachable among >30 newer confirmed ones
        status, created = http_request(
            f"{base_url}/api/intake", "POST", {"text": "Старая необработанная заявка", "region_id": "KZ-ABA"}
        )
        assert status == 201
        old_pending = created["id"]
        confirmed_ids = []
        for i in range(32):
            status, created = http_request(
                f"{base_url}/api/intake", "POST", {"text": f"Синтетическая закрытая заявка {i}", "region_id": "KZ-ABA"}
            )
            assert status == 201
            status, _ = http_request(
                f"{base_url}/api/complaints/{created['id']}/confirm",
                "POST",
                {"topic": "roads", "service_id": "srv_roads", "priority": "normal"},
            )
            assert status == 200
            confirmed_ids.append(created["id"])
        data = items_of(base_url, "view=pending&region_id=KZ-ABA&page_size=50")
        assert old_pending in [c["id"] for c in data["items"]], "Old pending must remain visible"
        assert data["total"] == 1 and data["view_counts"]["confirmed"] == 32
        print("PASS 1: old pending reachable with 32 newer confirmed; filtered counters consistent")

        # 2. Pages have no gaps or duplicates on an unchanged set and are stable
        seen = []
        sizes = []
        for page in (1, 2, 3, 4):
            data = items_of(base_url, f"view=confirmed&region_id=KZ-ABA&page={page}&page_size=10")
            assert data["pages"] == 4 and data["total"] == 32 and data["page"] == page
            sizes.append(len(data["items"]))
            seen.extend(c["id"] for c in data["items"])
        assert sizes == [10, 10, 10, 2], f"Unexpected page sizes: {sizes}"
        assert len(set(seen)) == 32 and set(seen) == set(confirmed_ids), "Pages must not gap or repeat"
        again = items_of(base_url, "view=confirmed&region_id=KZ-ABA&page=2&page_size=10")
        assert [c["id"] for c in again["items"]] == seen[10:20], "Page contents must be stable"
        print("PASS 2: 4 pages without gaps/duplicates; repeated page returns identical items")

        # 3. Ordering: human-confirmed urgent first, then older; id breaks equal timestamps
        oldest_normal = confirmed_ids[0]
        status, urgent = http_request(
            f"{base_url}/api/intake", "POST", {"text": "Срочная подтверждённая заявка", "region_id": "KZ-ALA"}
        )
        urgent_id = urgent["id"]
        status, _ = http_request(
            f"{base_url}/api/complaints/{urgent_id}/confirm",
            "POST",
            {"topic": "heating", "service_id": "srv_teplo", "priority": "urgent"},
        )
        assert status == 200
        data = items_of(base_url, "view=all&page_size=50")
        order = [c["id"] for c in data["items"]]
        first = data["items"][0]
        assert first["decision_status"] == "confirmed" and first["priority"] == "urgent", "Confirmed urgent must lead"
        assert order.index(urgent_id) < order.index(oldest_normal), "Confirmed urgent must precede older normal ones"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE complaints SET ingested_at = '2020-01-01T00:00:00+00:00', received_at = NULL "
                "WHERE id IN (?, ?)",
                (confirmed_ids[1], confirmed_ids[2]),
            )
        data = items_of(base_url, "view=confirmed&region_id=KZ-ABA&page_size=50")
        tied = [c["id"] for c in data["items"][:2]]
        assert tied == sorted(tied), f"Equal timestamps must order by id: {tied}"
        print("PASS 3: confirmed urgent leads; older first; equal timestamps tie-broken by id")

        # 4. A needs_clarification complaint with an urgent proposal stays visible in its view
        status, cl = http_request(
            f"{base_url}/api/intake", "POST", {"text": "Авария, непонятно что происходит", "region_id": "KZ-AST"}
        )
        cl_id = cl["id"]
        status, cls = http_request(f"{base_url}/api/complaints/{cl_id}/classify", "POST")
        assert status == 200 and cls["proposal"]["priority"] == "urgent" and cls["proposal"]["topic"] is None
        status, _ = http_request(
            f"{base_url}/api/complaints/{cl_id}/clarification",
            "POST",
            {"reason": "unclear_event", "question": "Что произошло?", "actor": "operator_queue"},
        )
        assert status == 200
        clar = items_of(base_url, "view=clarification&page_size=50")
        assert cl_id in [c["id"] for c in clar["items"]], "Urgent clarification stays visible in its view"
        pending = items_of(base_url, "view=pending&page_size=50")
        assert cl_id not in [c["id"] for c in pending["items"]]
        urgent_filter = items_of(base_url, "priority=urgent&page_size=50&view=all")
        assert cl_id not in [c["id"] for c in urgent_filter["items"]], "Proposal is not a confirmed urgency filter hit"
        assert urgent_filter["total"] > 0
        print("PASS 4: needs_clarification visible in its view; proposal urgency is not confirmed urgency")

        # 5. Unknown urgency is its own filter and never merged with normal
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE complaints SET priority = 'needs_review' WHERE id = 'syn-020'")
        unknown = items_of(base_url, "priority=unknown&page_size=50&view=all")
        unknown_ids = [c["id"] for c in unknown["items"]]
        assert "syn-020" in unknown_ids and unknown["total"] == len(unknown_ids)
        assert all(c["priority"] not in {"normal", "urgent"} for c in unknown["items"])
        normal = items_of(base_url, "priority=normal&view=confirmed&page_size=50")
        assert "syn-020" not in [c["id"] for c in normal["items"]]
        print("PASS 5: unknown/legacy urgency has its own filter and is not normal")

        # 6. Region filter, empty view and counters stay consistent
        ala = items_of(base_url, "view=all&region_id=KZ-ALA&page_size=50")
        assert all(c["region_id"] == "KZ-ALA" for c in ala["items"])
        empty = items_of(base_url, "view=pending&region_id=KZ-ALA&page_size=50")
        assert empty["items"] == [] and empty["total"] == 0 and empty["pages"] == 1
        counts = ala["view_counts"]
        assert counts["all"] == counts["pending"] + counts["clarification"] + counts["confirmed"]
        print("PASS 6: region filter, empty selection and view counters are consistent")

        # 7. Out-of-range page clamps to the last available page
        clamped = items_of(base_url, "view=confirmed&region_id=KZ-ABA&page=99&page_size=10")
        assert clamped["page"] == 4 and len(clamped["items"]) == 2 and clamped["total"] == 32
        print("PASS 7: out-of-range page clamps to the last page")

        # 8. Input validation
        for query in (
            "view=bogus",
            "priority=bogus",
            "region_id=KZ-INVALID",
            "page=0",
            "page_size=0",
            "page_size=51",
        ):
            status, _ = http_request(f"{base_url}/api/complaints?{query}")
            assert status == 422, f"Query '{query}' expected 422, got {status}"
        print("PASS 8: invalid view, priority, region, page and page_size are rejected with 422")

        print("\n========================================================")
        print("ALL 8 QUEUE CHECKS PASSED SUCCESSFULLY!")
        print("========================================================")
    finally:
        print("[*] Terminating test server process...")
        stop_server(proc)
        tmp_dir.cleanup()
        print("[*] Temporary test resources cleaned up.")


if __name__ == "__main__":
    run_check()
