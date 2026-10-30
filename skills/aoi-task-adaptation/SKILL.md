---
name: aoi-task-adaptation
description: 建立 AOI 初始质检模型，选择人工复核或上机模式，并通过累计有效反馈、独立验证和版本发布执行模型进化。
---

# AOI 累计反馈质检

图片先通过业务页面登记为 asset_id；Nexent 附件不会自动成为算法可读资产。
操作依赖分配的 AOI MCP 工具。原异常分数不是缺陷概率，不应重新归一化。

## 初始模型

明确产品、工序与检测部位。创建任务并分别登记 100 张正常、30 张缺陷。
另登记独立标注验证集，使用 validation_normal、validation_defect 两个角色；
默认每类不少于 5 张，不得复用训练或反馈图片。
submit_adaptation 只创建候选，随后 get_job 查询结果。
用 validate_candidate 对固定独立验证集验收；get_job 的 comparison.passed=true 后，
用户要求发布时调用 publish_model，传实际操作者及 expected_current。
开发模式不能用于正式发布或进化。

## 检测模式

让用户明确选择 review_test（人工复核测试）或 machine_test（上机测试），
然后用 inspect_images 指定 task_id、mode；可显式传当前发布模型版本。
作业提交后版本固定，即使后来发布了新版，该检测仍使用原指定版本。
两种模式保存原机器结果、图片和版本，并进入同一云端同步队列。
未配置云端或同步失败时报告实际状态，不宣称已上传。

review_test 的检测完成后，人工确认 normal、defect 或 uncertain，
调用 review_inspection 记录 operator、note。模型推测不能冒充人工复核；
只有用户给出的确认结论才能写入。机器结果保留不改，人工结论另存并同步。
machine_test 不提供复核或学习反馈，不能把机器预测当正确标签。

## 累计进化

用 preview_evolution_dataset 检查累计训练集与排除原因。
原始支持集加截止本轮的累计有效反馈；不复制图片凑数，冲突和验证图片排除。
原始标签纠正使用 correct_initial_label，必须来自明确人工确认并保留历史。
submit_evolution 固定父模型、反馈截止点和快照；正式配置为 3 次/3 折 OOF、10 轮 Adapter。
submit_evolution 和 validate_candidate 是长作业，只负责创建作业并立即返回持久化
`job_id`。等待合理时间后使用 get_job 查询该 ID；不得因 MCP 会话超时重复提交。
适配成功后 validate_candidate 比较候选和父模型；未通过则保留旧发布版。
发布时传 expected_current，过期父模型必须重新基于当前版本适配。
rollback_model 只允许历史已发布版本，由用户明确要求后执行，保留审计记录。

## 错误与输出

queued/running 不代表模型已成功。失败报告 error 和日志关键原因，
修正后创建新作业，不无限自动重试。
同步错误可用 get_cloud_sync_events 查询、retry_cloud_sync 重试；人工结论等待原检测同步完成。
返回任务、作业、模式、模型版本、数据版本、反馈截止点、验证对比及发布状态，
逐张保留 asset_id、record_id、result、final_score、threshold、evidence_asset_ids。
没有生成证据图时不编造热力图或缺陷位置。
