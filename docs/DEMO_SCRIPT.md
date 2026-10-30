# 7 分钟比赛演示脚本

演示前打开 Nexent `http://localhost:3000`、业务页 `http://localhost:8100` 和最终验收文档。先运行 `scripts/competition_acceptance.py`，确认全部 PASS。

## 0:00–0:40 场景与架构

制造企业的图片、检验表、质量规范和扫描标签彼此割裂。本系统将它们登记为资产，通过 AOI、证据检索和质量图谱形成可追溯决策，并让模型与本体在人工审核后受控进化。

展示架构图和 Nexent 智能体：Nexent 负责 Agent 推理与 Skills 编排，MCP 调用独立业务服务，AOI 核心仍由原算法脚本执行。

## 0:40–1:40 多模态资产认知与 OCR

在 Nexent 输入：

> 查询资产 `asset_bc353b599f7c42d69cac7c92dffcf262`，解析它并列出 OCR 文本、页码、bbox、polygon、confidence 和 trace_id。不要执行 AOI 或更新本体。

应展示 7 个证据片段、`unified-v2-ocr`、页码 1、置信度和 `trace_8c7e82638f2b47c5810e16c6dc332988`。说明识别文本之外还保留坐标，结论可回到原图定位。

## 1:40–2:50 跨文档检索与图谱推理

在 Nexent 输入：

> 分析资产 `asset_6bc290c739644a5e8593a0c3cb39eb46` 与 `asset_5ae0ef040b8f425c87ede6f1c7a70ec4` 的质量关联。原样报告 decision_basis、主要证据位置、主要图谱路径和 trace_id，不执行 AOI。

应展示质量规范 paragraph 1、检验记录 row 2/3；证据 3、节点 10、唯一边 15、路径 30；LOT-2026-A 缺陷及 GB/T 5779.1-2000、隔离复核措施。

## 2:50–3:50 本体动态更新

展示审计链：从 `asset_ad7175343c464e34923c69df095b68b0` 派生 ISO 2859-1:1999 候选；错误结构候选被人工拒绝；服务端按 `add_standard_mappings` 重新生成 `ontology_proposal_dc9a7666971d42d98dbf86202264cd93`；人工批准后发布 `ontology_v4`，父版本为 `ontology_v3`。

说明抽取、审核、发布分开，智能体不能越过人工审批直接改变本体。

## 3:50–5:10 AOI 检测与人工反馈

展示 V0 的 review_test 作业和两条复核：缺陷图确认为 defect，正常图确认为 normal，均为 `learning_eligible=true`。再展示累计预览：初始 130＋有效反馈 2，共 101 正常、31 缺陷，数据集 `dataset_25aed13c9acaa14d`。

说明机器预测不进入训练；只有人工复核模式产生候选反馈，上机模式只留检测记录。

## 5:10–6:20 受控模型进化与发布

展示：

- 进化作业 `job_2f4c52c012de42058eb4fd6573de556b` 成功，父模型 V0、候选 V1。
- 冻结验证 `job_5d60a075e2844d19b74ed4621e8b6696`：`comparison.passed=true`，FP=0、FN=6、BA=90%。
- 发布历史恰好两条：V0 初始发布、V0→V1 进化发布。
- V1 上机作业 `job_8e06280ca8874ca3a1991be01effe48f` 同时得到 NG 和 OK。

说明训练成功不等于发布；固定验证集、父模型保护和人工发布共同控制上线风险。

## 6:20–7:00 Nexent、MCP 与 Skills 价值

展示 Nexent 工具列表和 Skill 仓库，说明资产认知、质量分析、本体更新、AOI 任务适配四类流程。最后展示验收脚本全 PASS 和关键 trace。

结尾话术：

> 智适检激活了图片、表格、规范和扫描件中的存量质量数据。Nexent 用 Skills 固化业务方法，用 MCP 调用真实 AOI、证据和图谱能力；每个判断都能追到模型、数据、文档位置和执行 Trace，模型与本体都必须通过审核后才能进化。

## 截图清单

1. Nexent 智能体、MCP 工具和已选 Skills。
2. OCR 7 个片段及 bbox/confidence/trace。
3. 跨文档证据表、图谱统计和主要路径。
4. ontology_v3→v4 的候选、批准和发布。
5. 100＋30 初始集、两条反馈和 101＋31 快照。
6. V1 冻结验证 `passed=true`。
7. V0/V1 两条发布历史。
8. V1 的 NG/OK 上机结果。
9. `competition_acceptance.py` 全 PASS。
