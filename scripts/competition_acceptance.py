"""Read-only verification of the persisted Nexent competition evidence."""
from __future__ import annotations

import argparse
import json
import socket
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TASK_ID = "task_2e7fc257054e4ad1b061f384baf04cdd"
V0 = "model_ac98cc13e4f64a70aee486593ce8416d"
V1 = "model_d0c21d0f6f4b499493f9f3c84b793170"
EVOLUTION = "job_2f4c52c012de42058eb4fd6573de556b"
VALIDATION = "job_5d60a075e2844d19b74ed4621e8b6696"
MACHINE_TEST = "job_8e06280ca8874ca3a1991be01effe48f"
DATASET = "dataset_25aed13c9acaa14d"
PROPOSAL = "ontology_proposal_dc9a7666971d42d98dbf86202264cd93"
OCR_PARSE = "parse_asset_bc353b599f7c42d69cac7c92dffcf262"


class Store:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)

    def get(self, kind: str, record_id: str) -> dict:
        row = self.db.execute(
            "SELECT payload FROM records WHERE kind=? AND id=?", (kind, record_id)
        ).fetchone()
        return json.loads(row[0]) if row else {}

    def all(self, kind: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT payload FROM records WHERE kind=? ORDER BY rowid", (kind,)
        ).fetchall()
        return [json.loads(row[0]) for row in rows]


def http_check(url: str) -> tuple[bool, object]:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            data = json.loads(response.read().decode("utf-8"))
            return response.status == 200, data
    except Exception as exc:
        return False, str(exc)


def tcp_check(host: str, port: int) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=3):
            return True, f"{host}:{port} reachable"
    except OSError as exc:
        return False, str(exc)


