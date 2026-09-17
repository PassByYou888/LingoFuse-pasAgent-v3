# LingoFuse LLM 工具链工作总结

> **文档路径**：`src/LingoFuse_LLM_Service_Work_Summary.md`
> **文档版本**：v7.0（v3 架构 + Structured Output 合并版）
> **涵盖周期**：2026-08-31 ~ 2026-09-17
> **涉及组件**：Python 服务端、Python 代理层、Python 工具桥（LTB）、Python 客户端、Pascal 客户端、Pascal GUI、工程脚本、全套文档、**Structured Output**
> **相关文档**（同目录）：
> - [`LingoFuse_LLM_Ecosystem_User_Guide.md`](LingoFuse_LLM_Ecosystem_User_Guide.md) — 生态总览
> - [`LingoFuse_LLM_Service_CLI_guide.md`](LingoFuse_LLM_Service_CLI_guide.md) — 本地推理服务手册
> - [`LingoFuse_LLM_Proxy_CLI_Guide.md`](LingoFuse_LLM_Proxy_CLI_Guide.md) — 纯转发代理手册
> - [`LingoFuse_LLM_Proxy_Tool_CLI_Guide.md`](LingoFuse_LLM_Proxy_Tool_CLI_Guide.md) — LTB 命令行手册
> - [`LingoFuse_LLM_Proxy_Compatibility_Guide.md`](LingoFuse_LLM_Proxy_Compatibility_Guide.md) — 后端兼容清单
> - [`LingoFuse_LLM_Pitfalls_For_AI.md`](LingoFuse_LLM_Pitfalls_For_AI.md) — 踩坑大全
> - [`llm_client_v3.md`](llm_client_v3.md) — Pascal 客户端 SDK 文档（**含 §16 Structured Output**）

**本次更新（v7.0）** 修正内容：
- **新增阶段 F**（Structured Output，2026-09-17 单日集中完成）：覆盖代理层 `response_format` 转发、客户端 4 个 SO 方法、GUI 结构化输出面板、学习文档、踩坑文档 P9 系列。
- **时间线**从五阶段扩展为**六阶段**。
- **新增整章「十七、Structured Output 能力」**，系统讲解从设计到落地。
- **经验总结**新增 SO 相关条目（经验 12–15）。
- **交付物清单**更新为 v7.0 最终状态。
- **踩坑文档**从 v4.0（P0–P8）扩展到 **v5.0（P0–P9）**。
- 全文版本号统一至 2026-09-17 实际值。

---

## 阅读引导

本文档是**版本演进与架构决策的历史记录**。如果你是：

- **想了解工具链全貌** → 读第一章「工作概览」和第二章「时间线」
- **想知道每个阶段做了什么** → 读第三、四、五、六、**七章**（服务端 / 代理层 / 工具执行 / v3 架构 / **Structured Output**）
- **想了解缺陷修复历程** → 读第八章
- **想了解协议演进** → 读第九章
- **想了解 SO 能力全景** → 读 **第七章**
- **想了解遗留问题** → 读第二十章
- **想直接上手** → 请转向 [`LingoFuse_LLM_Ecosystem_User_Guide.md`](LingoFuse_LLM_Ecosystem_User_Guide.md)

---

## 一、工作概览

本次工作围绕 **LingoFuse LLM 工具链** 展开，覆盖 **Python 服务端 / 代理层 / 工具桥 / 客户端** 与 **Pascal 客户端 / GUI** 两大语言生态，经历**六个主要阶段**：

1. **阶段 A（8/31 ~ 9/10）—— 服务端重构**
   把最初一个**单会话、无状态、串行**的 LLM 网关，升级为**多会话、持久化、结构化协议**的工业级组件（`llm_service.py` v3.0）。
2. **阶段 B（9/11 ~ 9/13）—— 代理层与生态**
   新增 **`llm_proxy.py`** —— 一个无状态转发器，把 LingoFuse 二进制 RPC 桥接到任何 OpenAI 兼容 HTTP 后端，并同步升级所有客户端与文档。
3. **阶段 C（9/13 ~ 9/14）—— 工具链收尾**
   统一四个 Python 工具的 `--help` 自适应；引入**跨语言 API 能力矩阵机制**；升级会话回收为**双条件判断**；一次性编译 EXE 的脚本；补齐依赖清单与兼容性指南；对所有交付文件做交叉审查。
4. **阶段 D（9/14）—— 代理层工具执行（LTB 引入）**
   新增 **`llm_proxy_tool.py`（LLM Tool Bridge，LTB）** —— 在代理层基础上增加**服务端侧工具执行**能力：MCP 工具发现、多轮 `tool_calls` 循环、结果回填，**客户端对工具完全无感知**。与 `mcp_api_tool.py` 使用不同 `reg_agent` 名字，可同时运行。
5. **阶段 E（9/14 ~ 9/17）—— v3 架构升级**
   **v3 独立开仓**。核心叙事从"三种服务端"改为"**四大应用组件 + 一个辅助工具**"；`llm_service` 从核心服务端降级为辅助验证工具；代码生成器支持**双语言输出（Pascal + Python）**；后端兼容清单从 129+ 扩展到 **250+**；**多模态转发**路径明确——`llm_proxy` / LTB 原样转发图片附件，是否支持取决于后端；`llm_service` **不支持多模态**（本地推理路径未实现）。
6. **阶段 F（9/17）—— Structured Output 能力** 🆕
   在代理层 + 客户端 + GUI + 文档**四层协同**引入 **Structured Output（结构化输出）**：代理层打通 `response_format` 转发链路；Pascal 客户端新增 **4 个入口**（含 **图片 + JSON Schema 一步到位**）；GUI 新增"结构化输出"面板（含检测器模板一键加载）；新增独立学习文档；踩坑文档从 P0–P8 扩展到 **P0–P9**。

### 一句话总结

> **从"能跑"到"能扛"，再到"能对接全世界"，再到"全生态对齐"，再到"客户端零改动即可享受工具能力"，再到"四大应用组件叙事 + 双语言代码生成 + 多模态转发路径"，最后到"Structured Output 能力：让模型输出 100% 可靠 JSON"：19 项服务端缺陷修复 + 8 次代理层迭代 + 一套跨语言 API 能力矩阵 + 服务端侧工具执行闭环 + v3 架构重组 + 250+ 后端兼容 + 双语言代码生成 + 全栈文档重写 + Structured Output 四层协同。**

### 图 1：交付物一览

```mermaid
mindmap
  root(("本次工作交付物"))
    Python服务端
      llm_service_py_v3_3
    Python代理层
      llm_proxy_py_v1_9
    Python工具桥
      llm_proxy_tool_py_v2_2
    Python客户端
      llm_test_py_v3_6
    Pascal客户端
      llm_client_v3_pas_v3_10
    PascalGUI
      llm_tool_v3_frm_pas_v3_4
    代码生成器
      code_decl_to_mcp_v5
      Pascal_Python_双输出
    工程脚本
      build_llm_service_ps1
      requirements_txt
    文档
      工作总结_v7_0
      生态总览_v5_1
      踩坑文档_v5_0
      学习指南_Structured_Output_v1_0
      SDK文档_llm_client_v3_v2_3
      服务端手册_v3_1
      代理手册_v4_1
      LTB手册_v3_1
      兼容清单_v4_1
```

---

## 二、时间线与版本演进

### 2.1 六阶段时间轴

```mermaid
timeline
    title LingoFuse LLM 工具链版本演进（六阶段）
    section 阶段 A：服务端重构
        缺陷研究 : 19 项缺陷分级
                 : 2 致命 + 6 严重 + 11 一般
        v2.0 架构重构 : 单 worker 串行化
                     : thinking 开关
                     : 结构化消息协议
        v2.1 配置统一 : 上下文默认 0
        v2.2 模板统一 : 显式路径加载
        v3.0 持久多会话 : session 生命周期
    section 阶段 B：代理层与生态
        llm_proxy v1.0 ~ v1.3 : 初版转发
        llm_proxy v1.4 : http_client 替换 requests
        llm_proxy v1.5 ~ v1.7 : Accept-Encoding identity
                              : TCP_NODELAY
        llm_proxy v1.8 : set_system_message 拒绝
        客户端同步 : emoji 控制台
    section 阶段 C：工具链收尾
        help 自适应 : 四工具统一
        API 能力矩阵 : get_api_capabilities
        会话双条件回收 : 超时 AND 离线
        工程脚本 : build_llm_service_ps1
    section 阶段 D：代理层工具执行
        LTB v2.0 : 服务端侧 tool_calls 循环
                 : MCP 工具发现
                 : 与 mcp_api_tool 共存
        LTB v2.1 : 预连接 middleware
                 : 多轮循环上限
                 : 工具结果截断
    section 阶段 E：v3 架构升级
        v3 独立开仓 : 多模态是衍生型机制
        四大应用组件 : mcp_api_tool / llm_proxy_tool / llm_proxy / llm_client
        llm_service 降级 : 辅助验证工具 + 可嵌入
        代码生成器 : Pascal + Python 双输出
        兼容清单 : 129+ 扩展到 250+
        多模态转发 : llm_proxy / LTB 原样转发
        文档重写 : 全部 v3 版
    section 阶段 F：Structured Output
        代理层 : response_format passthrough
              : sse_client 白名单扩展
        Pascal 客户端 : GenerateStructured
                      : GenerateWithJsonSchema
                      : GenerateWithImageFileAndSchema
                      : GenerateWithAttachmentsAndSchema
        GUI : 结构化输出面板
            : 检测器模板一键加载
        文档 : 学习指南 + SDK §16
             : 踩坑 P9 系列 15 条
```

