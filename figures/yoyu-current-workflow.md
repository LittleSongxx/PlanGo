# 当前实际工作流

基于 6b76a12 的语义摘要。阶段间的 Supervisor 回跳和全局预算/取消分支省略；不是逐节点导出的执行图。红色节点标出接棒与终态缺口。浏览器节点含自己的审批/回执子流程；Reflection 在拒绝等路径可达，并非所有终态都执行。

```mermaid
flowchart TB
    User["用户目标 / 多轮修改"] --> Entry["任务上下文 + 可选图片抽取"]
    Entry --> Route{"规则分流"}
    Route -->|行程| Memory["读取已有记忆"]
    Memory --> Requirement["Requirement 专家：结构化需求"]
    Requirement -->|需求缺信息| Ask
    Requirement --> Discovery["Discovery：类别检索 + 按需 LLM 补查"]
    Discovery --> Advocate["可选 Advocate：多人视角<br/>逻辑分支，模型请求串行"]
    Advocate --> Planner["Planner 专家：已观测地点的草案"]
    Planner --> Verify["确定性编译 / 校验 / 备选"]
    Verify -->|可修复| Critic["Critic 专家 + 有界确定性修复"]
    Critic --> Verify
    Verify -->|信息不足| Ask["询问用户 / 中断"]
    Ask --> Requirement
    Verify -->|可呈现| Approval{"用户选择方案 / 审批"}
    Approval -->|修改| User
    Approval -->|拒绝| Reflection["Reflection 专家<br/>按策略尝试记忆提案"]
    Reflection --> Final["最终状态"]
    Approval -->|批准| Handoff["准备浏览器执行<br/>目标上下文接棒存在缺口"]
    Handoff --> Browser["Browser 工具 Agent<br/>观测 - 决策 - 审批 - 回执<br/>读取与 type 可循环"]
    Route -->|网页任务| Browser
    Route -->|仅图像分支| ImageEnd["OCR 产物 / 结束或部分完成"]
    Browser --> BrowserEnd["结果或人工回填后结束<br/>普通 click 会提前终止<br/>正常终态未接回 Reflection"]
    classDef llm fill:#e9f2ff,stroke:#416aa3,color:#132942;
    classDef service fill:#eef6f2,stroke:#55816a,color:#183627;
    classDef gap fill:#fff0ee,stroke:#b76354,color:#612c23;
    class Requirement,Discovery,Advocate,Planner,Critic,Reflection,Browser llm;
    class Memory,Verify,Entry service;
    class Handoff,BrowserEnd gap;
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

复现：`npx -y @mermaid-js/mermaid-cli@11.17.0 -i figures/yoyu-current-workflow.mmd -o figures/yoyu-current-workflow.png -b white`。本次使用本机已有 headless Chromium，通过 Puppeteer 配置指定路径；其他环境需准备 Puppeteer 浏览器。
