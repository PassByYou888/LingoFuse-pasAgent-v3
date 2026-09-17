# Pascal Integration Guide — pasAgent v3（V5.2 修正版）

> **文档版本**：V5.2（AI 协作重写版 · 自查修正版）
> **最后更新**：2026-09-17
> **适用**：Pascal 开发者（默认你已经是老手）
> **核心主张**：**你 + AI 一起做。你负责执行，AI 负责查文档。**

---

## 零、这份文档怎么用

```mermaid
flowchart LR
    A["📄 v3 文档 + 源码"] -->|"复制 / 上传"| B["🤖 豆包 / 千问 / GPT"]
    B -->|"读完"| C["🧠 它懂 v3 了"]
    C -->|"然后"| D["👨‍💻 让它教你一步步做"]
    D -->|"报错 / 卡住"| E["🔁 贴回给 AI"]
    E --> B

    style A fill:#1A5490,stroke:#0D2F52,stroke-width:4px,color:#FFFFFF
    style B fill:#8E44AD,stroke:#5B2C6F,stroke-width:5px,color:#FFFFFF
    style C fill:#1E8449,stroke:#0E4D2A,stroke-width:4px,color:#FFFFFF
    style D fill:#B7791F,stroke:#7E5109,stroke-width:4px,color:#FFFFFF
    style E fill:#922B21,stroke:#5A1A14,stroke-width:4px,color:#FFFFFF
```

### 具体怎么"喂"

| 平台 | 怎么喂 |
|------|--------|
| **豆包 / 千问** | 上传文件，或直接粘贴文本 |
| **GPT** | 上传文件，或粘贴文本 |
| **任何支持附件的 AI** | 把 `.md` / `.txt` / `.pas` / `.py` / `.lpi` / `.lpr` / `.lfm` 丢进去 |

> ⚠️ **v3 的文档和绝大多数源码都是纯文本，可以放心喂给 AI。**
> 例外：`.res`、`.ico` 是二进制资源文件，不用喂（AI 也读不懂）。

### 三个开箱话术模板（照抄即可）

**模板 1 · 让它教你上手**
> 我在读 pasAgent v3 项目的 `readme.md` 和 `Pascal_Integration_Guide.md`。
> 我是 Pascal 老手，第一次接触这个项目。
> 请**按顺序**告诉我：我想做 [你的目标]，第一步该做什么？每一步给出**具体命令**。
> 需要我读哪份文档，直接告诉我文件名。

**模板 2 · 让它解释报错**
> 我按你的步骤执行，报了下面的错：
> ```
> [贴你的完整报错]
> ```
> 请告诉我：**这是哪份文档的第几节**在讲这个问题？修法是什么？

**模板 3 · 让它教你查文档**
> 我在看 `src/LingoFuse_LLM_Proxy_Tool_CLI_Guide.md`。
> 我想搞懂 `--vision` 到底怎么用，以及它跟后端的什么配置配套。
> 请引用**原文段落**解释，不要概括。

---

## 一、能力边界（必须懂）

```mermaid
flowchart TB
    ROOT["🎯 pasAgent v3"]

    ROOT --> APPS["🌟 应用层（4 个）"]
    ROOT --> AUX["🛠️ 辅助（1 个）"]
    ROOT --> DEV["🎨 开发工具"]

    APPS --> A1["🌉 mcp_api_tool<br/>路径 A"]
    APPS --> A2["🔴 llm_proxy_tool（LTB）<br/>路径 B ⭐"]
    APPS --> A3["🟣 llm_proxy<br/>纯转发"]
    APPS --> A4["📦 llm_client_v3<br/>Pascal SDK"]

    AUX --> B1["🟢 llm_service<br/>纯文本本地推理"]

    DEV --> C1["💎 code_decl_to_mcp<br/>一份声明 → Pascal + Python"]

    style ROOT fill:#0D2F52,stroke:#000000,stroke-width:5px,color:#FFFFFF
    style APPS fill:#1E8449,stroke:#0E4D2A,stroke-width:4px,color:#FFFFFF
    style AUX fill:#B7791F,stroke:#7E5109,stroke-width:4px,color:#FFFFFF
    style DEV fill:#8E44AD,stroke:#5B2C6F,stroke-width:4px,color:#FFFFFF
    style A2 fill:#922B21,stroke:#5A1A14,stroke-width:5px,color:#FFFFFF
```

### 三条硬边界