### 2.2 版本编号对照

```mermaid
flowchart LR
    subgraph A["🔵 阶段 A：服务端"]
        V0["v1.0"] --> V30["v3.0"]
        V30 --> V33["v3.3"]
    end

    subgraph B["🟣 阶段 B：代理层"]
        PV0["v1.0"] --> PV18["v1.8"]
    end

    subgraph D["🔴 阶段 D：LTB"]
        T20["LTB v2.0"] --> T21["LTB v2.1"]
    end

    subgraph C["🟢 阶段 C：工具链"]
        C1["--help 自适应"] --> C2["能力矩阵"]
        C2 --> C3["双条件回收"]
        C3 --> C4["编译脚本"]
    end

    subgraph E["⚫ 阶段 E：v3 架构"]
        E1["v3 独立开仓"] --> E2["四大应用组件叙事"]
        E2 --> E3["llm_service 降级"]
        E3 --> E4["双语言代码生成"]
        E4 --> E5["多模态转发"]
    end

    subgraph F["🎯 阶段 F：Structured Output"]
        F1["代理层 response_format"] --> F2["客户端 4 个方法"]
        F2 --> F3["GUI SO 面板"]
        F3 --> F4["学习文档"]
        F4 --> F5["踩坑 P9 系列"]
    end

    V33 --> C1
    PV18 --> C1
    C4 --> T20
    T21 --> E1
    E5 --> F1

    style A fill:#1A5490,stroke:#0D2F52,stroke-width:3px,color:#FFFFFF
    style B fill:#5B2C6F,stroke:#321640,stroke-width:3px,color:#FFFFFF
    style C fill:#1E8449,stroke:#0E4D2A,stroke-width:3px,color:#FFFFFF
    style D fill:#922B21,stroke:#5A1A14,stroke-width:3px,color:#FFFFFF
    style E fill:#0D2F52,stroke:#000000,stroke-width:3px,color:#FFFFFF
    style F fill:#B7791F,stroke:#7E5109,stroke-width:4px,color:#FFFFFF
```

### 2.3 阶段 F 的时间刻度（单日集中）

阶段 F 是**单日集中完成**的 SO 能力引入，包含 5 个交付面：

| 时刻 | 交付面 | 产物 |
|------|--------|------|
| T+0 | 代理层 | `sse_client.py` 白名单扩展 + `llm_proxy.py` / `llm_proxy_tool.py` passthrough |
| T+1 | SDK | `llm_client_v3.pas` v3.9 → v3.10，新增 4 个 SO 方法 |
| T+2 | SDK 文档 | `llm_client_v3.md` v2.1 → v2.3，新增 §16 完整指南 |
| T+3 | GUI | `llm_tool_v3_frm.pas/lfm` v3.2 → v3.4，SO 面板 |
| T+4 | 学习文档 | `Structured_Output_Learning_Guide.md` v1.0 |
| T+5 | 踩坑文档 | `LingoFuse_LLM_Pitfalls_For_AI.md` v4.0 → v5.0，P9 系列 15 条 |

---

## 三、服务端架构演进（阶段 A）

### 3.1 架构对比：v1.0 与 v3.0

**v1.0 原始架构**：多线程直调共享 `llm` 实例，KV cache 污染 → 崩溃。

**v3.0 重构架构**：单 worker 串行化，回调只入队，Worker 独占调用 `llm`，通过 Sequenced Notify 分发。

**核心洞察**：llama.cpp 的 `llama_context` 是**单线程状态机**，v1.0 的多线程直调会导致进程级 abort。v3.0 用**单 worker 串行化**彻底消除这个类别的问题。

### 3.2 组件分层

接入层（Call API）→ 并发层（回调线程 / Queue / Worker / Watchdog）→ 推理层（Jinja2 模板 / ThinkingParser / llama_cpp.Llama）→ 通信层（LF_Sequenced_Notify）。

---

## 四、代理层架构（阶段 B + 阶段 D）

### 4.1 llm_proxy 的角色定位（阶段 B）

`llm_proxy.py` 是一个 **LingoFuse 服务端**，但它的内部逻辑与 `llm_service.py` 完全不同：它不加载模型，只做协议翻译。

### 4.2 无状态语义下的会话处理

代理**不持有**模型 KV cache，每轮请求都**重新组装**完整 messages 数组发给后端。

### 4.3 SSE 传输层的发现之旅（阶段 B 核心攻坚）

**三层缓冲，缺一不可**：

| 层 | 缓冲源 | 症状 | 修复 |
|----|--------|------|------|
| 1 | `iter_lines()` 内部 512 字节缓冲 | 短 SSE 行攒够才 yield | 换 `iter_content` |
| 2 | `iter_content(None)` = "读直到 EOF" | 整个响应才一次性返回 | 换 `chunk_size=1` |
| 3 | gzip 解码器攒够 deflate 块才吐 | 又是批量输出 | `Accept-Encoding: identity` |
| 4 | `urllib3` 内部预读 socket | 换了前面也还是延迟 | 换 `http.client` |

**最终方案**：`http.client` + `Accept-Encoding: identity` + `TCP_NODELAY`。

### 4.4 thinking 流的处理

**核心决策：代理不做任何策略**。是否产生 thinking 由**模型**决定，不由代理决定。

### 4.5 set_system_message 的拒绝机制

**从"假成功"到"明确拒绝"**。理由：代理是无状态转发器，"全局默认 system message"这个概念在其语义下不存在。**撒谎比拒绝更危险**。

### 4.6 LTB 的引入（阶段 D 核心）

#### 设计动机

阶段 B/C 完成后，代理层可以对接外部后端了，但存在一个死角：**很多客户端不支持 MCP**。LTB 的目标：让**任何** LingoFuse 客户端（哪怕只知道 `generate`）都能享受工具能力。

#### LTB 内部架构

客户端 → `generate` → LTB → （携带 tools 请求后端）→ （执行 tool_calls）→ 结果回填 → 后端继续生成 → 最终文本 → 客户端。

#### 多轮 tool_calls 循环（LTB 独有）

**关键设计**：

- **最后一轮不带 tools**：强制模型产出最终文本，保证循环终止
- **两重上限**：`max_tool_rounds`（轮次）+ `max_total_tool_calls`（总调用数）
- **两重截断**：`max_tool_result_chars`（单条结果）+ `max_total_tool_result_chars`（总结果）

#### 预连接 middleware（顺序敏感）

**关键发现**：`Server.start()` 内部会调用 `LF_PrepareDone()`，一旦主线程启动，`language_middleware._connect()` 再调用 `LF_PrepareDone()` 会返回 0 而非 1，从而**永久禁用工具缓存**。

**修复**：LTB 在 `Server.start()` **之前**先调 `_ensure_tools_ready()`。

---

## 五、v3 架构升级（阶段 E）

> **v3 独立开仓**。这一阶段不是简单的版本迭代，而是**架构级重组**。核心变化在于"叙事重心"的转移——从"三种服务端"改为"四大应用组件 + 一个辅助工具"。

### 5.1 为什么 v3 要独立开仓

**多模态（Multimodal）是一个衍生型机制**——它不是单个程序的升级，而是需要**周边程序协同支持**才能完整运转的架构级变革。

具体来说，引入多模态后：

- **`llm_service`** 需要同时驱动多个模型，并把不同模态的输入路由到对应模型
- **`llm_proxy` / `llm_proxy_tool`** 需要支持多模态请求的转发、多模态能力矩阵的声明
- **`llm_client`** 需要支持多模态内容构建（文本 + 图片附件的组合）
- **客户端**需要理解"同一服务端、不同模态能力"的语义

这些改动**跨越多个独立组件**，彼此耦合紧密。为了避免 v2 主仓库的稳定性被破坏，**v3 独立开仓**。

### 5.2 核心叙事重组

**v2 的叙事**：三种服务端（`llm_service` / `llm_proxy` / `llm_proxy_tool`）。

**v3 的叙事**：四大应用组件 + 一个辅助工具。

| 层级 | 组件 | 角色 |
|------|------|------|
| **第一梯队（应用层）** | `mcp_api_tool` | MCP 协议网关（路径 A） |
| | `llm_proxy_tool`（LTB） | LLM 工具桥（路径 B） |
| | `llm_proxy` | 纯文本转发代理 |
| | `llm_client` | Pascal 客户端 SDK |
| **第二梯队（辅助工具）** | `llm_service` | 本地推理服务 + 可嵌入的小工具 |
| **开发工具** | `code_decl_to_mcp` | 代码生成器（Pascal + Python 双输出） |

### 5.3 llm_service 定位调整

v2 中 `llm_service` 是"三种服务端之一"；v3 中它**降级为辅助验证工具**，因为它有**两个独特价值**：

1. **可脱离 pasAgent 生态单独使用**——作为你项目里的"本地 LLM 小工具"
2. **完全离线**——不需要联网、不需要外部 API

**生产环境推荐**：用 `llm_proxy` / `llm_proxy_tool` 转发到 LM Studio 等成熟后端。

### 5.4 代码生成器双语言输出

**v3 新增能力**：`code_decl_to_mcp` 从单语言输出扩展为**双语言输出**。

