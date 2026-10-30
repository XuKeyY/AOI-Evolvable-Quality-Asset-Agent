import io
import tempfile
import unittest
from pathlib import Path
from PIL import Image

from services.asset_service import AssetService
from services.document_service import DocumentService
from services.graph_service import GraphService
from services.store import Store
from services.trace_service import TraceService


class KnowledgeFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "data")
        self.assets = AssetService(self.store)
        self.traces = TraceService(self.store)
        self.documents = DocumentService(self.assets, self.store, self.traces)
        self.graph = GraphService(self.store, self.documents, self.traces)

    def tearDown(self):
        self.temp.cleanup()

    def upload(self, name, content):
        return self.assets.register(io.BytesIO(content), name)

    def test_cross_document_evidence_graph_and_trace(self):
        spec = self.upload("检验规范.txt", "产品：精密螺钉\n\n批次：LOT-2026-A\n检验项：螺钉头划痕\n标准：GB/T 5779.1-2000\n处理措施：隔离并人工复核".encode("utf-8"))
        record = self.upload("检验记录.csv", "产品,批次,工序,异常\n精密螺钉,LOT-2026-A,终检,螺钉头划痕\n".encode("utf-8-sig"))

        parsed = self.documents.parse(spec["id"])
        self.assertEqual(parsed["status"], "parsed")
        self.assertTrue(parsed["trace_id"].startswith("trace_"))
        self.graph.ingest(spec["id"])
        self.graph.ingest(record["id"])

        evidence = self.graph.search_evidence("LOT-2026-A", limit=10)
        self.assertEqual({item["asset_id"] for item in evidence["results"]}, {spec["id"], record["id"]})
        batch_nodes = [node for node in self.store.all("graph_node") if node["type"] == "批次"]
        self.assertEqual(len(batch_nodes), 1)
        traversal = self.graph.query(start_node_id=batch_nodes[0]["id"], max_hops=2)
        document_assets = {node.get("properties", {}).get("asset_id") for node in traversal["nodes"] if node["type"] == "文档"}
        self.assertEqual(document_assets, {spec["id"], record["id"]})
        unrelated = self.upload("unrelated.txt", "产品：轴承\n批次：OTHER-LOT\n异常：锈蚀".encode("utf-8"))
        self.graph.ingest(unrelated["id"])
        analysis = self.graph.analyze_quality_assets([spec["id"], record["id"]], "LOT-2026-A")
        self.assertEqual(analysis["decision_basis"]["evidence_chunks"], 2)
        scoped_assets = {edge["evidence"]["asset_id"] for edge in analysis["graph"]["edges"]}
        self.assertEqual(scoped_assets, {spec["id"], record["id"]})
        self.assertEqual(analysis["decision_basis"]["graph_nodes"], len(analysis["graph"]["nodes"]))
        self.assertEqual(analysis["decision_basis"]["graph_edges"], len(analysis["graph"]["edges"]))
        self.assertEqual(analysis["decision_basis"]["unique_graph_edges"], len({edge["id"] for edge in analysis["graph"]["edges"]}))
        self.assertEqual(self.traces.get(analysis["trace_id"])["status"], "succeeded")

        fallback = self.graph.analyze_quality_assets([spec["id"], record["id"]], "分析这两个资产的质量关联与图谱信息")
        self.assertEqual(fallback["evidence"]["retrieval_mode"], "asset_scope_fallback")
        self.assertEqual(fallback["decision_basis"]["evidence_chunks"], 3)
        self.assertEqual({item["asset_id"] for item in fallback["evidence"]["results"]}, {spec["id"], record["id"]})

    def test_ontology_requires_review_before_publish(self):
        spec = self.upload("新规范.txt", "产品：精密螺钉\n依据 ISO 9001:2015 执行质量复核。".encode("utf-8"))
        proposal = self.graph.propose_ontology_update([spec["id"]], "新增规范对齐")
        self.assertEqual(proposal["status"], "pending")
        with self.assertRaises(ValueError):
            self.graph.publish(proposal["id"], "reviewer")
        reviewed = self.graph.review_proposal(proposal["id"], "approve", "reviewer", "证据有效")
        self.assertEqual(reviewed["status"], "approved")
        version = self.graph.publish(proposal["id"], "reviewer")
        self.assertEqual(version["version"], 2)
        self.assertEqual(version["parent_version"], "ontology_v1")
        self.assertTrue(version["standard_mappings"])

    def test_ontology_normalizes_agent_requested_change_schema(self):
        spec = self.upload("抽样规范.txt", (
            "产品：精密螺钉 工序：抽样终检 检验项：批次抽样验收 "
            "标准：ISO 2859-1:1999 处理措施：不合格批次转入加严检验"
        ).encode("utf-8"))
        proposal = self.graph.propose_ontology_update([spec["id"]], "新增规范", [
            {"type": "add_standard", "standard_name": "ISO 2859-1:1999"},
            {"type": "add_action", "action": "不合格批次转入加严检验"},
        ])
        self.assertEqual([change["operation"] for change in proposal["changes"]], ["add_standard_mappings"])
        self.assertEqual(len(proposal["normalization_warnings"]), 2)
        mapping = proposal["changes"][0]["items"][0]
        self.assertEqual(mapping["product"], "精密螺钉")
        self.assertEqual(mapping["process"], "抽样终检")
        self.assertEqual(mapping["inspection_item"], "批次抽样验收")
        self.assertEqual(mapping["action"], "不合格批次转入加严检验")

    def test_entity_fields_stop_at_next_label_and_reingest_replaces_edges(self):
        text = (
            "产品：精密螺钉 批次：LOT-2026-A 工序：终检 "
            "检验项：螺钉头划痕 标准：GB/T 5779.1-2000 "
            "处理措施：隔离异常批次，并由质量工程师人工复核"
        )
        entities = self.graph._entities(text)
        self.assertIn(("产品", "精密螺钉"), entities)
        self.assertIn(("批次", "LOT-2026-A"), entities)
        self.assertIn(("工序", "终检"), entities)
        self.assertIn(("检验项", "螺钉头划痕"), entities)
        self.assertIn(("标准条款", "GB/T 5779.1-2000"), entities)
        self.assertIn(("处理措施", "隔离异常批次，并由质量工程师人工复核"), entities)
        self.assertNotIn(("异常", "批次"), entities)
        self.assertFalse(any("标准：" in name or "检验项：" in name for _, name in entities))

        asset = self.upload("replace.txt", text.encode("utf-8"))
        self.graph.ingest(asset["id"])
        old_edges = [edge for edge in self.store.all("graph_edge") if edge["evidence"]["asset_id"] == asset["id"]]
        self.assertTrue(old_edges)
        self.store.put("graph_edge", {
            "id": "edge_stale", "source": "old", "relation": "涉及", "target": "bad",
            "evidence": {"asset_id": asset["id"]}, "created_at": "old",
        })
        self.graph.ingest(asset["id"])
        current_ids = {edge["id"] for edge in self.store.all("graph_edge") if edge.get("evidence", {}).get("asset_id") == asset["id"]}
        self.assertNotIn("edge_stale", current_ids)
        self.assertEqual(current_ids, {edge["id"] for edge in old_edges})

    def test_human_conclusion_creates_only_confirmed_anomaly(self):
        defect = self.graph._entities("产品=精密螺钉；批次=LOT-1；人工结论=缺陷")
        normal = self.graph._entities("产品=精密螺钉；批次=LOT-2；人工结论=正常")
        self.assertIn(("异常", "缺陷"), defect)
        self.assertFalse(any(entity_type == "异常" for entity_type, _ in normal))

    def test_image_ocr_keeps_page_box_and_confidence(self):
        class FakeOCR:
            def recognize(self, image):
                return [{
                    "text": "LOT-2026-A",
                    "confidence": 0.987654,
                    "polygon": [[10, 20], [110, 20], [110, 42], [10, 42]],
                    "bbox": {"x_min": 10, "y_min": 20, "x_max": 110, "y_max": 42},
                }]

        buffer = io.BytesIO()
        Image.new("RGB", (200, 100), "white").save(buffer, format="PNG")
        asset = self.upload("label.png", buffer.getvalue())
        documents = DocumentService(self.assets, self.store, self.traces, FakeOCR())
        parsed = documents.parse(asset["id"], force=True)
        locator = parsed["chunks"][0]["locator"]
        self.assertEqual(parsed["chunks"][0]["text"], "LOT-2026-A")
        self.assertEqual(locator["page"], 1)
        self.assertEqual(locator["bbox"]["x_min"], 10)
        self.assertEqual(locator["confidence"], 0.987654)
        self.assertEqual(locator["image_width"], 200)


if __name__ == "__main__":
    unittest.main()