def main() -> int:
    project = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=project / "data" / "aoi.sqlite3")
    parser.add_argument("--output", type=Path, default=project / "data" / "competition_acceptance.json")
    parser.add_argument("--api-health", default="http://127.0.0.1:8100/health")
    parser.add_argument("--mcp-host", default="127.0.0.1")
    parser.add_argument("--mcp-port", type=int, default=8101)
    args = parser.parse_args()
    store = Store(args.db)
    checks: list[dict] = []

    def add(name: str, passed: bool, evidence: object) -> None:
        checks.append({"name": name, "passed": bool(passed), "evidence": evidence})

    ok, evidence = http_check(args.api_health)
    add("business_api", ok, evidence)
    ok, evidence = tcp_check(args.mcp_host, args.mcp_port)
    add("mcp_port", ok, evidence)

    task = store.get("task", TASK_ID)
    add("task_and_current_model", task.get("initial_model") == V0 and task.get("published_model") == V1, {
        "task_id": task.get("task_id"), "initial_model": task.get("initial_model"),
        "published_model": task.get("published_model"), "validation_version": task.get("validation_version")})

    releases = [r for r in store.all("release") if r.get("task_id") == TASK_ID]
    pairs = [(r.get("from_model"), r.get("to_model")) for r in releases]
    add("v0_to_v1_release_history", len(releases) == 2 and (None, V0) in pairs and (V0, V1) in pairs,
        {"count": len(releases), "releases": releases})

    job = store.get("job", EVOLUTION)
    counts = job.get("training_counts", {})
    add("cumulative_feedback_evolution",
        job.get("status") == "succeeded" and job.get("adaptation_type") == "evolution"
        and job.get("parent_model") == V0 and job.get("model_version") == V1
        and job.get("dataset_version") == DATASET and job.get("feedback_cutoff") == 2
        and counts == {"normal": 101, "defect": 31}, {
            "job_id": job.get("job_id"), "status": job.get("status"), "parent_model": job.get("parent_model"),
            "model_version": job.get("model_version"), "dataset_version": job.get("dataset_version"),
            "feedback_cutoff": job.get("feedback_cutoff"), "training_counts": counts,
            "selected_method": job.get("summary", {}).get("selected_method"),
            "selected_threshold": job.get("summary", {}).get("selected_threshold"),
            "support_oof_metrics": job.get("summary", {}).get("support_oof_metrics")})

    job = store.get("job", VALIDATION)
    comparison = job.get("comparison", {})
    candidate = comparison.get("candidate", {})
    add("frozen_validation_gate", job.get("status") == "succeeded" and comparison.get("passed") is True
        and candidate.get("false_positives") == 0 and candidate.get("false_negatives") == 6
        and candidate.get("balanced_accuracy") == 0.9, {
            "job_id": job.get("job_id"), "validation_version": job.get("validation_version"),
            "passed": comparison.get("passed"), "failures": comparison.get("failures"),
            "candidate": candidate, "baseline": comparison.get("baseline"),
            "policy": comparison.get("policy") or job.get("policy")})

    job = store.get("job", MACHINE_TEST)
    results = job.get("results", [])
    add("v1_machine_test", job.get("status") == "succeeded" and job.get("mode") == "machine_test"
        and job.get("model_version") == V1 and {x.get("result") for x in results} == {"OK", "NG"}, {
            "job_id": job.get("job_id"), "mode": job.get("mode"),
            "model_version": job.get("model_version"), "results": results})

    reviews = [r for r in store.all("review") if r.get("task_id") == TASK_ID]
    add("human_feedback", len(reviews) == 2 and {r.get("label") for r in reviews} == {"normal", "defect"}
        and all(r.get("learning_eligible") is True for r in reviews), {"count": len(reviews), "reviews": reviews})

    ontology = store.get("ontology_version", "ontology_v4")
    mappings = ontology.get("standard_mappings", [])
    iso = [m for m in mappings if m.get("standard_name") == "ISO 2859-1:1999"]
    proposal = store.get("ontology_proposal", PROPOSAL)
    add("reviewed_ontology_evolution", ontology.get("parent_version") == "ontology_v3"
        and proposal.get("status") == "published" and proposal.get("published_version") == "ontology_v4"
        and len(iso) == 1, {"version": "ontology_v4", "parent_version": ontology.get("parent_version"),
            "proposal_id": proposal.get("proposal_id"), "proposal_status": proposal.get("status"),
            "iso_mapping": iso, "trace_id": proposal.get("trace_id")})

    analysis = next((item for item in reversed(store.all("quality_analysis"))
        if item.get("decision_basis", {}).get("evidence_chunks") == 3
        and item.get("decision_basis", {}).get("graph_nodes") == 10
        and item.get("decision_basis", {}).get("unique_graph_edges") == 15
        and item.get("decision_basis", {}).get("graph_paths") == 30), {})
    add("cross_document_retrieval_and_graph", bool(analysis), {
        "analysis_id": analysis.get("analysis_id"), "trace_id": analysis.get("trace_id"),
        "decision_basis": analysis.get("decision_basis"),
        "asset_ids": [a.get("asset_id") for a in analysis.get("assets", [])]})

    parsed = store.get("asset_parse", OCR_PARSE)
    chunks = parsed.get("chunks", [])
    locators = [chunk.get("locator", {}) for chunk in chunks]
    fields = ("page", "bbox", "polygon", "confidence")
    add("ocr_evidence_localization", parsed.get("status") == "parsed"
        and parsed.get("parser_version") == "unified-v2-ocr" and len(chunks) == 7
        and all(all(field in locator for field in fields) for locator in locators), {
            "parse_id": parsed.get("parse_id"), "asset_id": parsed.get("asset_id"),
            "parser_version": parsed.get("parser_version"), "chunk_count": len(chunks),
            "confidence_range": [min((x.get("confidence", 0) for x in locators), default=None),
                                 max((x.get("confidence", 0) for x in locators), default=None)],
            "trace_id": parsed.get("trace_id")})

    skill_paths = [project / "skills" / name / "SKILL.md" for name in
                   ("asset-recognition", "quality-analysis", "ontology-update", "aoi-task-adaptation")]
    skill_zips = [project / "skills" / "dist" / f"{name}.zip" for name in
                  ("asset-recognition", "quality-analysis", "ontology-update", "aoi-task-adaptation")]
    required_skill_files = skill_paths + skill_zips
    add("skill_templates", all(path.is_file() for path in required_skill_files),
        [str(path.relative_to(project)) for path in required_skill_files])

    passed = all(item["passed"] for item in checks)
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "overall_passed": passed,
              "database": str(args.db), "summary": {"passed": sum(c["passed"] for c in checks),
              "total": len(checks)}, "checks": checks}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for item in checks:
        print(("PASS" if item["passed"] else "FAIL") + "  " + item["name"])
    print(f"Result: {report['summary']['passed']}/{report['summary']['total']} passed")
    print(f"Report: {args.output}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