一份 Pascal / C 声明 → **Pascal 工具提供者 + Python 工具提供者**。

```mermaid
flowchart LR
    A["📄 Pascal / C 声明"] --> B["💎 code_decl_to_mcp"]
    B --> C1["🅿️ Pascal 工具提供者"]
    B --> C2["🐍 Python 工具提供者"]

    style A fill:#4A90E2,stroke:#1E3A8A,stroke-width:4px,color:#FFFFFF
    style B fill:#9B59B6,stroke:#6C3483,stroke-width:5px,color:#FFFFFF
    style C1 fill:#F39C12,stroke:#B7791F,stroke-width:4px,color:#FFFFFF
    style C2 fill:#27AE60,stroke:#145A32,stroke-width:4px,color:#FFFFFF
```

**关键价值**：

- **Pascal ↔ Python 互操作**——算法在 Pascal 里、集成在 Python 里？生成两份，各取所需
- **零样板代码**——`cdecl` 回调、JSON 序列化、工具注册全部自动生成
- **5 层透明模型**——每层 JSON 可看、可改、可回退，出错时能精确定位

### 5.5 后端兼容清单扩展

兼容清单从 **129+** 扩展到 **250+ 条目**，覆盖 **10 大类**：

| 类别 | v2 数量 | v3 数量 |
|------|:-------:|:-------:|
| 云 API（国际） | 30+ | **40+** |
| 云 API（中国区） | 15+ | **20+** |
| 本地推理服务器 | 20+ | **30+** |
| 网关 / 代理 / 路由 | 20+ | **35+** |
| API 聚合 / 中转站 | 15+ | **25+** |
| 桌面客户端 | 17+ | **25+** |
| Web UI | — | **20+** |
| 嵌入 / 重排序 / TTS / STT | 12+ | **25+** |
| 智能体框架 | — | **20+** |
| RAG 平台 | — | **20+** |
| **合计** | **129+** | **250+** |

### 5.6 多模态转发路径

**关键设计决策**：多模态能力**由后端决定**——`llm_proxy` 和 LTB 只是**原样转发**图片附件。

| 组件 | 多模态行为 |
|------|-----------|
| **`llm_service`** | ❌ **不支持**——本地推理路径未实现视觉编码器 |
| **`llm_proxy`** | 原样转发图片附件，能力取决于后端 |
| **`llm_proxy_tool`（LTB）** | 原样转发图片附件，能力取决于后端 |

**多模态的架构定位**：

```mermaid
flowchart LR
    A["🖥️ 客户端"] -->|"generate + 图片附件"| B["🌉 llm_proxy / LTB"]
    B -->|"原样转发（不解析）"| C["🔌 VLM 后端<br/>Qwen2-VL / Llava"]
    C -->|"识别图片"| D["返回文本"]

    style A fill:#1A5490,stroke:#0D2F52,stroke-width:3px,color:#FFFFFF
    style B fill:#922B21,stroke:#5A1A14,stroke-width:4px,color:#FFFFFF
    style C fill:#1E8449,stroke:#0E4D2A,stroke-width:3px,color:#FFFFFF
    style D fill:#B7791F,stroke:#7E5109,stroke-width:3px,color:#FFFFFF
```

### 5.7 v3 文档体系重写

**完整重写的文档**：

| 文档 | 变化 |
|------|------|
| `readme.md` | 🔴 重写——四大应用组件叙事 |
| `LingoFuse_LLM_Ecosystem_User_Guide.md` | 🔴 重写——v5.0 |
| `LingoFuse_LLM_Service_CLI_guide.md` | 🟠 大改——v3.0 |
| `LingoFuse_LLM_Proxy_CLI_Guide.md` | 🟠 大改——v4.0 |
| `LingoFuse_LLM_Proxy_Tool_CLI_Guide.md` | 🟠 大改——v3.0 |
| `LingoFuse_LLM_Proxy_Compatibility_Guide.md` | 🟠 大改——v4.0（129+ → 250+） |
| `LingoFuse_LLM_Pitfalls_For_AI.md` | 🟠 大改——v4.0（新增 P8 系列） |
| `Pascal_Integration_Guide.md` | 🟡 中改 |
| `Build_Guide.md` | 🟡 中改 |
| `NVIDIA-Nemotron-...md` | 🟡 中改 |

---

## 六、API 能力矩阵机制（阶段 C 引入，阶段 D/E 扩展）

### 6.1 设计背景

`llm_service.py` / `llm_proxy.py` / `llm_proxy_tool.py` 是**兄弟服务端**，共用同一端点，但功能集不同。客户端需要一种机制发现"当前运行的是哪一个"。

### 6.2 能力矩阵结构

```json
{
  "code": 0,
  "server_kind": "service" | "proxy",
  "capabilities": {
    "generate": 1,
    "create_session": 1,
    "close_session": 1,
    "cancel_session": 1,
    "list_sessions": 1,
    "set_system_message": 0,
    "health": 1,
    "llm_stream": 1,
    "tools": 0,
    "tool_calls": 0,
    "tool_results": 0,
    "attachments": 1,
    "vision": 0
  }
}
```

**三种服务端的差异**：

| API | `llm_service` | `llm_proxy` | `llm_proxy_tool`（LTB） |
|-----|:-------------:|:-----------:|:----------------------:|
| `set_system_message` | **1** | **0** | **0** |
| `tools` / `tool_calls` / `tool_results` | — | — | **1** |
| `server_kind` | `"service"` | `"proxy"` | `"proxy"` |

> **注意**：
>
> - `llm_proxy` 与 `llm_proxy_tool` 的 `server_kind` 都是 `"proxy"`；要区分二者，读 `tools` / `tool_calls` 等 LTB 特有字段。
> - **能力矩阵没有 `response_format` 字段**——Structured Output 是否可用由 **服务端类型 + 后端能力**共同决定。

### 6.3 客户端降级行为

**关键设计**：`HasCapabilityInfo` 与 `LLMSupported` 分离——前者回答"信息可靠吗"，后者回答"支持吗"。这个分离让"未知"与"不支持"得以区分。

---

## 七、Structured Output 能力（阶段 F · 新增）

> 🆕 **阶段 F 的核心交付**。这一阶段不是单个组件的升级，而是**代理层 + 客户端 + GUI + 文档四层协同**的能力引入。

### 7.1 什么是 Structured Output

**Structured Output（结构化输出）** 是一种让大模型**严格按照调用方提供的 JSON Schema 生成输出**的能力。

**核心机制**：不是"求"模型输出 JSON，而是**在模型生成 token 的层面强制约束**（约束解码 / Constrained Decoding）。

**对比**：

| 方案 | 成功率 | 字段名一致 | 类型一致 |
|------|:------:|:---------:|:-------:|
| 纯提示词 | 40-85% | ❌ | ❌ |
| 事后解析 | 60-90% | ❌ | ⚠️ |
| **Structured Output** | **100%** | **✅** | **✅** |

**支持链路**：

```mermaid
flowchart LR
    A["🅿️ Pascal 客户端<br/>llm_client_v3"] -->|"generate + options.response_format"| B["🌉 llm_proxy / LTB"]
    B -->|"原样转发"| C["🔌 LM Studio<br/>/ Ollama / vLLM"]
    C -->|"约束解码"| D["🤖 大模型<br/>Qwen2.5-VL 等"]
    D -.->|"合法 JSON"| A

    style A fill:#1A5490,stroke:#0D2F52,stroke-width:3px,color:#FFFFFF
    style B fill:#922B21,stroke:#5A1A14,stroke-width:3px,color:#FFFFFF
    style C fill:#1E8449,stroke:#0E4D2A,stroke-width:3px,color:#FFFFFF
    style D fill:#B7791F,stroke:#7E5109,stroke-width:3px,color:#FFFFFF
```

**不支持链路**：

```
客户端 → llm_service（本地 llama.cpp）
    ❌ 不转发 response_format
```

### 7.2 阶段 F 的四层协同

```mermaid
flowchart TB
    ROOT["🎯 Structured Output 能力"]

    ROOT --> L1["🔧 代理层"]
    ROOT --> L2["📦 客户端 SDK"]
    ROOT --> L3["🖼️ GUI"]
    ROOT --> L4["📚 文档"]

    L1 --> L1A["sse_client 白名单扩展"]
    L1 --> L1B["llm_proxy passthrough"]
    L1 --> L1C["LTB passthrough"]

    L2 --> L2A["GenerateStructured"]
    L2 --> L2B["GenerateWithJsonSchema"]
    L2 --> L2C["GenerateWithImageFileAndSchema ⭐"]
    L2 --> L2D["GenerateWithAttachmentsAndSchema"]

    L3 --> L3A["结构化输出面板"]
    L3 --> L3B["检测器模板一键加载"]
    L3 --> L3C["4 种组合自动分流"]

    L4 --> L4A["学习指南 v1.0"]
    L4 --> L4B["SDK §16 完整指南"]
    L4 --> L4C["踩坑 P9 系列 15 条"]

    style ROOT fill:#0D2F52,stroke:#000000,stroke-width:5px,color:#FFFFFF
    style L1 fill:#922B21,stroke:#5A1A14,stroke-width:3px,color:#FFFFFF
    style L2 fill:#1A5490,stroke:#0D2F52,stroke-width:3px,color:#FFFFFF
    style L3 fill:#8E44AD,stroke:#5B2C6F,stroke-width:3px,color:#FFFFFF
    style L4 fill:#B7791F,stroke:#7E5109,stroke-width:3px,color:#FFFFFF
```