```mermaid
flowchart TB
    B1["1️⃣ llm_service<br/>❌ 不支持多模态"]
    B2["2️⃣ 多模态必须走<br/>llm_proxy / LTB<br/>而且必须传 --vision"]
    B3["3️⃣ 视觉能力由后端决定<br/>后端必须是 VLM"]

    B1 --> IMPL["🎯 记住这 3 条"]
    B2 --> IMPL
    B3 --> IMPL

    style B1 fill:#922B21,stroke:#5A1A14,stroke-width:4px,color:#FFFFFF
    style B2 fill:#B7791F,stroke:#7E5109,stroke-width:4px,color:#FFFFFF
    style B3 fill:#8E44AD,stroke:#5B2C6F,stroke-width:4px,color:#FFFFFF
    style IMPL fill:#0D2F52,stroke:#000000,stroke-width:5px,color:#FFFFFF
```

> 💡 **让 AI 展开讲这三条**：
> 「请引用 `readme.md` 和 `src/LingoFuse_LLM_Ecosystem_User_Guide.md`，逐条解释这三条边界背后的源码事实。」

---

## 二、决策图

```mermaid
flowchart TB
    START["❓ 你的客户端情况"] --> Q1{"客户端支持<br/>MCP 协议？"}

    Q1 -->|"是"| A["🅰️ 路径 A<br/>mcp_api_tool"]
    Q1 -->|"否"| Q2{"需要<br/>工具调用？"}

    Q2 -->|"是"| B["🅱️ 路径 B ⭐<br/>llm_proxy_tool（LTB）"]
    Q2 -->|"否"| Q3{"需要<br/>图片问答？"}

    Q3 -->|"是"| C["🟣 llm_proxy<br/>+ --vision"]
    Q3 -->|"否"| D["🟣 llm_proxy<br/>纯对话"]

    style START fill:#0D2F52,stroke:#000000,stroke-width:4px,color:#FFFFFF
    style A fill:#1E8449,stroke:#0E4D2A,stroke-width:4px,color:#FFFFFF
    style B fill:#922B21,stroke:#5A1A14,stroke-width:5px,color:#FFFFFF
    style C fill:#8E44AD,stroke:#5B2C6F,stroke-width:4px,color:#FFFFFF
    style D fill:#8E44AD,stroke:#5B2C6F,stroke-width:4px,color:#FFFFFF
```

📖 **路径 A**：`src/LingoFuse_LLM_Ecosystem_User_Guide.md`
📖 **路径 B**：`src/LingoFuse_LLM_Proxy_Tool_CLI_Guide.md`
📖 **纯转发**：`src/LingoFuse_LLM_Proxy_CLI_Guide.md`

---

## 三、把 Pascal 函数接进 AI：四个步骤

> 每一步都配一个 **"问 AI 的话术模板"**，照抄即可。

### 步骤 1 · 写声明

```mermaid
flowchart LR
    A["✍️ 写声明"] --> B{"在 interface 段？<br/>顶层？<br/>参数只用基础类型？"}
    B -->|"是"| C["✅ 通过"]
    B -->|"否"| D["📖 pascal_code_mcp_rule.md"]

    style A fill:#1A5490,stroke:#0D2F52,stroke-width:4px,color:#FFFFFF
    style C fill:#1E8449,stroke:#0E4D2A,stroke-width:4px,color:#FFFFFF
    style D fill:#B7791F,stroke:#7E5109,stroke-width:4px,color:#FFFFFF
```

**四条铁律**：`interface` 段 + 顶层 + 禁 `var/out` + 只用整数/浮点/字符串。

**🎤 话术模板**：
> 把 `pascal_code_mcp_rule.md` 全文丢给 AI，然后问：
> 「我要暴露一个 Pascal 函数 `function DoXxx(...)`，请按这份规范**逐条检查**我写的是否合规。如果不合规，告诉我具体违反了第几节。」

---

### 步骤 2 · 生成工具提供者

```mermaid
flowchart LR
    A["📄 Pascal / C 声明"] -->|"粘贴"| B["💎 code_decl_to_mcp.exe<br/>GUI 应用"]
    B --> C1["🅿️ xxx_tool_provider_unit.pas"]
    B --> C2["🐍 xxx_tool_provider.py"]

    style B fill:#9B59B6,stroke:#6C3483,stroke-width:5px,color:#FFFFFF
    style C1 fill:#F39C12,stroke:#B7791F,stroke-width:4px,color:#FFFFFF
    style C2 fill:#27AE60,stroke:#145A32,stroke-width:4px,color:#FFFFFF
```

**关键点**：
- 生成器是 **GUI**，不是命令行
- 走完 5 个 Tab，最后一步复制产物
- `.pas` → `lazbuild`；`.py` → 直接 `python` 跑

