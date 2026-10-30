# 系统架构与版本边界

```mermaid
flowchart LR
    U[用户 / 质检员] --> N[Nexent 智能体]
    N --> S[Skills 流程模板]
    N --> M[MCP 8101]
    M --> A[FastAPI 业务服务 8100]
    A --> R[资产与证据解析\nOCR / PDF / Office / 表格]
    A --> G[质量图谱与本体版本]
    A --> T[Trace 与审计]
    A --> Q[AOI 服务封装]
    Q --> C[原 AOI 核心脚本\nDINOv2 本地权重]
    A --> D[(SQLite / 文件资产)]
    A --> O[云同步 Outbox]
```

Nexent 负责对话推理、工具选择和 Skill 编排。MCP 是智能体与业务服务之间的工具协议。业务服务负责硬约束、数据版本、人工审核、回归门禁与证据记录。AOI 核心继续使用外部目录中的算法脚本和权重，业务工程不改变异常分数的含义。

## 三类版本

| 版本类型 | 示例 | 含义 |
|---|---|---|
| 智能体发布版本 | Nexent 页面 V1/V2 | Agent 配置、提示词、工具和 Skills 的发布历史 |
| AOI 实验版本 | V0/V1 | 面向演示的模型代号；对应真实 `model_...` ID |
| 本体版本 | `ontology_v3`/`ontology_v4` | 标准映射和知识结构的审核发布历史 |

三类版本独立演进。Nexent 页面显示 V2 不代表 AOI 已训练两轮，也不代表本体版本为 v2。

## 受控进化流程

```mermaid
flowchart TD
    I[100 正常 + 30 缺陷] --> V0[适配 V0]
    V0 --> FV0[冻结验证集]
    FV0 --> P0[人工发布 V0]
    P0 --> R[review_test 检测]
    R --> H[人工确认 / 纠正]
    H --> SNAP[初始 130 + 累计有效反馈快照]
    SNAP --> V1[候选 V1]
    V1 --> CMP[同一冻结验证集与父模型比较]
    CMP -->|通过| P1[人工发布 V1]
    CMP -->|失败| KEEP[保留 V0]
    P1 --> MT[machine_test 上机记录]
```

验证集不参与适配。`machine_test` 的机器结果不会成为学习标签。发布使用 `expected_current` 防止过期父版本切换。