### 7.3 代理层：打通转发链路

**问题**：`llm_proxy` / LTB 用**白名单**机制过滤 `options`，只转发列在 passthrough 里的字段。`response_format` 不在白名单里，**被静默丢弃**。

**解决**：三层修改，形成完整链路。

**层 1：`llm_common/sse_client.py`**

```python
# 扩展 _FORWARDED_PASSTHROUGH_KEYS，把 response_format 加入白名单
_FORWARDED_PASSTHROUGH_KEYS = (
    "tools",
    "tool_choice",
    "response_format",   # ← v3.1 起加入
)
```

**层 2：`llm_proxy.py`**

```python
# _handle_generate 中的 sanitize_options 加 passthrough
options = sanitize_options(
    options_raw,
    passthrough={
        "response_format": lambda v: isinstance(v, dict),   # ← 关键
    },
    ...
)
```

**层 3：`llm_proxy_tool.py`**

```python
# 与 llm_proxy.py 一致，加入 response_format
options = sanitize_options(
    options_raw,
    scalar_specs=COMMON_SCALAR_SPECS,
    passthrough={
        "tools":           lambda v: isinstance(v, list),
        "tool_choice":     lambda v: isinstance(v, (str, dict)),
        "response_format": lambda v: isinstance(v, dict),   # ← 关键
    },
    ...
)
```

**结果**：`options.response_format` 从客户端一路原样转发到后端，无损失。

**DEBUG 日志验证**：

```
[DEBUG] Backend request: url=... model=... msgs=N tools=no response_format=yes
```

### 7.4 客户端：4 个 SO 入口

`llm_client_v3.pas` v3.9 → v3.10，新增 4 个方法。

| 方法 | 用途 | 附件 | schema |
|------|------|:----:|:------:|
| `GenerateStructured` | 完整 `response_format` JSON 字符串 | ❌ | 完整 |
| `GenerateWithJsonSchema` | schema 名 + 本体 + strict | ❌ | 客户端组装 |
| **`GenerateWithImageFileAndSchema`** | **图片文件 + schema（检测器推荐）** | ✅ | 客户端组装 |
| `GenerateWithAttachmentsAndSchema` | 附件数组 + schema | ✅ | 客户端组装 |

**新增 3 个私有辅助**：

- `BuildImageAttachmentFromFile`：读文件 → 空文件检查 → base64 膨胀预检 → MIME 猜测 → base64 编码
- `BuildSchemaResponseFormatJson`：组装 `{"type":"json_schema","json_schema":{...}}`
- `SendGenerateCombined`：统一处理 4 种组合（附件×schema）的请求构造 + 发送 + 响应解析

**推荐入口**：**`GenerateWithImageFileAndSchema`**——图片 + schema 一步到位，无需手动组合。

### 7.5 GUI：结构化输出面板

`llm_tool_v3_frm.pas` v3.2 → v3.4，新增独立 TabSheet + 5 个控件：

| 控件 | 作用 |
|------|------|
| `EnableStructuredOutputCheckBox` | 总开关 |
| `SchemaNameEdit` | schema 名（默认 `object_detection`） |
| `StrictCheckBox` | strict 严格模式（默认勾选） |
| `LoadDetectorTemplateButton` | 一键加载内置检测器模板 |
| `SchemaMemo` | schema 本体编辑区 |

**`DoGenerateWithCurrentSettings` 自动分流**：

| 有 schema | 有附件 | 走的 API |
|:---------:|:------:|---------|
| ✅ | ✅ | `GenerateWithAttachmentsAndSchema` |
| ✅ | ❌ | `GenerateWithJsonSchema` |
| ❌ | ✅ | `GenerateWithAttachments` |
| ❌ | ❌ | `Generate` |

**典型使用流程**：

1. 连接 `llm_proxy` / LTB
2. 切到"结构化输出"页 → 点"加载检测器模板"
3. 勾选"启用 Structured Output" + "strict"
4. 切到"输入"页 → 添加图片
5. 点"生成"

### 7.6 文档：三层知识沉淀

**层 1：学习指南**（新增 `Structured_Output_Learning_Guide.md` v1.0）

- 从概念起源到工程实践的完整教程
- 10 章 + 3 附录
- 覆盖：定义 / 约束解码原理 / OpenAI 规范 / JSON Schema 基础 / 后端实现 / 6 大应用场景 / LingoFuse 实践 / 最佳实践 / 与其他方案对比 / 未来展望

**层 2：SDK 文档**（`llm_client_v3.md` v2.1 → v2.3）

- 新增 **§16 Structured Output 完整指南**
- 含 5 个检测器 JSON 模板
- 完整调用链分解
- 7 个常见坑 + 调试方法
- 可运行 Pascal 示例

**层 3：踩坑文档**（`LingoFuse_LLM_Pitfalls_For_AI.md` v4.0 → v5.0）

- 新增 **P9 系列 15 条 SO 坑**：
  - P9-1 `llm_service` 不支持 SO
  - P9-2 `response_format` 被静默丢弃
  - P9-3 `json_schema` 嵌套两层
  - P9-4 `strict` 用了字符串
  - P9-5 `additionalProperties` 缺失
  - P9-6 `required` 列表不完整
  - P9-7 `oneOf` 等不支持的关键字
  - P9-8 图片 + schema 无单一方法
  - P9-9 坐标未归一化 / 顺序错 / 上下颠倒
  - P9-10 `confidence` 超出 `0~1`
  - P9-11 schema 太复杂导致编译失败
  - P9-12 后端版本过旧
  - P9-13 模型不支持 SO
  - P9-14 `tool_calls` 与 `response_format` 冲突
  - P9-15 多轮工具循环中 `response_format` 未清除

### 7.7 关键设计决策（阶段 F）

| ID | 决策 | 动机 | 实现 |
|----|------|------|------|
| **F1** | `response_format` 走 passthrough | 代理层不做业务逻辑 | 三层白名单扩展 |
| **F2** | 4 个公开 SO 方法 | 覆盖不同复杂度的场景 | SDK v3.10 |
| **F3** | `GenerateWithImageFileAndSchema` 作为推荐入口 | 检测器场景的唯一最佳路径 | 内部 3 步：Build schema / Build image / Send combined |
| **F4** | GUI 4 种组合自动分流 | 用户只需勾选 + 填内容 | `DoGenerateWithCurrentSettings` |
| **F5** | 检测器模板一键加载 | 降低新用户上手门槛 | 内置标准模板（label + bbox + confidence） |
| **F6** | 学习 / SDK / 踩坑三层文档 | 不同读者的不同切入点 | 3 份文档 3 种视角 |
| **F7** | 不引入新依赖 | 代理层保持纯标准库 | `response_format` 只是 dict 透传 |

### 7.8 SO 能力边界（核心）

```mermaid
flowchart TB
    START["🎯 用 Structured Output"]

    START --> Q1{"服务端类型?"}
    Q1 -->|"llm_service"| X1["❌ 不支持<br/>改用 llm_proxy / LTB"]
    Q1 -->|"llm_proxy / LTB"| Q2{"后端支持 SO?"}
    Q2 -->|"否"| X2["❌ 升级到 LM Studio 0.3.0+<br/>Ollama 0.3.0+ / vLLM 0.6.0+"]
    Q2 -->|"是"| Q3{"模型 ≥ 7B 且支持 SO?"}
    Q3 -->|"否"| X3["❌ 换模型<br/>Qwen2.5-VL / Nemotron Omni"]
    Q3 -->|"是"| Q4{"需要图片?"}
    Q4 -->|"是"| Y1["✅ GenerateWithImageFileAndSchema"]
    Q4 -->|"否"| Q5{"需要多个附件?"}
    Q5 -->|"是"| Y2["✅ GenerateWithAttachmentsAndSchema"]
    Q5 -->|"否"| Y3["✅ GenerateWithJsonSchema"]

    style START fill:#0D2F52,stroke:#000000,stroke-width:5px,color:#FFFFFF
    style X1 fill:#922B21,stroke:#5A1A14,stroke-width:3px,color:#FFFFFF
    style X2 fill:#922B21,stroke:#5A1A14,stroke-width:3px,color:#FFFFFF
    style X3 fill:#922B21,stroke:#5A1A14,stroke-width:3px,color:#FFFFFF
    style Y1 fill:#1E8449,stroke:#0E4D2A,stroke-width:4px,color:#FFFFFF
    style Y2 fill:#1E8449,stroke:#0E4D2A,stroke-width:4px,color:#FFFFFF
    style Y3 fill:#1E8449,stroke:#0E4D2A,stroke-width:4px,color:#FFFFFF
```

**四条铁律**：

1. **`json_schema` 直接放在顶层，不要再嵌 `json_schema`**。
2. **`strict` 是布尔值，不是字符串**。
3. **`llm_service` 不支持，必须用 `llm_proxy` / LTB**。
4. **v3.10 一步到位**：图片 + schema 用 `GenerateWithImageFileAndSchema`，不要手动组合。

---

## 八、缺陷修复历程

### 8.1 阶段 A：19 项服务端缺陷

| 分类 | 数量 |
|------|:----:|
| 致命 P0 | 2 |
| 严重 P1 | 6 |
| 一般 P2 | 11 |

### 8.2 严重缺陷修复对照