**🎤 话术模板**：
> 把 `code_generate_mcp.md` 丢给 AI，然后问：
> 「我准备在 code_decl_to_mcp.exe 里粘贴我的声明。请告诉我**每个 Tab 我应该重点检查什么**，以及生成后 Pascal 侧要怎么编译。」

---

### 步骤 3 · 启动信标 + 工具提供者

```mermaid
sequenceDiagram
    participant T1 as 终端 1
    participant T2 as 终端 2
    participant T3 as 终端 3

    T1->>T1: pascal_agent_service.exe<br/>（信标，必须先启动）
    T2->>T1: pascal_agent_api.exe<br/>（工具提供者）
    T3->>T1: mcp_api_tool.exe<br/>（路径 A）<br/>或 llm_proxy_tool.exe（路径 B）
```

> **顺序不能乱**：信标 → 工具提供者 → 网关。

**🎤 话术模板**：
> 把 `src/LingoFuse_LLM_Proxy_Tool_CLI_Guide.md` 丢给 AI，然后问：
> 「我要用路径 B。请给我**可直接复制的启动命令**：终端 1、终端 2、终端 3 分别跑什么？我的后端是 LM Studio（127.0.0.1:1234）。」

---

### 步骤 4 · Pascal 客户端只发 `generate`

```pascal
LLM := TLLMClient.Create('LLM_Service', 'ipc:llm_service', 10000);
LLM.OnChunk := Do_LLM_Chunk;
if not LLM.Connect(err) then Exit;
if not LLM.Generate('5 加 7 等于几？', '', sid, err) then Exit;
// 客户端完全不知道工具、MCP、tool_calls 的存在
```

**🎤 话术模板**：
> 把 `src/llm_client_v3.md` 丢给 AI，然后问：
> 「我要写一个 Pascal GUI 客户端，连接 LTB。请给我**最小可运行代码**，包括连接、发送、接收流式 chunk，以及回调线程怎么 marshalling 到主线程。」

---

## 四、多模态：必须按步骤走

> ⚠️ **四条同时满足，缺一不可。** 任何一条不满足，图片都会被拒。

```mermaid
flowchart LR
    C1["1️⃣ 智能体客户端<br/>支持发送图片附件"] --> C2["2️⃣ 服务端<br/>传了 --vision"]
    C2 --> C3["3️⃣ 后端<br/>是 VLM + mmproj"]
    C3 --> C4["4️⃣ --backend-model<br/>指向 VLM"]

    C1 -.->|"❌ 任一缺失"| X["code: -1"]
    C2 -.-> X
    C3 -.-> X
    C4 -.-> X

    style C1 fill:#1A5490,stroke:#0D2F52,stroke-width:4px,color:#FFFFFF
    style C2 fill:#922B21,stroke:#5A1A14,stroke-width:5px,color:#FFFFFF
    style C3 fill:#8E44AD,stroke:#5B2C6F,stroke-width:5px,color:#FFFFFF
    style C4 fill:#1E8449,stroke:#0E4D2A,stroke-width:4px,color:#FFFFFF
    style X fill:#922B21,stroke:#5A1A14,stroke-width:5px,color:#FFFFFF
```

### 一步一步操作

```mermaid
flowchart TB
    S1["① LM Studio 加载 VLM<br/>（Qwen2-VL / Nemotron Omni 等）<br/>+ 同时选中 mmproj 视觉编码器"]
    S2["② 启动 LM Studio 本地服务器<br/>（端口 1234）"]
    S3["③ 启动 LTB<br/>必须加 --vision<br/>--backend-model 指向 VLM"]
    S4["④ Pascal 客户端调<br/>GenerateWithImageFile"]
    S5["⑤ 后端识别图片 → 返回文字"]

    S1 --> S2 --> S3 --> S4 --> S5

    style S1 fill:#1E8449,stroke:#0E4D2A,stroke-width:4px,color:#FFFFFF
    style S2 fill:#1E8449,stroke:#0E4D2A,stroke-width:4px,color:#FFFFFF
    style S3 fill:#922B21,stroke:#5A1A14,stroke-width:5px,color:#FFFFFF
    style S4 fill:#1A5490,stroke:#0D2F52,stroke-width:4px,color:#FFFFFF
    style S5 fill:#8E44AD,stroke:#5B2C6F,stroke-width:4px,color:#FFFFFF
```

**第 3 步的正确样子**：

```powershell
.\llm_proxy_tool.exe `
  --backend-url http://127.0.0.1:1234/v1 `
  --backend-model "qwen2-vl-7b-instruct" `   # ← 必须指向 VLM
  --vision                                    # ← 必须显式加
```

### 绝对不要做的事

