---
name: ontology-update
description: 从新增质量规范提出本体和标准映射变更，经过人工审核后发布可追踪版本。
---

# 本体持续更新

## 适用条件

新增规范、工艺文件、异常类型或标准版本需要进入质量知识体系时使用。

## 执行步骤

1. 对新增资产逐一调用 `parse_asset`，核对证据位置与解析提示。
2. 调用 `propose_ontology_update`，填写资产 ID 与变更依据，默认不传自定义 `changes`，由服务端依据文档生成 `add_standard_mappings`。该步骤只产生候选，不等于发布。
3. 向审核者展示变更项、标准编号、来源资产和位置。
4. 审核时确认 `changes` 中的 `operation` 只能是 `add_standard_mappings` 或 `add_concept`。出现 `type=add_standard`、`type=add_action` 或其他不支持结构时必须拒绝并重新生成。
5. 得到明确人工决定后调用 `review_ontology_update`。
6. 仅对已批准候选调用 `publish_ontology_version`。
7. 使用 `get_trace` 返回提议过程和发布版本。

## 失败处理

无证据、标准编号不完整或类型冲突时保留为待审核，不自动发布。审核拒绝后保留历史记录。