| ID | 缺陷 | 修复策略 | 版本 |
|----|------|----------|------|
| **F1** | LLM 并发不安全 | 单 worker 线程串行化 | v2.0 |
| **F2** | 会话无管理 | Session 对象 + Watchdog | v3.0 |
| **S1** | system_message 竞态 | 请求时 snapshot | v2.0 |
| **S2** | 上下文预算算错 | tokenize 精确计数 | v2.0 |
| **S3** | 字符串魔法协议 | `type` 字段结构化 | v2.0 |
| **S4** | 双轨错误协议 | 统一 `{code, error}` | v2.0 |
| **S5** | 每 token 新句柄 | 保留（API 约束） | v2.0 |
| **S6** | 双重 cleanup | `_cleaned_up` 幂等标志 | v2.0 |

### 8.3 阶段 B：代理层 8 次迭代的缺陷

| ID | 缺陷 | 症状 | 修复 | 版本 |
|----|------|------|------|------|
| **P1** | 进程秒退 | 启动后立即退出 | 加主循环 + 信号处理 | v1.3 |
| **P2** | SSE 延迟 | 客户端 17 秒才收到 | `http.client` 替换 `requests` | v1.4 |
| **P3** | gzip 干扰 | 批量输出 | `Accept-Encoding: identity` | v1.5 |
| **P4** | Nagle 抖动 | 40ms 级别抖动 | `TCP_NODELAY` | v1.6 |
| **P5** | thinking 开关 | 代理替客户端做策略 | 移除开关，无策略转发 | v1.7 |
| **P6** | set_system_message 假成功 | 返回 code:0 但无效果 | 明确返回 unsupported | v1.8 |
| **P7** | 死字段残留 | `_default_system_message` 无用 | 删除字段 | v1.8 |
| **P8** | 未支持选项静默丢弃 | `thinking`/`ephemeral` 被忽略无记录 | DEBUG 日志记录 | v1.8 |

### 8.4 阶段 C：工具链收尾改动

| ID | 改动 | 组件 | 影响 |
|----|------|------|------|
| **T1** | `--help` 自适应 | 四个 Python 工具 | 用户体验 |
| **T2** | API 能力矩阵机制 | 服务端 + 双客户端 | 协议一致性 |
| **T3** | 会话双条件回收 | `llm_service.py` | 会话稳定性 |
| **T4** | 编译脚本一次性编译 EXE | `build_llm_service.ps1` | 构建效率 |
| **T5** | 依赖清单分场景 | `requirements.txt` | 安装便利性 |
| **T6** | 文档高对比配色 + 同目录链接 | 全部 LingoFuse_LLM*.md | 可读性、可维护性 |

### 8.5 阶段 D：LTB 引入的关键设计点

| ID | 设计点 | 动机 | 实现 |
|----|--------|------|------|
| **D1** | 服务端侧工具执行 | 客户端不支持 MCP 时也能用工具 | `_run_generation` 多轮循环 |
| **D2** | 客户端零改动 | 客户端只发 `generate` | 所有 tool_calls 内部消化 |
| **D3** | 与 mcp_api_tool 共存 | 两条路径并行 | 用不同的 `reg_agent` 名 |
| **D4** | 预连接 middleware | 避免 `Server.start()` 竞争 | 在 `Server.start()` 前调 `_ensure_tools_ready()` |
| **D5** | 多轮循环上限 | 防止模型无限循环 | `max_tool_rounds` + `max_total_tool_calls` |
| **D6** | 结果长度截断 | 防止消息历史爆炸 | `max_tool_result_chars` + `max_total_tool_result_chars` + `max_history_chars` |
| **D7** | 最后一轮不带 tools | 保证循环终止 | `is_final_round` 判定 |
| **D8** | 自动降级 | 工具不可用时行为等价 `llm_proxy` | `--enable-tools` / `_HAS_MIDDLEWARE` / 连接失败判定 |
| **D9** | INFO 级别静默 | 生产部署不刷屏 | 每轮/每工具细节走 DEBUG |
| **D10** | `reg_agent` 名隔离 | 与 `mcp_api_tool` 共存 | 默认 `llm_proxy_agent`（vs `mcp_api_tool` 的 `reg_agent`） |

### 8.6 阶段 E：v3 架构升级的关键设计点

| ID | 设计点 | 动机 | 实现 |
|----|--------|------|------|
| **E1** | v3 独立开仓 | 多模态是衍生型机制，需要周边程序协同 | `LingoFuse-pasAgent-v3` 新仓库 |
| **E2** | 四大应用组件叙事 | 叙事重心从"服务端"转移到"应用组件" | `mcp_api_tool` / `llm_proxy_tool` / `llm_proxy` / `llm_client` |
| **E3** | `llm_service` 降级 | 它是辅助验证工具，不是应用核心 | 定位改为"辅助 + 可嵌入" |
| **E4** | 代码生成器双语言 | 让 Pascal 声明可用于 Python 生态 | `code_decl_to_mcp` 输出 Pascal + Python |
| **E5** | 兼容清单扩展 | 让用户看到更广的接入可能性 | 129+ → 250+ 条目（10 大类） |
| **E6** | 多模态转发 | 让 Pascal 客户端也能处理图片 | `llm_proxy` / LTB 原样转发，后端决定 |
| **E7** | 文档体系重写 | 与 v3 叙事对齐 | 8 份文档全部重写或大改 |

### 8.7 阶段 F：Structured Output 的关键设计点

| ID | 设计点 | 动机 | 实现 |
|----|--------|------|------|
| **F1** | 代理层 `response_format` passthrough | 代理层不做业务逻辑 | `sse_client.py` 白名单 + `llm_proxy.py` / `llm_proxy_tool.py` passthrough |
| **F2** | SDK 4 个 SO 公开方法 | 覆盖不同复杂度场景 | `llm_client_v3.pas` v3.10 |
| **F3** | `GenerateWithImageFileAndSchema` 一步到位 | 检测器场景唯一推荐入口 | 内部 3 步：Build schema / Build image / Send combined |
| **F4** | GUI 4 种组合自动分流 | 用户只需勾选 + 填内容 | `DoGenerateWithCurrentSettings` |
| **F5** | 检测器模板一键加载 | 降低新用户上手门槛 | 内置标准模板（label + bbox + confidence） |
| **F6** | 三层文档沉淀 | 不同读者的不同切入点 | 学习指南 + SDK §16 + 踩坑 P9 |
| **F7** | 不引入新依赖 | 代理层保持纯标准库 | `response_format` 只是 dict 透传 |
| **F8** | 兼容旧版本（v3.9 及更早） | 已有代码无需修改 | 纯新增方法，无破坏性变更 |

### 8.8 缺陷修复可视化（象限图）

```mermaid
flowchart TB
    subgraph Q1["🔴 立即修复 —— 影响大 + 复杂度高"]
        direction LR
        F1["F1 并发不安全"]
        F2["F2 会话无管理"]
        P2["P2 SSE 延迟"]
        D1["D1 服务端工具执行"]
        E6["E6 多模态转发"]
        F3["F3 SO 一步到位"]
    end

    subgraph Q2["🟠 高优先级 —— 影响大 + 复杂度低"]
        direction LR
        P1["P1 进程秒退"]
        P3["P3 gzip 干扰"]
        P6["P6 假成功"]
        S3["S3 字符串魔法"]
        D3["D3 共存命名"]
        E2["E2 叙事重组"]
        F1b["F1 SO passthrough"]
    end

    subgraph Q3["🟡 一般关注 —— 影响中 + 复杂度中"]
        direction LR
        S1["S1 竞态"]
        S2["S2 预算错"]
        D4["D4 预连接顺序"]
        D5["D5 多轮上限"]
        E4["E4 双语言代码生成"]
        F4["F4 GUI 分流"]
    end

    subgraph Q4["🟢 排期修复 —— 影响小 + 复杂度低"]
        direction LR
        P4["P4 Nagle 抖动"]
        P7["P7 死字段"]
        D8["D8 自动降级"]
        E5["E5 兼容清单扩展"]
        F5["F5 模板一键加载"]
    end

    style Q1 fill:#FADBD8,stroke:#922B21,stroke-width:3px,color:#5A1A14
    style Q2 fill:#FDEBD0,stroke:#B7791F,stroke-width:3px,color:#7E5109
    style Q3 fill:#FEF9E7,stroke:#B7950B,stroke-width:3px,color:#7E5109
    style Q4 fill:#EAECEE,stroke:#5D6D7E,stroke-width:3px,color:#2C3E50
```

---

## 九、协议演进

### 9.1 流式消息协议对比

| 版本 | 协议 | 特点 |
|:----:|------|------|
| **v1.0** | 字符串魔法 | `{"chunk": "__FINISH__"}`——用 `startswith` 判断 |
| **v3.0+** | 结构化 JSON | `{"type": "finish", "reason": "stop"}`——读 `type` 字段 |

**注意**：LTB 对客户端**只发送** `chunk` / `think` / `finish` / `error` / `closed`。**工具调用过程完全不暴露给客户端**——没有 `tool_calls` / `tool_result` 消息类型。

### 9.2 请求协议演进（options 字段）

| 版本 | 支持的 options | 备注 |
|:----:|:-------------:|------|
| **v1.0** | 无 | 只发 content + prompt |
| **v2.0** | 5 个 scalar（`max_tokens` / `temperature` / `top_p` / `top_k` / `repeat_penalty`） | 白名单 |
| **v2.x（LTB）** | + `tools` / `tool_choice` | passthrough |
| **v3.10（SO）** | + `response_format` | passthrough |

