---
name: asset-recognition
description: 统一登记和理解制造业图片、PDF、Word、文本、CSV 与 Excel 资产，并保留证据位置。
---

# 资产认知

## 适用条件

用户要求登记、理解、汇总或关联质量资产时使用。文件先由业务页面上传，后续工具只传 `asset_id`。

## 执行步骤

1. 使用 `get_asset` 核对资产类型、哈希和文件名。
2. 使用 `parse_asset` 解析资产；不要虚构未抽取到的内容。
3. 检查返回的 `notices`。图片需要质量判断时调用 `inspect_images`；扫描件提示需要 OCR 时明确说明。
4. 使用 `search_evidence` 查找用户关注的产品、批次、工序、检验项、异常或标准。
5. 输出资产摘要、实体线索和证据位置。PDF 标页码，表格标工作表/行，Word 标段落。

## 输出要求

返回 `asset_id`、摘要、证据列表、解析版本和不确定项。每项事实都关联返回的 locator。
