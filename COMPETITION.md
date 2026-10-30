# 智适检：基于 Nexent 的可进化质量资产认知智能体

本项目面向先进制造质检，将图片、扫描件、PDF、Word、文本、CSV 和 Excel 统一登记为带哈希的资产。AOI 完成少样本异常检测，文档和表格被解析为带页码、坐标、工作表、行号或段落号的证据；质量图谱连接产品、批次、工序、检验项、异常、标准条款与处理措施，供 Nexent 通过 MCP 检索和多跳推理。

## 与赛题 3 的对应关系

| 赛题能力 | 实现 | 验收 |
|---|---|---|
| 多模态异构资产认知 | `asset_service.py`、`document_service.py`、OCR | 上传和解析不同格式，返回统一 `asset_id` 和证据 locator |
| 低资源检测与持续学习 | 100＋30 初始适配、人工复核、累计反馈进化 | V0→反馈快照→V1→冻结验证→人工发布 |
| 本体半自动构建和标准对齐 | `graph_service.py`、`ontology/base_ontology.json` | 候选→人工审核→发布 `ontology_v4` |
| MCP 与 Skills | `mcp_server/server.py`、`skills/` | Nexent 工具调用和技能编排 |
| 检索与推理 | `search_evidence`、`query_quality_graph`、`analyze_quality_assets` | 跨文本和表格的证据与多跳路径 |
| 决策追溯 | `trace_service.py`、模型/数据/解析/本体版本 | `get_trace` 和原文位置 |

## 运行

复制 `config.example.json` 为 `config.json` 并填写本机路径。业务 API 只使用一个 worker。

```powershell
cd D:\competition\aoi-agent
.\.venv\Scripts\python.exe -m uvicorn api.app:app --host 0.0.0.0 --port 8100 --workers 1
# 另一个终端
.\.venv\Scripts\python.exe -m mcp_server.server
```

Nexent Docker 的 MCP 地址为 `http://host.docker.internal:8101/mcp`。将 `skills/dist/` 下的技能包导入 Nexent，再把 MCP 工具和 Skills 分配给智能体。

## 当前可演示验收状态

- AOI V0：`model_ac98cc13e4f64a70aee486593ce8416d`，初始 100 正常＋30 缺陷。
- AOI V1：`model_d0c21d0f6f4b499493f9f3c84b793170`，初始 130＋2 条人工有效反馈，共 101 正常＋31 缺陷。
- 冻结验证集：正常 20、缺陷 30；V1 为 FP=0、FN=6、FPR=0、FNR=20%、平衡准确率 90%，与父模型一致并通过既定门槛。
- 发布历史：V0 初始发布、V0→V1 进化发布，共两条审计记录。
- 本体已从 `ontology_v3` 经人工审核发布为 `ontology_v4`，新增 ISO 2859-1:1999 映射。
- 跨文档案例返回 3 个证据片段、10 个节点、15 条唯一边和 30 条路径。
- OCR 案例返回 7 个片段，保留页码、polygon、bbox、置信度和 trace。

执行最终自动验收：

```powershell
.\.venv\Scripts\python.exe scripts\competition_acceptance.py
```

详细证据、架构与演示顺序见 `docs/FINAL_ACCEPTANCE.md`、`docs/ARCHITECTURE.md`、`docs/DEMO_SCRIPT.md`。

## 诚实边界

云同步代码已经实现 outbox、幂等和重试，但没有配置真实云端地址；当前状态只能证明事件已入同步队列。AOI 核心作为外部算法依赖保留在原目录，本工程不复制权重或比赛数据。公开仓库前应排除本地数据、模型权重、运行日志、`config.json` 和密钥。