### 9.3 三种服务端行为对照

| 维度 | `llm_service.py` | `llm_proxy.py` | `llm_proxy_tool.py` |
|------|:----------------:|:--------------:|:-------------------:|
| **模型加载** | 本地 llama.cpp | 无 | 无 |
| **推理线程** | 单 worker 串行 | 无推理 | 无推理 |
| **上下文管理** | 服务端持有 KV cache | 每轮重建 messages | 每轮重建 messages |
| **set_system_message** | ✅ 支持 | ❌ 明确拒绝 | ❌ 明确拒绝 |
| **工具执行** | 无 | 无 | ✅ 服务端代管 |
| **tool_calls 循环** | 无 | 无 | ✅ 内置多轮 |
| **会话回收** | 双条件（超时 + 离线） | 单条件（超时） | 单条件（超时） |
| **多模态** | ❌ 不支持 | ✅ 原样转发 | ✅ 原样转发 |
| **Structured Output** | ❌ 不支持 | ✅ 原样转发 | ✅ 原样转发 |
| **端点 / App 名** | `ipc:llm_service` / `LLM_Service` | 同左 | 同左 |
| **同时运行** | ❌ | ❌ | ❌ |

---

## 十、多会话状态机

### 10.1 会话生命周期（三端一致）

会话在 `queued` / `running` / `idle` / `cancelled` / `error` / `closing` 状态间流转。

### 10.2 会话对象结构对照

| 类型 | 使用方 | 特殊能力 |
|------|--------|----------|
| `SessionState_Service` | `llm_service` | 三值 status + current_cancel_event |
| `SessionState_Proxy` | `llm_proxy` | running + cancel_event |
| `SessionState_LTB` | `llm_proxy_tool` | + `add_assistant_tool_calls` + `add_tool_result` + `_trim_locked` 保 tool_calls 组完整 |

---

## 十一、Thinking 分流机制

### 11.1 状态机原理

`Outside` ↔ `Inside` 两态机，遇 `<think>` 进，遇 `</think>` 出。`Outside` 文本 → `chunk`；`Inside` 文本 → `think`。

### 11.2 三条路径

- **`llm_service`**：模板预置 thinking 标记或模型自发，`ThinkingParser` 状态机分流
- **`llm_proxy`**：后端 SSE 已分离 `reasoning_content` 和 `content`，代理无策略转发
- **`llm_proxy_tool`**：与 `llm_proxy` 相同——`reasoning_content` 直接转为 `think`

### 11.3 关键差异

| 维度 | `llm_service` | `llm_proxy` | `llm_proxy_tool` |
|------|:-------------:|:-----------:|:----------------:|
| **thinking 判断** | 模板预置 + 状态机 | 后端已分离字段 | 后端已分离字段 |
| **是否可关** | 模板变量控制 | 无开关 | 无开关 |
| **tool_calls 处理** | 无 | 无 | **内部消化** |

---

## 十二、Chat Template 加载机制

### 12.1 加载路径

**v3.3 起简化**：不再搜索目录，只有显式指定时才加载模板文件。

| 版本 | 行为 |
|------|------|
| v3.2 及之前 | 默认搜索脚本目录、父目录、CWD，找到 `chat_template.jinja` 就加载 |
| **v3.3 起** | **默认空，不搜索**。只认显式 `--chat-template` 路径 |

**注意**：代理层（`llm_proxy` / `llm_proxy_tool`）**不加载模板**。后端（LM Studio 等）自己处理。

---

## 十三、客户端对接

### 13.1 Pascal 客户端（本仓库 `llm_client_v3.pas`）修复历程

从 v3.0 到 **v3.10**，Pascal 客户端经历了多轮迭代：

| 版本 | 主要变化 |
|------|---------|
| v3.0 ~ v3.3 | 基础功能 + 能力发现 + S1-S4 修复（早期） |
| v3.6 → v3.7 | 清理 6 个冗余辅助函数 |
| v3.7 → v3.8 | `TZ_JsonString` 类型统一（破坏性变更） |
| v3.8 → v3.9 | 新增 `GenerateStructured` / `GenerateWithJsonSchema` |
| **v3.9 → v3.10** | **新增 `GenerateWithImageFileAndSchema` / `GenerateWithAttachmentsAndSchema`** |

### 13.2 Pascal 客户端严重问题

| ID | 问题 | 后果 | 修复 |
|----|------|------|------|
| **S1** | `Connect` 失败路径泄漏 `FApp` | 内存泄漏 + 无法重连 | `CleanupPartialConnect` |
| **S2** | `Disconnect` 早退导致主线程泄漏 | LingoFuse 主线程常驻 | `FPrepared` 追踪状态 |
| **S3** | JSON 经 `string` 中转 | 中文乱码 | 全程 `TBytes` |
| **S4** | `OnLLMStream` 未检 buffer | 空消息崩溃 | 判空 `buf` / `sz` |

### 13.3 GUI 层新增功能

**本仓库 `llm_tool_v3_frm.pas` v3.4 的新增功能**：

| 功能 | 说明 |
|------|------|
| 附件面板（v3.2） | 文本 / 图片 / 剪贴板三种附件来源 |
| 剪贴板图片转 PNG（v3.2） | 保证 MIME 命中服务端白名单 |
| **结构化输出面板（v3.3）** | **新增 TabSheet + 5 个控件** |
| **检测器模板一键加载（v3.3）** | **内置标准 schema** |
| **4 种组合自动分流（v3.3）** | **`DoGenerateWithCurrentSettings`** |
| **附件 + schema 组合解除限制（v3.4）** | **改用 v3.10 组合方法，不再拒绝** |

**Pascal GUI 推荐**：直接连 `llm_proxy_tool.exe`（路径 B），客户端不需要任何 MCP 相关代码。

---

## 十四、配置模型统一

### 14.1 三级优先级的统一模式

**四种**工具（`llm_service` / `llm_proxy` / `llm_proxy_tool` / `llm_test`）都遵循**完全一致**的配置模型：

```
命令行参数 > 环境变量 > 内置默认值
```

**统一点**：

1. 所有配置项有 `DEFAULT_*` 模块级常量
2. 初始化时创建全局 `CONFIG` 对象
3. `parse_args()` 从 env + argv 读取
4. `_init_global_config(args)` 写回 CONFIG
5. 运行时**只读 CONFIG**，不再重读 env / argv

**四个文件的实现对照**：

| 文件 | CONFIG 类 | 初始化函数 | 配置项数 |
|------|-----------|-----------|:--------:|
| `llm_service.py` | `ServiceConfig` | `_init_global_config` | 16 |
| `llm_proxy.py` | `ProxyConfig` | `_init_global_config` | 12 |
| `llm_proxy_tool.py` | `ProxyConfig` | `_init_global_config` | **24** |
| `llm_test.py` | 使用 argparse 局部变量 | — | 8 |

---

## 十五、编译与工程交付

### 15.1 一次性编译四个 EXE

**`build_llm_service.ps1`** 一次性编译：

| 源文件 | 输出 | 特殊依赖 |
|--------|------|----------|
| `llm_service.py` | `llm_service.exe` | `llama_cpp`、`jinja2` |
| `llm_proxy.py` | `llm_proxy.exe` | `http.client`、`ssl`（无第三方依赖） |
| **`llm_proxy_tool.py`** | **`llm_proxy_tool.exe`** | `http.client`、`ssl`、**`language_middleware`** |
| `llm_test.py` | `llm_test.exe` | 仅 `lingofuse` |

**关键区别**：`llm_proxy_tool.exe` 必须带 `--hidden-import language_middleware`（源码里是 `try/except` 导入，静态分析不可见）。

### 15.2 依赖清单分场景

`requirements.txt` 按组件分场景组织：

| 段落 | 依赖 | 用途 |
|------|------|------|
| HTTP 桥接 | `flask` | `bridge.py` |
| 本地推理 | `llama-cpp-python` | `llm_service.py` |
| 聊天模板 | `jinja2` | 自定义模板 |
| 打包 | `pyinstaller` | 生成 EXE |
| 开发 | `pytest`、`black`、`flake8` | 可选 |

> **注意**：`llm_proxy.py` / `llm_proxy_tool.py` **已不再依赖 `requests`**——SSE 客户端已改为纯标准库 `http.client`。

---

## 十六、代码审查结论

### 16.1 通过项

- API 名称一致性：全部对齐
- 能力矩阵键集：全部对齐
- 请求响应字段：全部对齐
- 流式消息字段：全部对齐
- `--help` 自适应：四个 Python 工具统一
- 会话双条件回收：`llm_service` 专用
- Pascal 能力发现：完整
- LTB 与 `mcp_api_tool` 共存：`reg_agent` 不同
- 文档同目录链接：全部指向同目录
- **Structured Output 转发链路：三层白名单全对齐（sse_client / llm_proxy / LTB）**
- **SDK 4 个 SO 方法：全部通过单元测试**
- **GUI 4 种组合分流：全部验证**

### 16.2 待优化项（不阻塞）

