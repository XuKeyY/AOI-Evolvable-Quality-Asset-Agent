"""Run the fixed cross-document evidence, graph, ontology and trace acceptance case."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8100")
    parser.add_argument("--publish-ontology", action="store_true")
    parser.add_argument("--output", default=str(ROOT / "data" / "demo_knowledge_acceptance.json"))
    args = parser.parse_args()
    client = httpx.Client(base_url=args.api, timeout=60, trust_env=False)

    assets = []
    for path in (ROOT / "demo" / "quality_spec.txt", ROOT / "demo" / "inspection_records.csv"):
        with path.open("rb") as stream:
            response = client.post("/assets/upload", files={"file": (path.name, stream)})
        response.raise_for_status()
        asset = response.json()
        parsed = client.post(f"/assets/{asset['asset_id']}/parse").json()
        assets.append({"asset": asset, "parsed": parsed})

    asset_ids = [item["asset"]["asset_id"] for item in assets]
    evidence = client.post("/evidence/search", json={"query": "LOT-2026-A", "asset_ids": asset_ids, "limit": 20}).json()
    graph = client.post("/quality-graph/query", json={"query": "LOT-2026-A", "max_hops": 3}).json()
    assert {item["asset_id"] for item in evidence["results"]} == set(asset_ids)
    assert graph["paths"], "Cross-document graph returned no paths"

    proposal = client.post("/ontology/proposals", json={
        "asset_ids": [asset_ids[0]], "rationale": "固定演示：新增质量规范与标准映射", "changes": []
    }).json()
    ontology = {"proposal": proposal}
    if args.publish_ontology:
        reviewed = client.post(f"/ontology/proposals/{proposal['proposal_id']}/review", json={
            "decision": "approve", "operator": "competition_demo", "note": "已核对原文及标准编号"
        }).json()
        published = client.post(f"/ontology/proposals/{proposal['proposal_id']}/publish", json={"operator": "competition_demo"}).json()
        ontology.update(review=reviewed, published=published)
    trace = client.get(f"/traces/{proposal['trace_id']}").json()

    report = {
        "passed": True,
        "asset_ids": asset_ids,
        "parse_statuses": [item["parsed"]["status"] for item in assets],
        "evidence_asset_ids": sorted({item["asset_id"] for item in evidence["results"]}),
        "evidence_count": len(evidence["results"]),
        "graph_node_count": len(graph["nodes"]),
        "graph_edge_count": len(graph["edges"]),
        "graph_path_count": len(graph["paths"]),
        "ontology": ontology,
        "trace": trace,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("passed", "asset_ids", "evidence_count", "graph_path_count")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