```mermaid
flowchart LR
    A["🖼️ 图片"] -->|"发给"| B["🟢 llm_service"]
    B --> X["❌ code: -1<br/>本地 VLM 路径未实现"]

    style B fill:#1E8449,stroke:#0E4D2A,stroke-width:4px,color:#FFFFFF
    style X fill:#922B21,stroke:#5A1A14,stroke-width:5px,color:#FFFFFF
```

📖 **模型部署**：`NVIDIA-Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-IQ4_XS.md`
📖 **`--vision` 详解**：`src/LingoFuse_LLM_Proxy_Tool_CLI_Guide.md` 第 4.5 节
📖 **P8 多模态坑**：`src/LingoFuse_LLM_Pitfalls_For_AI.md`

**🎤 话术模板**：
> 把 `NVIDIA-Nemotron-...md` 和 `src/LingoFuse_LLM_Proxy_Tool_CLI_Guide.md` 一起丢给 AI，然后问：
> 「我要用 LM Studio + Nemotron Omni 做图片问答。请按顺序列出**每一个操作**：LM Studio 里点什么、LTB 命令怎么敲、Pascal 客户端怎么调。任何一步我做错会报什么错？」

---

## 五、六个场景：一句话方向

```mermaid
mindmap
  root(("🎯 场景 → 方向"))
    新建 FPC 项目
      原生 SDK + LTB
      📖 Build_Guide.md
    老 Delphi 项目
      独立单元 + 最小侵入
      📖 Pitfalls P4 系列
    手机 / 跨平台
      HTTP Bridge
      📖 lingofuse/Bridge_User_Guide.md
    GUI 工具
      先跑 llm_tool_v3 学套路
      📖 src/llm_client_v3.md
    商业部署
      LTB + 生产加固
      📖 LTB CLI Guide
    工业自动化
      离线优先
      📖 LLM Service CLI Guide
```

| 场景 | 关键点 |
|------|--------|
| **A 新建 FPC** | 用 `lazbuild`，不要直接 `fpc` |
| **B 老 Delphi** | 只加不改；回调要 marshalling |
| **C 手机 / Web** | `bridge.py` 是同步模式，不支持 SSE |
| **D GUI 工具** | 先编译 `llm_tool_v3` 跑通，再读源码 |
| **E 商业部署** | 密钥用文件；LTB 有四重上限 |
| **F 工业自动化** | 离线：`llm_service` + 本地 GGUF |

---

## 六、文档地图

```mermaid
flowchart TB
    L0["🥇 先喂这两份<br/>readme.md + Pascal_Integration_Guide.md"]
    L1["🥈 选读"]
    L2["🥉 按需"]

    L0 --> L1 --> L2

    L1 --> D1["src/LingoFuse_LLM_Ecosystem_User_Guide.md"]
    L1 --> D2["src/LingoFuse_LLM_Proxy_Tool_CLI_Guide.md"]
    L1 --> D3["src/llm_client_v3.md"]
    L1 --> D4["src/LingoFuse_LLM_Pitfalls_For_AI.md"]

    L2 --> E1["code_generate_mcp.md"]
    L2 --> E2["pascal_code_mcp_rule.md"]
    L2 --> E3["NVIDIA-Nemotron-...md"]
    L2 --> E4["Build_Guide.md"]

    style L0 fill:#0D2F52,stroke:#000000,stroke-width:5px,color:#FFFFFF
    style L1 fill:#1E8449,stroke:#0E4D2A,stroke-width:4px,color:#FFFFFF
    style L2 fill:#B7791F,stroke:#7E5109,stroke-width:4px,color:#FFFFFF
```

---

## 七、核心要点速记

> 📌 **三句话**：

1. **先喂 AI，再动手** —— 把 `readme.md` + 本指南丢给豆包 / 千问 / GPT，让它教你一步步做。
2. **多模态四条件同时满足** —— 智能体客户端支持图片 + 服务端 `--vision` + 后端 VLM + model 指向 VLM。
3. **细节都在专项文档** —— 本指南只给方向，参数 / 架构 / 坑点都在 `src/` 各手册里。

> 🎯 **记住这句话就够了**：
>
> **v3 的文档和代码都是给 AI 读的。让 AI 读、让 AI 教、你只管执行和反馈。遇到问题贴回去——它会告诉你翻哪份文档的第几节。**

---

**文档版本**：V5.2（AI 协作重写版 · 自查修正版——修正三步走/四步矛盾、删除虚构 token 限制、修正二进制文件陈述、统一术语）
**维护者**：LingoFuse-pasAgent 团队
**反馈**：问题提 Issue，急事加 Q（600585）