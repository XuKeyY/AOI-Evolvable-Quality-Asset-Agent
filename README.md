# AOI 累计反馈、双模式测试与模型进化

## Competition acceptance baseline (2026-10-28)

The current published AOI experiment version is V1 (`model_d0c21d0f6f4b499493f9f3c84b793170`), evolved from V0 with two human-reviewed samples. The frozen validation job passed with FP=0, FN=6 and balanced accuracy=0.90. The reviewed ontology is `ontology_v4`; OCR, cross-document retrieval, graph reasoning, MCP and Skills all have persisted acceptance evidence.

Run `scripts/competition_acceptance.py` for the read-only 12-check acceptance report. See `docs/FINAL_ACCEPTANCE.md`, `docs/ARCHITECTURE.md` and `docs/DEMO_SCRIPT.md` for the evidence matrix and demo procedure. Nexent Agent release labels, AOI V0/V1 labels and ontology version IDs are separate version namespaces.

业务工程位于 D:\competition\aoi-agent。AOI 核心使用 E:\competition\AOI_100plus30_kit\AOI_100plus30_kit，核心源码保持不变，
初始适配调用原 code/adapt_support.py；进化调用本工程 algorithm/adapt_support_growth.py，
后者只扩展入口数量、协议元数据并引用原 official_core/feature_backend/model_io 和 V25 核心。
图像推理继续调用原 hidden_test_eval.py，异常分数、阈值、>= 判定规则保持原意。

## 页面和服务

业务页面 http://localhost:8100，交互文档 http://localhost:8100/docs。
MCP http://host.docker.internal:8101/mcp，用于 Nexent Docker；
本机 MCP 地址 http://localhost:8101/mcp。不占用 Nexent 3000 端口。
业务服务只运行一个 worker，不使用 --reload；数据库和任务队列仍为单机开发实现。

```powershell
cd D:\competition\aoi-agent
.\.venv\Scripts\python.exe -m uvicorn api.app:app --host 0.0.0.0 --port 8100 --workers 1
# 另一个窗口：
.\.venv\Scripts\python.exe -m mcp_server.server
```

本次升级会重启已有的本工程 8100/8101 服务；若网址已可访问，不要重复启动。
当前监听进程见 data/service_pids.json。完整业务依赖已安装在独立环境，
算法依赖保留在原项目环境，config.json 指向它。

## 数据与初始模型

每个任务持久保存原始正常/缺陷支持集、人工反馈历史和独立标注验证集。
初始支持集恰好 100 正常＋30 缺陷，提交初始适配后固定图片集合；
原始标签仍可通过人工修订接口纠正，修订另存，不覆盖原标签和历史。

独立验证使用 validation_normal、validation_defect 两个角色。
默认每类至少 5 张，登记时排除支持集及已确认反馈图片；
首次提交验证后固定，后续候选和父模型都在同一套图片和标签上检测。
验证图片可以产生复核记录，但不作为学习反馈。

首版发布前允许重新提交初始候选，使用原始图片的最新确认标签；仍须满足 100＋30。
首版发布后禁止再次初始适配，改用累计进化。

初始流程：登记支持/验证集 → 初始适配 → validate_candidate →
查询比较结果 → 人工发布。适配成功只注册候选，不自动用于现场检测。
旧版本遗留候选也不会在升级时静默发布，必须独立验证。
开发模式不得发布；根据真实 V25 约束，开发初始至少 12 正常＋3 缺陷，
2 折、1 次 OOF、1 轮 Adapter，仅作运行检查，不保证任意图片分布都成功。

## 两种检测模式

- review_test：使用当前发布模型，保存原机器结果并入云端同步队列，
  提供人工复核（normal / defect / uncertain），人工结论另存并追加同步。
- machine_test：使用当前发布模型，同样保存图片和机器结果并入同步队列，
  不提供复核页面；服务端拒绝其复核请求，不产生学习标签。

检测提交时固定模型版本和文件哈希；之后即便发布新版，已提交任务仍用原版本。
record_id 定位单张检测记录，inspection_id 定位批量作业。
原机器 result/final_score/threshold 始终保留；人工结论不修改机器预测。
operator 和 note 用于审计；当前没有账号身份认证。

## 累计反馈适配

第 N 次进化使用原始支持集＋截止本轮的累计有效反馈，不只使用最新一批反馈。
每轮固定 parent_model、feedback_cutoff、dataset_version 和文件快照。
正式进化配置固定为 3 次／3 折 OOF、10 轮 Adapter，
不使用业务开发模式或 --allow-nonstandard-counts。
正常至少 9、缺陷至少 3；更多样本和多样性仍需真实运行检验。

训练快照规则：

- 根据解码后的 RGB 像素哈希去重，不用文件名判断重复。
- 同一个反馈资产使用最新人工结论，uncertain 不进入新增反馈。
- 重复资产间最新有效标签不同，则排除该图片，列出 conflicting_labels。
- 原始图片使用最新已确认标签；后续冲突重复需再次确认原始标签才能消解。
- 排除固定验证集图片及任何未经人工确认的机器预测。
- 无有效训练内容变化时不创建进化任务。
- 所有原标签修订和复核历史保留。learning_eligible 是复核时初步资格，
  最终是否进入本轮训练以快照去重、冲突、截止点和验证排除规则为准。

更多图片会用于原训练函数；全局／Adapter 特征库仍保持原导出容量
6144×64 和 8192×64，不将“数据增长”误写成改变部署模型结构。
CPU 适配较慢，样本增长的实际耗时与内存需真实数据评估。

## 独立验证、发布与回滚

