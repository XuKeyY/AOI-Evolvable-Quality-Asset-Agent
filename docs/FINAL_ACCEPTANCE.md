# 赛题 3 最终验收证据

本文件记录“智适检：可进化质量资产智能体”的可重复验收基线。证据来自业务数据库和服务返回值；截图用于演示，不作为唯一依据。

## 赛题要求与实现证据

| 赛题要求 | 已实现能力 | 可核验记录 |
|---|---|---|
| 多模态异构资产统一认知 | 图片、扫描图片/PDF OCR、PDF、Word、文本、CSV、Excel 统一登记为 `asset_id`，保存哈希和证据位置 | OCR 资产 `asset_bc353b599f7c42d69cac7c92dffcf262`，解析 `parse_asset_bc353b599f7c42d69cac7c92dffcf262` |
| 低资源领域能力 | 100 张正常＋30 张缺陷建立 V0，人工有效反馈累计后以 101＋31 重新适配 V1 | 进化作业 `job_2f4c52c012de42058eb4fd6573de556b`，数据集 `dataset_25aed13c9acaa14d` |
| 本体半自动构建和动态更新 | 从新规范提取标准映射，候选经人工审核后发布；无效结构可拒绝且保留历史 | `ontology_proposal_dc9a7666971d42d98dbf86202264cd93` → `ontology_v4` |
| 行业标准对齐 | ISO 2859-1:1999 映射到产品、工序、检验项、处理措施和原文位置 | `ontology_v4.standard_mappings` |
| MCP 与 Skills 深度集成 | Nexent 已分配 AOI、资产、证据、图谱、本体、追踪和版本工具；四个 Skill 模板固化业务流程 | MCP `http://host.docker.internal:8101/mcp`；`skills/` |
| 检索与推理 | 规范文本和检验表按共享产品、批次、工序、检验项形成跨文档路径 | 分析 `analysis_45875ff5844b45c799a1c74b90250b1f`：3 段证据、10 节点、15 条唯一边、30 条路径 |
| 决策可溯源 | 检测、解析、检索、本体更新均保存模型/数据/解析版本、证据 locator 和 `trace_id` | OCR trace `trace_8c7e82638f2b47c5810e16c6dc332988`；图谱 trace `trace_05edd32776f44608b87fc1a0a35780eb` |
| 可进化与回归保护 | 反馈只来自人工复核；候选须在冻结验证集通过绝对与相对门槛才可人工发布 | 验证作业 `job_5d60a075e2844d19b74ed4621e8b6696`，`comparison.passed=true` |

## AOI 模型版本

V0/V1 是演示实验名称，`model_...` 是系统真实模型 ID。

| 实验版本 | 系统模型 ID | 数据 | 验证与发布 |
|---|---|---|---|
| V0 | `model_ac98cc13e4f64a70aee486593ce8416d` | 初始 100 正常＋30 缺陷 | 发布 `release_bf6d5060d1ba4b098bddac1dfefb59c2` |
| V1 | `model_d0c21d0f6f4b499493f9f3c84b793170` | 初始 130＋2 条有效反馈，共 101 正常＋31 缺陷 | 验证 `job_5d60a075e2844d19b74ed4621e8b6696`；发布 `release_c1bd8d4073f945aebbb844e5a6f3ded0` |

V1 支持集 OOF：平衡准确率 94.30%，召回率 93.55%，FPR 4.95%。冻结验证集为正常 20、缺陷 30；V0 与 V1 均为 FP=0、FN=6、FPR=0、FNR=20%、平衡准确率 90%。V1 没有回退，满足预先固定的门槛，因此允许发布。两条反馈规模很小，本轮证明的是受控进化闭环，不宣称统计上显著提升。

V1 发布后的上机测试作业为 `job_8e06280ca8874ca3a1991be01effe48f`，同一模型分别输出一张 NG 和一张 OK；`machine_test` 记录不会自动进入学习集。

## OCR 与证据定位

`quality_label_iso2859.png` 被 `unified-v2-ocr` 解析为 7 个片段。每个片段均保存 `page=1`、四点 `polygon`、`bbox`、置信度和图像尺寸，置信度范围 0.977049～0.999002。

## 自动验收

```powershell
cd D:\competition\aoi-agent
.\.venv\Scripts\python.exe scripts\competition_acceptance.py
```

脚本只读核验 SQLite 业务记录，同时检查 8100 健康接口和 8101 端口；结果写入 `data/competition_acceptance.json`。关键记录缺失或矛盾时返回非零退出码。

## 已知边界

- 云端同步已实现持久化 outbox、幂等键和失败重试，但当前未配置真实云端接收地址，不能宣称云端上传成功。
- 当前操作者字段用于审计，开发演示版尚未接入账号鉴权。
- MVTec AD 缺陷图参与少样本适配，属于自定义低资源实验协议，不是官方无监督基准结果。
- 两条反馈未改变冻结验证指标；演示重点是“人工反馈→快照→候选→回归门禁→人工发布”的完整控制链路。