| 编号 | 严重度 | 文件 | 简述 |
|:----:|:------:|------|------|
| 1 | 🟠 P3 | Pascal 客户端 | `Connect` 中 `FetchCapabilities` 会覆盖 out 参数 |
| 2 | 🟠 P3 | `llm_test.py` | `llm_supported` 缓存未命中缺键时会重复 fetch |
| 3 | 🟠 P3 | GUI 窗体 | 后台线程调用 `DoStatus` 存在 UI 线程安全疑点 |
| 4 | 🟡 P4 | GUI 窗体 | `FormClose` 跳过 `Disconnect`（已知问题） |
| 5 | 🟡 P4 | `llm_service.py` | 未使用的导入 |
| 6 | 🟡 P4 | LTB | `response_format` 在多轮工具循环中未清除（P9-15） |
| 7 | 🟡 P4 | LTB | `tools` 与 `response_format` 竞争，未实现"仅最后一轮加 SO" |

---

## 十七、数据统计

### 17.1 各阶段问题数

```mermaid
xychart-beta
    title "各阶段问题数"
    x-axis ["服务端研究", "v2.0", "v2.2", "v3.0", "代理层", "Pascal", "工具链", "LTB", "v3架构", "SO"]
    y-axis "Issue Count" 0 --> 25
    bar [19, 8, 3, 5, 8, 6, 6, 9, 7, 15]
```

### 17.2 代理层迭代修复项数

```mermaid
xychart-beta
    title "llm_proxy.py + llm_proxy_tool.py 迭代修复项数"
    x-axis ["proxy v1.0", "v1.3", "v1.4", "v1.5", "v1.6", "v1.7", "v1.8", "LTB v2.0", "LTB v2.1", "SO"]
    y-axis "Fixes" 0 --> 6
    bar [1, 1, 2, 1, 1, 1, 3, 5, 4, 3]
```

### 17.3 支持的后端统计

| 类别 | v2 数量 | v3 数量 |
|------|:-------:|:-------:|
| 云 API 提供商（国际） | 30+ | **40+** |
| 云 API 提供商（中国区） | 15+ | **20+** |
| 本地推理服务器 | 20+ | **30+** |
| 网关 / 代理 / 路由 | 20+ | **35+** |
| API 聚合 / 中转站 | 15+ | **25+** |
| 桌面客户端（自带 Server） | 17+ | **25+** |
| Web UI | — | **20+** |
| 嵌入 / TTS / STT（部分支持） | 12+ | **25+** |
| 智能体框架 | — | **20+** |
| RAG 平台 | — | **20+** |
| **合计** | **129+** | **250+** |

> **该清单对 `llm_proxy.py` 和 `llm_proxy_tool.py` 均适用**（两者底层 SSE 客户端完全一致）。

---

## 十八、交付物清单

### 18.1 服务端（Python，本仓库 `src/`）

| 文件 | 版本 | 说明 |
|------|------|------|
| `llm_service.py` | **v3.3** | 持久化多会话流式服务端（本地推理，辅助验证工具） |
| `llm_proxy.py` | **v1.9** | 无状态转发器（**含 Structured Output 转发**） |
| **`llm_proxy_tool.py`** | **v2.2** | **转发器 + 服务端侧工具执行（LTB）+ SO 转发** |

### 18.2 共享模块（Python，本仓库 `src/llm_common/`）

| 文件 | 版本 | 说明 |
|------|------|------|
| `sse_client.py` | **v3.2** | **`_FORWARDED_PASSTHROUGH_KEYS` 含 `response_format`** |
| `option_sanitizer.py` | v1.1 | options 白名单过滤 |
| `attachments.py` | v1.x | 附件验证与转换 |
| `capabilities.py` | v1.x | 能力矩阵 |
| `session_base.py` | v1.x | 会话基类 |
| `session_multimodal.py` | v1.x | LTB 会话（含 tool_calls 组保护） |
| 其他 | v1.x | banner / headers / runtime / logging_setup |

### 18.3 客户端（Python，本仓库 `src/`）

| 文件 | 版本 | 说明 |
|------|------|------|
| `llm_test.py` | **v3.6** | 交互式多会话 REPL，emoji 支持，能力发现 |

### 18.4 客户端（Pascal，本仓库 `src/`）

| 文件 | 版本 | 说明 |
|------|------|------|
| **`llm_client_v3.pas`** | **v3.10** | **含 4 个 Structured Output 方法** |
| **`llm_tool_v3_frm.pas`** | **v3.4** | **含结构化输出面板 + 4 种组合分流** |
| `llm_tool_v3_frm.lfm` | **v3.4** | **含 SO 面板控件定义** |
| `llm_tool_v3.lpi` | v3.x | GUI 项目文件 |

### 18.5 MCP 网关与工具桥（Python，本仓库 `src/`）

| 文件 | 版本 | 说明 |
|------|------|------|
| `mcp_api_tool.py` | **v2.42** | MCP 网关，stdio 主进程 + 参数类型注解 + ConsoleOutput 抑制 |
| `language_middleware.py` | **v7.3** | `_read_string` 容错 + `ensure_ascii=False` + `reg_tool` 字段名对齐 |
| `mcp_api_proxy.py` | **v2.5** | stdio 通信代理 |
| `generate_agent_json.py` | **v2.5** | 配置生成器 |
| `_lf_native.py` | v1.x | 加载信息走 stderr |

### 18.6 代码生成器（LingoFuse 核心仓库）

| 文件 | 版本 | 说明 |
|------|------|------|
| `code_decl_to_mcp` | **v5.0** | 双语言代码生成（Pascal + Python） |

### 18.7 工程脚本（本仓库 `src/`）

| 文件 | 说明 |
|------|------|
| `build_mcp_api_tool.ps1` | 编译 `mcp_api_tool.exe` + `mcp_api_proxy.exe` |
| `build_llm_service.ps1` | 一次性编译**四个** LLM EXE |
| `build_bridge.ps1` | 编译 `bridge.exe` |
| `build_pascal_agent.bat` | Lazarus 一键编译 Pascal 项目 |
| `requirements.txt` | 按组件分场景的依赖清单 |

### 18.8 文档（本仓库）

| 文件 | 版本 | 说明 |
|------|------|------|
| `readme.md` | **v3.0** | 项目总览（四大应用组件叙事） |
| `Pascal_Integration_Guide.md` | **v5.2** | Pascal 开发者切入指南 |
| `Build_Guide.md` | **v4.2** | 编译指南 |
| `NVIDIA-Nemotron-...md` | **v3.2** | 推荐模型下载与部署 |
| `Structured_Output_Learning_Guide.md` | **v1.0** | **🆕 Structured Output 完整学习指南** |
| `src/LingoFuse_LLM_Ecosystem_User_Guide.md` | **v5.1** | 生态总览 |
| `src/LingoFuse_LLM_Service_CLI_guide.md` | **v3.1** | 本地推理服务手册 |
| `src/LingoFuse_LLM_Proxy_CLI_Guide.md` | **v4.1** | 纯转发代理手册 |
| `src/LingoFuse_LLM_Proxy_Tool_CLI_Guide.md` | **v3.1** | LTB 命令行手册 |
| `src/LingoFuse_LLM_Proxy_Compatibility_Guide.md` | **v4.1** | 250+ 后端兼容清单 |
| `src/LingoFuse_LLM_Pitfalls_For_AI.md` | **v5.0** | 踩坑大全（**P0–P9 系列**） |
| `src/llm_client_v3.md` | **v2.3** | Pascal 客户端 SDK 文档（**含 §16 Structured Output**） |
| `src/LingoFuse_LLM_Service_Work_Summary.md` | **v7.0** | 本文档 |

---

## 十九、经验总结

### 19.1 核心经验

```mermaid
mindmap
  root(("经验总结"))
    架构层面
      单线程不总是性能差
      串行化是稳定性根基
      队列是最好的解耦
    协议层面
      结构化优于字符串魔法
      UTF-8 全程贯通
      能力矩阵替代硬编码假设
    跨语言层面
      字节流是通用语言
      事件签名必须严格对齐
      var/out 在 FPC 下同签名
    传输层面
      SSE 三层缓冲缺一不可
      http_client 是唯一可靠方案
      identity 禁用 gzip
    语义层面
      无状态转发不加状态
      假成功比拒绝更危险
      客户端零改动是终极目标
      未知不等于不支持
    v3架构层面
      多模态是衍生型机制
      叙事重心决定用户心智
      辅助工具不等于配角
      代码生成器是长期资产
      转发者角色也能创造价值
    结构化输出层面
      schema 是新的契约
      让一步到位成为默认入口
      跨层协同才能落地
      文档的触发式生长
```

### 19.2 v3 架构决策的经验

**经验 7：叙事重心决定用户心智**

v2 的叙事是"三种服务端"，用户心智是"选一个服务端"。v3 的叙事是"四大应用组件 + 一个辅助工具"，用户心智是"选一个场景的解决方案"。**同样的代码，不同的叙事，用户的选择路径完全不同**。

**经验 8：辅助工具不等于配角**

`llm_service` 在 v3 中降级为"辅助验证工具"，但它有**两个独特价值**：完全离线、可嵌入。把它从"核心服务端"挪出来，反而让它的定位更清晰——**它就是那个你不需要整个生态就能单独使用的本地 LLM 小工具**。

**经验 9：代码生成器是长期资产**

`code_decl_to_mcp` 的双语言输出让 Pascal 声明可以无缝进入 Python 生态。这是**跨语言协作的关键基础设施**——一份声明，两种工具提供者，语义完全一致。

**经验 10：转发者角色也能创造价值**

`llm_proxy` / LTB 在多模态路径上只是"转发者"——它们不解析图片，只是把 `attachments` 原样传给后端。但这**恰恰是正确的设计**：代理层不应该有业务逻辑，能力由后端决定。

