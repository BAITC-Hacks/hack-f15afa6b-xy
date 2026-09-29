"""Validate planning artifacts; this does not evaluate the future application."""

import json
import re
from collections import Counter
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    backlog = json.loads((root / "planning/backlog.json").read_text())
    inventory = json.loads((root / "planning/data_inventory.json").read_text())
    matrix = json.loads((root / "planning/data_matrix.json").read_text())
    report = (root / "docs/research.md").read_text()
    issues = backlog["issues"]
    by_id = {issue["id"]: issue for issue in issues}
    requirements = {f"REQ-{n:02d}" for n in range(1, 18)}
    assert len(by_id) == len(issues), "Duplicate issue ID"
    assert set(backlog["requirements"]) == requirements
    covered = set()
    hours = Counter()
    visited = set()

    def visit(issue_id, active):
        assert issue_id in by_id, f"Unknown dependency: {issue_id}"
        assert issue_id not in active, f"Dependency cycle: {issue_id}"
        if issue_id in visited:
            return
        for dependency in by_id[issue_id]["depends_on"]:
            visit(dependency, active | {issue_id})
        visited.add(issue_id)

    for issue in issues:
        assert 1 <= issue["week"] <= len(backlog["milestones"])
        assert issue["owner"] in {"Ильяс", "Ольга", "Нурали"}
        assert issue["hours"] > 0
        assert issue["title"] and issue["acceptance"] and issue["requirements"]
        assert set(issue["requirements"]) <= requirements
        visit(issue["id"], set())
        covered.update(issue["requirements"])
        hours[issue["owner"]] += issue["hours"]
    assert covered == requirements, "Uncovered requirement"

    expected = set(inventory["expected_regions"])
    organizer = inventory["organizer_source"]
    observed = {item["region"] for item in organizer["files"]}
    assert len(expected) == 20 and observed <= expected
    assert len(observed) == organizer["observed_regions"]
    assert len(organizer["files"]) == organizer["observed_csv_files"]
    assert organizer["csv_headers_verified"] <= organizer["observed_csv_files"]
    assert {item["region"] for item in inventory["datasets"]} == observed

    matrix_rows = matrix["field_matrix"]
    matrix_regions = {row["region"] for row in matrix_rows}
    assert matrix_regions == expected
    assert len(matrix_rows) == 20
    listed_rows = [row for row in matrix_rows if row["source"] is not None]
    assert len(listed_rows) == organizer["observed_regions"]
    assert sum(len(row["source"].get("file_ids", [row["source"].get("file_id")])) for row in listed_rows) == organizer["observed_csv_files"]
    assert {row["region"] for row in listed_rows} == observed
    assert set(matrix["coverage"]["missing_regions"]) == expected - observed
    assert all(row["source"] is None for row in matrix_rows if row["region"] not in observed)
    allowed_field_statuses = {"present_in_header", "candidate_unverified", "not_observed"}
    for row in listed_rows:
        for field in row["fields"].values():
            assert field["status"] in allowed_field_statuses
            assert field["columns"] == sorted(field["columns"])

    definitions = re.findall(r"^\[\^(\d+)\]:", report, re.MULTILINE)
    references = set(re.findall(r"\[\^(\d+)\](?!:)", report))
    assert len(definitions) == len(set(definitions)), "Duplicate source definition"
    assert references == set(definitions), "Missing or unused source note"
    for path in root.rglob("*"):
        generated_evidence = (path.is_relative_to(root / "training/evidence")
                              or path.is_relative_to(root / "artifacts"))
        if (path.is_file() and ".git" not in path.parts and ".venv" not in path.parts
                and not generated_evidence and path.suffix in {".md", ".json", ".yml", ".py"}):
            assert len(path.read_text().splitlines()) < 500, f"File too long: {path}"
    print(f"PASS: {len(issues)} tasks, {len(backlog['milestones'])} stages, {len(covered)} requirements; dependency DAG valid")
    print(f"Hours: {sum(hours.values())}; by owner: {dict(hours)}")
    print(f"Data: {len(observed)}/20 regions listed, {organizer['csv_headers_verified']} organizer CSV headers checked; full audit pending")
    print(f"Sources: {len(definitions)} reference groups linked; no app/ML test claimed")


if __name__ == "__main__":
    main()
