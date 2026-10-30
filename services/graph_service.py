from __future__ import annotations

import hashlib
import re
from collections import deque
from typing import Any

from services.document_service import DocumentService
from services.store import Store, identifier, now
from services.trace_service import TraceService


BASE_CONCEPTS = ["产品", "批次", "工序", "检验项", "检测记录", "异常", "人工复核", "标准条款", "处理措施", "文档"]
FIELD_MARKER_RE = re.compile(
    r"(?i)(?:^|(?<=[\s；;，,。]))(?P<label>"
    r"产品(?:名称|编号|号)?|零件(?:名称|编号|号)?|物料(?:名称|编号|号)?|"
    r"批次|批号|lot|工序|制程|检验项|检查项|检测项|"
    r"异常(?:类型|名称)?|缺陷(?:类型|名称)?|不良(?:类型|名称)?|"
    r"处理措施|处置|纠正措施|人工结论|标准(?:名称|编号|号|条款)?"
    r")\s*[:：=]\s*"
)
STANDARD_RE = re.compile(r"(?i)\b(?:GB(?:/T)?|ISO|IEC|DIN|ASTM|Q/[A-Z0-9.-]+)\s*[A-Z0-9./-]+(?:[-:]\d{2,4})?\b")


class GraphService:
    def __init__(self, store: Store, documents: DocumentService, traces: TraceService):
        self.store = store
        self.documents = documents
        self.traces = traces
        if not self.store.all("ontology_version"):
            self.store.put("ontology_version", {
                "id": "ontology_v1", "version": 1, "status": "published", "concepts": BASE_CONCEPTS,
                "relations": ["涉及", "依据", "属于批次", "发现异常", "建议措施", "来源于"],
                "standard_mappings": [], "created_at": now(), "parent_version": None,
            })

    def ingest(self, asset_id: str) -> dict[str, Any]:
        parsed = self.documents.parse(asset_id)
        stale_edge_ids = [
            edge["id"] for edge in self.store.all("graph_edge")
            if edge.get("evidence", {}).get("asset_id") == asset_id
        ]
        self.store.delete_many("graph_edge", stale_edge_ids)
        document = self._put_node("文档", asset_id, parsed["summary"], {"asset_id": asset_id})
        entities = []
        edges = []
        for chunk in parsed["chunks"]:
            for entity_type, name in self._entities(chunk["text"]):
                node = self._put_node(entity_type, name, name, {})
                entities.append(node)
                edge_id = "edge_" + hashlib.sha256(f"{document['id']}|涉及|{node['id']}|{chunk['chunk_id']}".encode()).hexdigest()[:20]
                edge = {
                    "id": edge_id, "source": document["id"], "relation": "涉及", "target": node["id"],
                    "evidence": {"asset_id": asset_id, "chunk_id": chunk["chunk_id"], "locator": chunk["locator"], "text": chunk["text"][:500]},
                    "created_at": now(),
                }
                self.store.put("graph_edge", edge)
                edges.append(edge)
        return {"asset_id": asset_id, "document_node": document, "entities": self._unique(entities), "edges": self._unique(edges)}

    def search_evidence(self, query: str, asset_ids: list[str] | None = None, limit: int = 10) -> dict[str, Any]:
        terms = [term.lower() for term in re.findall(r"[\w\u4e00-\u9fff-]+", query) if len(term) > 1]
        results = []
        for parsed in self.store.all("asset_parse"):
            if asset_ids and parsed["asset_id"] not in asset_ids:
                continue
            for chunk in parsed.get("chunks", []):
                lower = chunk["text"].lower()
                score = sum(lower.count(term) for term in terms) if terms else 1
                if score:
                    results.append({**chunk, "score": score, "parser_version": parsed["parser_version"]})
        results.sort(key=lambda item: (-item["score"], item["asset_id"], item["chunk_id"]))
        return {"query": query, "results": results[: max(1, min(limit, 100))], "total_matches": len(results)}

    def query(self, query: str = "", start_node_id: str | None = None, max_hops: int = 3,
              asset_ids: list[str] | None = None) -> dict[str, Any]:
        max_hops = max(1, min(max_hops, 5))
        nodes = {item["id"]: item for item in self.store.all("graph_node")}
        edges = self.store.all("graph_edge")
        if asset_ids:
            allowed_assets = set(asset_ids)
            edges = [edge for edge in edges if edge.get("evidence", {}).get("asset_id") in allowed_assets]
            allowed_nodes = {endpoint for edge in edges for endpoint in (edge["source"], edge["target"])}
            nodes = {node_id: node for node_id, node in nodes.items() if node_id in allowed_nodes}
        terms = [term.lower() for term in re.findall(r"[\w\u4e00-\u9fff-]+", query) if len(term) > 1]
        starts = [start_node_id] if start_node_id else [node_id for node_id, node in nodes.items()
            if not terms or any(term in (node.get("name", "") + node.get("summary", "")).lower() for term in terms)]
        if not starts and asset_ids:
            starts = [node_id for node_id, node in nodes.items()
                if node.get("properties", {}).get("asset_id") in set(asset_ids)]
        starts = [node_id for node_id in starts if node_id in nodes]
        adjacency: dict[str, list[dict[str, Any]]] = {}
        for edge in edges:
            adjacency.setdefault(edge["source"], []).append(edge)
            adjacency.setdefault(edge["target"], []).append(edge)
        seen = set(starts)
        queue = deque((node_id, 0, []) for node_id in starts)
        paths = []
        selected_edges = {}
        while queue:
            node_id, depth, path = queue.popleft()
            if depth >= max_hops:
                continue
            for edge in adjacency.get(node_id, []):
                other = edge["target"] if edge["source"] == node_id else edge["source"]
                selected_edges[edge["id"]] = edge
                next_path = path + [edge["id"]]
                paths.append({"from": starts[0] if starts else node_id, "to": other, "edge_ids": next_path})
                if other not in seen:
                    seen.add(other)
                    queue.append((other, depth + 1, next_path))
        return {"query": query, "start_nodes": starts, "nodes": [nodes[node_id] for node_id in seen], "edges": list(selected_edges.values()), "paths": paths[:100]}

    def analyze_quality_assets(self, asset_ids: list[str], query: str, task_id: str | None = None) -> dict[str, Any]:
        if not asset_ids:
            raise ValueError("asset_ids must not be empty")
        trace = self.traces.start("quality_asset_analysis", {"asset_ids": asset_ids, "query": query, "task_id": task_id})
        try:
            parses = []
            for asset_id in asset_ids:
                parsed = self.documents.parse(asset_id)
                graph_update = self.ingest(asset_id)
                parses.append({"asset_id": asset_id, "summary": parsed["summary"], "status": parsed["status"],
                    "entity_count": len(graph_update["entities"])})
            self.traces.step(trace["id"], "unified_asset_understanding", parses)
            evidence = self.search_evidence(query, asset_ids, 20)
            if not evidence["results"]:
                evidence = self.search_evidence("", asset_ids, 20)
                evidence["query"] = query
                evidence["retrieval_mode"] = "asset_scope_fallback"
            else:
                evidence["retrieval_mode"] = "keyword"
            self.traces.step(trace["id"], "evidence_retrieval", {"count": len(evidence["results"]), "chunk_ids": [x["chunk_id"] for x in evidence["results"]]})
            graph = self.query(query, max_hops=3, asset_ids=asset_ids)
            self.traces.step(trace["id"], "graph_reasoning", {"node_count": len(graph["nodes"]), "path_count": len(graph["paths"])})
            inspections = [record for record in self.store.all("inspection_record")
                if record.get("asset_id") in asset_ids and (task_id is None or record.get("task_id") == task_id)]
            result = {"analysis_id": identifier("analysis"), "trace_id": trace["id"], "assets": parses,
                "inspections": inspections, "evidence": evidence, "graph": graph,
                "decision_basis": {
                    "model_results": len(inspections),
                    "evidence_chunks": len(evidence["results"]),
                    "graph_nodes": len(graph["nodes"]),
                    "graph_edges": len(graph["edges"]),
                    "unique_graph_edges": len({edge["id"] for edge in graph["edges"]}),
                    "graph_paths": len(graph["paths"]),
                }}
            self.store.put("quality_analysis", {"id": result["analysis_id"], **result, "created_at": now()})
            self.traces.finish(trace["id"], {"analysis_id": result["analysis_id"], **result["decision_basis"]})
            return result
        except Exception as exc:
            self.traces.finish(trace["id"], error=str(exc))
            raise

    def propose_ontology_update(self, asset_ids: list[str], rationale: str = "", changes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        trace = self.traces.start("propose_ontology_update", {"asset_ids": asset_ids})
        derived = []
        standards = []
        for asset_id in asset_ids:
            parsed = self.documents.parse(asset_id)
            self.ingest(asset_id)
            for chunk in parsed["chunks"]:
                context = {}
                context_fields = {"产品": "product", "工序": "process", "检验项": "inspection_item", "处理措施": "action"}
                for entity_type, name in self._entities(chunk["text"]):
                    if entity_type in context_fields and context_fields[entity_type] not in context:
                        context[context_fields[entity_type]] = name
                for standard in STANDARD_RE.findall(chunk["text"]):
                    standards.append({"concept": "标准条款", "standard_name": standard, **context,
                        "asset_id": asset_id, "locator": chunk["locator"], "basis": chunk["text"][:500]})
        if standards:
            derived.append({"operation": "add_standard_mappings", "items": self._unique(standards)})
        requested_changes = changes or []
        supported_operations = {"add_standard_mappings", "add_concept"}
        supported_requested = [change for change in requested_changes if change.get("operation") in supported_operations]
        normalization_warnings = [
            f"Unsupported requested change was replaced by evidence-derived mappings: {change.get('operation') or change.get('type') or 'unknown'}"
            for change in requested_changes if change.get("operation") not in supported_operations
        ]
        proposal = {
            "id": identifier("ontology_proposal"), "proposal_id": None, "status": "pending",
            "asset_ids": asset_ids, "rationale": rationale, "changes": derived + supported_requested,
            "requested_changes": requested_changes, "normalization_warnings": normalization_warnings,
            "standards_found": self._unique(standards), "trace_id": trace["id"], "created_at": now(),
            "review": None,
        }
        proposal["proposal_id"] = proposal["id"]
        self.store.put("ontology_proposal", proposal)
        self.traces.finish(trace["id"], {"proposal_id": proposal["id"]})
        return proposal

    def review_proposal(self, proposal_id: str, decision: str, operator: str, note: str = "") -> dict[str, Any]:
        if decision not in {"approve", "reject"}:
            raise ValueError("decision must be approve or reject")
        proposal = self.store.get("ontology_proposal", proposal_id)
        if proposal["status"] != "pending":
            raise ValueError("Proposal has already been reviewed")
        unsupported = [change for change in proposal.get("changes", [])
            if change.get("operation") not in {"add_standard_mappings", "add_concept"}]
        if decision == "approve" and unsupported:
            raise ValueError("Proposal contains unsupported changes and must be rejected or regenerated")
        proposal["status"] = "approved" if decision == "approve" else "rejected"
        proposal["review"] = {"decision": decision, "operator": operator, "note": note, "at": now()}
        return self.store.put("ontology_proposal", proposal)

    def publish(self, proposal_id: str, operator: str) -> dict[str, Any]:
        proposal = self.store.get("ontology_proposal", proposal_id)
        if proposal["status"] != "approved":
            raise ValueError("Only an approved proposal can be published")
        unsupported = [change for change in proposal.get("changes", [])
            if change.get("operation") not in {"add_standard_mappings", "add_concept"}]
        if unsupported:
            raise ValueError("Approved proposal contains unsupported changes and cannot be published")
        versions = self.store.all("ontology_version")
        parent = max(versions, key=lambda item: item["version"])
        mappings = list(parent.get("standard_mappings", []))
        concepts = list(parent["concepts"])
        for change in proposal.get("changes", []):
            if change.get("operation") == "add_standard_mappings":
                mappings.extend(change.get("items", []))
            if change.get("operation") == "add_concept" and change.get("name"):
                concepts.append(change["name"])
        version_number = parent["version"] + 1
        version = {
            "id": f"ontology_v{version_number}", "version": version_number, "status": "published",
            "concepts": list(dict.fromkeys(concepts)), "relations": parent["relations"],
            "standard_mappings": self._unique(mappings), "parent_version": parent["id"],
            "proposal_id": proposal_id, "operator": operator, "created_at": now(),
        }
        self.store.put("ontology_version", version)
        proposal["status"] = "published"
        proposal["published_version"] = version["id"]
        self.store.put("ontology_proposal", proposal)
        return version

    def _entities(self, text: str):
        found = []
        markers = list(FIELD_MARKER_RE.finditer(text))
        for index, marker in enumerate(markers):
            label = marker.group("label").lower()
            value_end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
            name = " ".join(text[marker.end():value_end].strip(" \t\r\n，,。；;").split())
            if not name or label.startswith("标准"):
                continue
            if label.startswith(("产品", "零件", "物料")):
                entity_type = "产品"
            elif label in {"批次", "批号", "lot"}:
                entity_type = "批次"
            elif label in {"工序", "制程"}:
                entity_type = "工序"
            elif label in {"检验项", "检查项", "检测项"}:
                entity_type = "检验项"
            elif label in {"处理措施", "处置", "纠正措施"}:
                entity_type = "处理措施"
            else:
                entity_type = "异常"
                if label == "人工结论" and name.strip().upper() in {"正常", "合格", "OK", "PASS"}:
                    continue
            found.append((entity_type, name))
        for standard in STANDARD_RE.findall(text):
            found.append(("标准条款", " ".join(standard.split())))
        return list(dict.fromkeys(found))

    def _put_node(self, node_type: str, canonical: str, summary: str, properties: dict[str, Any]):
        node_id = "node_" + hashlib.sha256(f"{node_type}|{canonical.lower()}".encode("utf-8")).hexdigest()[:20]
        node = {"id": node_id, "node_id": node_id, "type": node_type, "name": canonical, "summary": summary, "properties": properties, "updated_at": now()}
        self.store.put("graph_node", node)
        return node

    @staticmethod
    def _unique(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        seen = set()
        for item in items:
            key = str(item.get("id") or sorted(item.items()))
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result