首次候选按绝对门槛验证；后续候选同时与其父发布模型比较。
默认门槛在 config.json 的 regression_gate：

- 每类至少 5 张独立验证图片。
- 平衡准确率不低于 80%，且误报率、漏检率各不超过 20%。
- 误报率、漏检率均不得高于父模型。
- 平衡准确率下降不得超过 0.002。

原参考 V22 的 _v22_fit_replay_alpha 使用 BA/recall/FPR 回放保护，
但其硬正常原型增量机制不适合直接替换当前 V25 全量适配；
本工程复用 V25 OOF，并在发布层增加独立回归保护，默认 FPR/FNR 条件更严格。
默认门槛是可调整的开发策略，需按实际业务确认；修改后要重新验证候选。

比较作业 succeeded 只表示比较完成，comparison.passed 才决定是否允许发布。
未通过保留旧版；发布需要候选父版本和 expected_current 匹配当前版本。
数据库事务同时保存版本状态、任务发布指针和发布审计，避免部分切换。
已验证候选文件或门槛改变时拒绝发布。
回滚仅允许本任务历史已发布版本，保留操作历史，不将未验证候选当回滚目标。

## 云端同步

当前没有提供真实云端地址，因此默认 disabled；事件保存在 SQLite outbox，
状态 pending_configuration，不能算上传成功。
两种检测模式共用 inspection 事件；人工结论生成 human_review 事件，
原支持标签修订生成 support_label_revision 事件。
人工结论等待原检测事件同步完成；远端应按 sequence 判断最新修订，而不是到达顺序。

启用真实云端：在 config.json 的 cloud 中设置 enabled=true、
endpoint 为接收接口完整 URL；按 token_env 指定环境变量填 Bearer token，
然后重启 API。密钥不要写入 config.json。
本机 API 调用不使用环境代理；云端默认 trust_env=false，确需环境代理时显式启用。
重启可以恢复未确认交付；重试采用同一 event_id 和 Idempotency-Key，
指数退避到 300 秒，默认最多 8 次，耗尽后可人工重试。
只有远端确认匹配 event_id 且 status=accepted/duplicate 才标为 synced。

云端协议为 POST multipart/form-data：
event 为 JSON 字符串（event_id、kind、payload、depends_on）；
files 为原图片列表。检测 payload 包含原始结果、mode、模型/数据版本和图片哈希。
远端返回：
```json
{"event_id":"sync_...","status":"accepted"}
```
重复事件返回 duplicate；同一 ID 不应覆盖不同内容。
已有云端协议不同的话，需要对接适配器，不应宣称任意接口都兼容。

本地接收端供联调，并非真实云端：
```powershell
.\.venv\Scripts\python.exe -m uvicorn api.cloud_receiver:app --host 127.0.0.1 --port 8102
```
测试时 endpoint=http://127.0.0.1:8102/events。
该接收端保存事件、文件哈希并检查幂等，不用于生产鉴权。

## API 和 MCP

主要新增接口：

- POST /tasks/{task_id}/evolutions：累计正式适配。
- GET /tasks/{task_id}/training-preview：累计数据、截止点、排除原因。
- GET /tasks/{task_id}/feedback：历史反馈与当前有效新增样本。
- POST/GET /tasks/{task_id}/label-revisions：原始标签修订/历史。
- POST /inspections：asset_ids、task_id、mode，可传当前 model_version。
- GET /inspection-records?task_id=...：逐图原始记录。
- POST/GET /inspection-records/{record_id}/reviews：人工结论/历史。
- POST /models/{model_version}/validations：固定验证集比较。
- POST /tasks/{task_id}/publications：model_version、operator、expected_current。
- POST /tasks/{task_id}/rollbacks：历史版本、operator、expected_current。
- GET /tasks/{task_id}/releases：发布/回滚历史。
- GET /sync-events；POST /sync-events/{event_id}/retry：云端状态/重试。

初始上传、任务、作业、模型及 CSV 接口继续保留。
MCP 暴露模式检测、复核、修订、进化、验证比较、发布、回滚及同步工具。
原 MCP 的 inspect_images 参数已变更为 task_id 和 mode；
Nexent 应刷新工具列表与参数，再上传新的 skills/aoi-task-adaptation.zip。
Skill 指导业务流程；数量、模式权限、快照排除和发布门槛由代码执行。

图片和扫描 PDF 使用 RapidOCR。每个 OCR 证据片段保存页码、四点多边形、边界框、置信度和渲染尺寸，可直接用于跨文档检索与证据定位。解析版本为 `unified-v2-ocr`。

## 验证与边界

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts\check_mcp.py
.\.venv\Scripts\python.exe scripts\validate_real.py --normal-dir "正常目录" --defect-dir "缺陷目录" --test-dir "独立测试目录"
```

自动测试覆盖双模式、不可覆写机器预测、累计反馈、去重冲突、验证排除、
截止点、正式配置、回归失败、发布、过期父版本、回滚、版本固定及云端幂等重试。
测试模型是 fixture，不证明真实 AOI 准确率或增长数据训练已验收。
增长入口测试使用真实算法环境，只检查 131 张数据能通过数量门槛，
并在特征提取前中止，不能算完整进化训练。

validate_real.py 是候选诊断：调用真实适配和原推理脚本，比对服务与直接脚本结果，
不上传云端、不产生人工反馈、不自动发布。
原源码哈希基线位于 data/source_baseline.json；
升级备份（包括升级前数据库）位于 data/backups。
目前仍需真实样本、真实云端接口以及 Nexent 平台实际工作流验收。
