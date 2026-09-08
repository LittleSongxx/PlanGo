# 目标工作流：待实施建议

这是建议架构，除浏览器方向外尚未成为用户确认选型，也不代表已经实现。蓝色为 LLM 专业节点/工具 Agent，绿色为确定性机制或服务，黄色为用户交互。图中节点不应全部称为 Agent。

```mermaid
flowchart TB
    User["用户目标 / 需求修改"] --> Requirement["Requirement 专家<br/>统一 GoalSpec / RequirementPatch"]
    Requirement -->|需要澄清| User
    Requirement --> Diff["确定性合并 + 依赖失效"]
    Diff --> Research["Research 专家<br/>官方 API / 浏览器证据"]
    Research --> Planner["Planner 专家<br/>按需调用视角模块"]
    Planner --> Verify["确定性编译与三态校验<br/>满足 / 违反 / 未知"]
    Verify -->|未知，补证据| Research
    Verify -->|明确违反，有界修复| Repair["Planner / 可选 Critic"]
    Repair --> Verify
    Verify -->|可交付| Choice{"用户接受结果或请求执行"}
    Choice -->|只要方案| Outcome["统一结果契约<br/>计划交付 / 业务核验 / 人工确认 / 部分完成"]
    Choice -->|执行| Contract["ExecutionGoal / ActionContract<br/>业务参数与期望后置条件"]
    Contract --> Browser["Browser Agent + 受信执行器<br/>DOM-first / 按需 Vision"]
    Browser --> Effect{"动作影响与授权策略"}
    Effect -->|影响不明| Handoff
    Effect -->|页面交互| Execute["执行并记录实际观测"]
    Effect -->|外部写入| Confirm["用户确认具体业务参数"]
    Confirm --> Execute
    Execute --> Check["检查页面状态与业务身份"]
    Check -->|下一步| Browser
    Check -->|已知结果| Outcome
    Check -->|结果 UNKNOWN| Handoff["核对 / 人工接管<br/>禁止自动重放提交"]
    Handoff --> Outcome
    Outcome --> Feedback["用户反馈 / 可信终态事件"]
    Feedback --> Project["幂等记忆投影 + 用户偏好确认"]
    Project --> Store[("长期事实与经历")]
    Store -.->|下次任务按需读取| Requirement
    classDef llm fill:#e9f2ff,stroke:#416aa3,color:#132942;
    classDef service fill:#eef6f2,stroke:#55816a,color:#183627;
    classDef human fill:#fff5df,stroke:#b69045,color:#534018;
    class Requirement,Research,Planner,Repair,Browser llm;
    class Diff,Verify,Contract,Execute,Check,Project,Outcome service;
    class User,Choice,Confirm,Handoff,Feedback human;
```

注：所有重试、补查与修复受预算和停止条件约束；全局错误/取消路径在此语义摘要中省略。

## 图形复核

评分仅针对图形的语义、箭头与可读性，不是项目成熟度评分。

- [x] 原始 `.mmd` 与本文 Mermaid 代码一致；Mermaid CLI 11.17.0 渲染成功。
- [x] 已查看实际 PNG，核对每条箭头方向和目标；当前图与建议图分开标注。
- [x] 角色、确定性服务、人工交互与断点标签均与对应说明一致。
- [x] 主流程、澄清/修复回环和反馈边界完整；全局预算/错误路径的省略已说明。
- [x] 白底、克制配色、中文文字可读，无重叠遮挡关键标签。

问题与修正：当前图第一版的浏览器自环标签被终态块遮挡，已收进子流程说明；补齐需求澄清边。目标图补齐影响不明时的接管路径，避免误画成直接执行。

Score: 9/10。Verdict: ACCEPT。纵向主流程较长，适合文档滚动阅读；可用 Mermaid 预览缩放。

复现：`npx -y @mermaid-js/mermaid-cli@11.17.0 -i figures/yoyu-target-workflow.mmd -o figures/yoyu-target-workflow.png -b white`。本次使用本机已有 headless Chromium，通过 Puppeteer 配置指定路径；其他环境需准备 Puppeteer 浏览器。