**经验 11：兼容清单是生态的"心"**

一个 250+ 条目的兼容清单，让用户看到"我的后端能不能用"。**兼容清单的丰富程度，直接反映了生态的开放程度**。

### 19.3 Structured Output 阶段的经验（阶段 F）

**经验 12：schema 是新的契约**

SO 的核心不是"让模型输出 JSON"，而是**用 JSON Schema 作为调用方和模型之间的契约**。这个契约比"提示词 + 事后解析"更**严格**、更**可靠**、更**可维护**。一旦你的系统里有 5 个以上的数据提取任务，就该考虑用 SO 统一。

**经验 13：让一步到位成为默认入口**

早期的 SO 方案需要用户**手动组合**"附件 + schema"两次调用。这有**两个问题**：
- 用户容易漏字段或结构错。
- 代码变得很长，可读性差。

v3.10 新增 `GenerateWithImageFileAndSchema` 后，**一行代码解决所有问题**。**能封装成一个 API 的，绝不让用户手动组合**。

**经验 14：跨层协同才能落地**

SO 不是"客户端加个参数"就完事的。它需要**四层协同**：
- **代理层**：白名单扩展 + passthrough（否则静默丢弃）
- **客户端**：新增 4 个方法
- **GUI**：新增面板 + 自动分流
- **文档**：学习指南 + SDK 指南 + 踩坑

**任何一层缺失，用户都会"用不成"**——这正是为什么 SO 是一个"能力"而不是"特性"。

**经验 15：文档的触发式生长**

SO 相关的工作产生了**三份新/更新文档**：
- **学习指南**（新增）——系统理解
- **SDK §16**（更新）——具体使用
- **踩坑 P9 系列**（更新）——问题排查

**每一份都服务不同的读者**：入门者看学习指南，工程师看 SDK §16，调试者看踩坑。**文档应该按"触发场景"分层，而不是堆在一份里**。

### 19.4 三条铁律

```mermaid
mindmap
  root(("三条铁律"))
    铁律一
      client_name 是身份
      不是标签
      必须真实注册
    铁律二
      回调要快
      耗时操作入队
      阻塞调用禁止
    铁律三
      字节流是通用语言
      UTF-8 全程
      不经 string 中转
```

---

## 二十、遗留问题与后续建议

### 20.1 尚未处理的问题

```mermaid
flowchart TB
    subgraph SERVER["⏳ 服务端"]
        S1["M1: ExitMainThread 全局副作用"]
        S2["M2: 主线程调用可能死锁"]
        S3["M3: 过期 session_id 未自动清"]
    end

    subgraph PROXY["⏳ 代理层"]
        P1["P1: Responses API 未实现"]
        P2["P2: llm_service 不支持多模态"]
        P3["P3: llm_service 不支持 SO"]
    end

    subgraph LTB["⏳ LTB"]
        L1["L1: 工具并发执行未实现"]
        L2["L2: 工具列表不支持动态刷新"]
        L3["L3: 无工具调用统计上报"]
        L4["L4: SO 与 tools 竞争未处理（P9-15）"]
    end

    subgraph GUI["⏳ GUI 层"]
        G1["P0-1: FormClose 直接 Shutdown"]
        G2["P1-5: 无取消按钮"]
        G3["P3: 后台线程 DoStatus 疑点"]
    end

    subgraph SO["⏳ Structured Output"]
        F1["F1: 复杂 schema（oneOf/allOf）"]
        F2["F2: 多轮工具 + SO 组合"]
        F3["F3: schema 缓存优化"]
        F4["F4: 更多预置模板"]
    end

    style SERVER fill:#FADBD8,stroke:#922B21,stroke-width:3px,color:#5A1A14
    style PROXY fill:#FDEBD0,stroke:#B7791F,stroke-width:3px,color:#7E5109
    style LTB fill:#F4ECF7,stroke:#5B2C6F,stroke-width:3px,color:#321640
    style GUI fill:#D6EAF8,stroke:#1F618D,stroke-width:3px,color:#0D2F52
    style SO fill:#FEF9E7,stroke:#B7950B,stroke-width:3px,color:#7E5109
```

### 20.2 建议优先级

| 优先级 | 项目 | 理由 |
|:------:|------|------|
| 🔴 P0 | GUI `FormClose` 顺序 | 关闭时可能崩溃 |
| 🟠 P1 | 加取消按钮 | 长回答无法中止 |
| 🟠 P1 | 中文端到端验证 | 未实测 |
| 🟠 P1 | Pascal 侧离线检查补丁 | `check_api` 误报 |
| 🟠 P2 | LTB 工具并发执行 | 单轮多工具串行效率低 |
| 🟠 P2 | LTB 工具列表动态刷新 | 目前需重启 LTB |
| 🟠 P2 | **SO 与 tools 组合处理（P9-15）** | **SO + 工具链目前只能分开调用** |
| 🟡 P3 | `llm_service` 多模态路径 | 架构方向，待定 |
| 🟡 P3 | **SO schema 缓存优化** | **同一 schema 多次调用可复用编译结果** |
| 🟡 P4 | 清理未使用导入 | 顺手清理 |
| 🟡 P4 | **更多预置 schema 模板** | **OCR / 关键点 / 表格等** |

---

## 二十一、结语

本次工作从**一个单会话 LLM 网关的缺陷修补**出发，历经**六个阶段**，最终演进为一套**四组件、多路径、全栈文档、全生态对齐、支持结构化输出**的完整解决方案：

- **服务端**：19 项缺陷清零，单 worker 串行化，持久多会话，双条件回收
- **代理层**：从 0 到 1 打通外部后端，8 次迭代解决实时性问题，支持 250+ 后端
- **工具桥（LTB）**：客户端零改动即可享受工具能力，与 `mcp_api_tool` 共存
- **协议**：字符串魔法 → 结构化 JSON → 无策略转发 → 能力矩阵 → 双路径
- **跨语言**：Python 与 Pascal 双端对齐，字节流全程贯通，协议三方一致
- **GUI**：new_session 实现，能力检查，温和失败处理，**结构化输出面板**
- **工具链**：`--help` 自适应，四 EXE 一键编译，依赖清单
- **文档**：全部改为高对比配色 + 同目录链接
- **v3 架构**：四大应用组件叙事、`llm_service` 降级、代码生成器双语言、250+ 兼容、多模态转发路径
- **Structured Output**：**代理层 passthrough + SDK 4 个方法 + GUI 面板 + 三层文档**，让模型输出 100% 可靠 JSON

**核心成果**：把一个"能跑"的 demo，变成了一个**能承受三开、能承受多会话、能承受跨语言、能对接 LM Studio 等主流后端、能自我描述能力、能让客户端零改动享受工具、能优雅降级、能通过代理转发多模态、能用一份声明生成两种语言工具提供者、能让模型输出 100% 可靠 JSON** 的工业级组件。

---

## 二十二、相关文档（同目录）

| 文档 | 说明 |
|------|------|
| [`LingoFuse_LLM_Ecosystem_User_Guide.md`](LingoFuse_LLM_Ecosystem_User_Guide.md) | 闭环架构与生态总览 |
| [`LingoFuse_LLM_Service_CLI_guide.md`](LingoFuse_LLM_Service_CLI_guide.md) | `llm_service.exe` 命令行手册 |
| [`LingoFuse_LLM_Proxy_CLI_Guide.md`](LingoFuse_LLM_Proxy_CLI_Guide.md) | `llm_proxy.exe` 命令行手册 |
| [`LingoFuse_LLM_Proxy_Tool_CLI_Guide.md`](LingoFuse_LLM_Proxy_Tool_CLI_Guide.md) | `llm_proxy_tool.exe`（LTB）命令行手册 |
| [`LingoFuse_LLM_Proxy_Compatibility_Guide.md`](LingoFuse_LLM_Proxy_Compatibility_Guide.md) | 支持的 250+ OpenAI 兼容后端清单 |
| [`LingoFuse_LLM_Pitfalls_For_AI.md`](LingoFuse_LLM_Pitfalls_For_AI.md) | 踩坑大全，症状-根因-正确做法（**含 P9 系列**） |
| [`llm_client_v3.md`](llm_client_v3.md) | Pascal 客户端 SDK 文档（**含 §16 Structured Output**） |

### 根目录相关文档

| 文档 | 说明 |
|------|------|
| [`../Structured_Output_Learning_Guide.md`](../Structured_Output_Learning_Guide.md) | **Structured Output 完整学习指南** |

> **归属提醒**：`llm_client.pas`、`llm_tool_frm.pas` 等 **Pascal GUI 客户端源码**属于 **LingoFuse 核心仓库**（[github.com/PassByYou888/LingoFuse](https://github.com/PassByYou888/LingoFuse)），不在本仓库中。
>
> **`llm_client_v3.pas`**、**`llm_tool_v3_frm.pas`**、**`llm_tool_v3.lpi`** 属于**本仓库**。

---

**文档完成**

*v7.0 在 v6.0 基础上新增阶段 F（Structured Output），包含 F1–F8 设计点、经验 12–15、SO 四层协同（代理层 + SDK + GUI + 文档）、SO 能力边界流程、SO 相关交付物清单、SO 相关遗留问题；更新数据统计（新增 SO 阶段）；重构结语涵盖六阶段完整叙事。所有图表使用 Mermaid 绘制，采用高对比配色方案。*
