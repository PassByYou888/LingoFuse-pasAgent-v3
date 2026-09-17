# QUICK_START_LLM_STACK.md

> **场景**：LM Studio 已在 `http://169.254.154.148:1234` 打开 Local Server，模型为 `nvidia-nemotron-3.5-lightning-30b-a3b@iq4_xs`。
> **目标**：用 pasAgent v3 工具链与这个后端交互，覆盖纯对话、工具调用、MCP 客户端、GUI 四条路径。
> **文档版本**：v1.0
> **最后更新**：2026-09-17

---

## 0. 前置约定

### 0.1 场景参数（全文复用）

| 项 | 值 |
|---|---|
| 后端地址 | `http://169.254.154.148:1234/v1` |
| 后端模型 ID | `nvidia-nemotron-3.5-lightning-30b-a3b@iq4_xs` |
| 是否 VLM | ❌ **不是**（不加 `--vision`） |
| 是否支持 tool_calls | ✅ 支持（Nemotron 系列原生支持 Function Calling） |
| 是否支持 Structured Output | ✅ 支持（LM Studio 0.3.0+ 即可） |

### 0.2 工作目录约定

下文用 `<DIST>` 表示你解压预编译包的目录：

- **Windows**：例如 `C:\Temp\temp2`
- **Linux**：例如 `/opt/lingofuse`

**所有 EXE / DLL 必须放在同一目录**，或在 `PATH` 中能找到 `LingoFuse64.dll` / `liblingofuse.so`。

### 0.3 先确认后端可用

**Windows（PowerShell）**：

```powershell
curl.exe http://169.254.154.148:1234/v1/models
```

**Linux（Shell）**：

```bash
curl http://169.254.154.148:1234/v1/models
```

返回的 `data[].id` 里**必须**能找到 `nvidia-nemotron-3.5-lightning-30b-a3b@iq4_xs`。如果没有，先修 LM Studio，再往下走。

### 0.4 三条铁律

1. **`llm_service` / `llm_proxy` / `llm_proxy_tool` 三者默认共用 `ipc:llm_service`**——**同一时刻只能跑一个**。要共存必须改 `--endpoint` + `--app-name`。
2. **模型 ID 含 `@`**——命令行里**必须用引号包起来**（PowerShell 单引号 `'...'`，Linux 单引号 `'...'` 也可以）。
3. **当前模型不是 VLM**——**不要传 `--vision`**，否则发图片会被拒绝。

---

## 1. 一分钟速览

| 我想干嘛 | 用哪个 | 要不要信标 | 要不要工具提供者 |
|---|---|:---:|:---:|
| **只跟模型聊天** | `llm_proxy` | ❌ | ❌ |
| **让 AI 调用我的 Pascal 工具**（推荐） | `llm_proxy_tool`（LTB） | ✅ | ✅ |
| **LM Studio 自己调工具（MCP）** | `mcp_api_tool` | ✅ | ✅ |
| **命令行测试** | `llm_test` | ❌ | ❌ |
| **Pascal GUI 客户端** | `llm_tool_v3` | ❌ | ❌ |

---

## 2. 路径 A：纯对话（最简）

**只要启动一个进程**：`llm_proxy`。

### 2.1 启动代理

**Windows（PowerShell）**：

```powershell
cd <DIST>
.\llm_proxy.exe `
  --backend-url http://169.254.154.148:1234/v1 `
  --backend-model "nvidia-nemotron-3.5-lightning-30b-a3b@iq4_xs" `
  --backend-key lm-studio `
  --log-level INFO
```

**Linux（Shell）**：

```bash
cd /opt/lingofuse
./llm_proxy \
  --backend-url http://169.254.154.148:1234/v1 \
  --backend-model "nvidia-nemotron-3.5-lightning-30b-a3b@iq4_xs" \
  --backend-key lm-studio \
  --log-level INFO
```

看到 `[INFO] LLM Proxy service 'LLM_Service' running on ipc:llm_service` 即成功。

### 2.2 用命令行客户端测试

**另开一个终端**：

**Windows（PowerShell）**：

```powershell
cd <DIST>
.\llm_test.exe
```

**Linux（Shell）**：

```bash
cd /opt/lingofuse
./llm_test
```

进入交互式 REPL，直接输入问题回车即可。输入 `/quit` 退出。

单次调用模式（跑一条就退）：

```powershell
.\llm_test.exe --content "用一句话介绍你自己"
```

```bash
./llm_test --content "用一句话介绍你自己"
```

### 2.3 用 Pascal GUI 测试

**Windows（PowerShell）**：

```powershell
.\llm_tool_v3.exe
```

**Linux（Shell，需 X11）**：

```bash
./llm_tool_v3
```

GUI 里：
1. `LLM参数` 页 → 端点填 `ipc:llm_service`，APP 填 `LLM_Service`
2. 点 `链接端点`
3. `输入` 页 → 写内容 → 点 `发送 generate 请求`

---

## 3. 路径 B：AI 调用 Pascal 工具（LTB，推荐）

**要启动三个进程**：信标 → 工具提供者 → LTB。

### 3.1 终端 1：信标（工具注册中心）

**Windows（PowerShell）**：

```powershell
cd <DIST>
.\pascal_agent_service.exe
```

**Linux（Shell）**：

```bash
cd /opt/lingofuse
./pascal_agent_service
```

看到 `[MAIN] Service is running. Type "exit" to quit.` 即成功。**保持运行**。

### 3.2 终端 2：工具提供者（示例算术工具）

**Windows（PowerShell）**：

```powershell
cd <DIST>
.\pascal_agent_api.exe
```

**Linux（Shell）**：

```bash
cd /opt/lingofuse
./pascal_agent_api
```

看到 `[MAIN] All tools registered.` 即成功。**保持运行**。

> 💡 **这就是你自己的工具放的地方**。把 `pascal_agent_api.lpr` 里的 `do_add` / `do_sub` / `do_mul` / `do_div` 换成你自己的函数，重新编译即可。

### 3.3 终端 3：LTB（服务端工具桥）

**Windows（PowerShell）**：

```powershell
cd <DIST>
.\llm_proxy_tool.exe `
  --backend-url http://169.254.154.148:1234/v1 `
  --backend-model "nvidia-nemotron-3.5-lightning-30b-a3b@iq4_xs" `
  --backend-key lm-studio `
  --mcp-reg-agent-app llm_proxy_agent `
  --mcp-tool-provider-app agent_main_app `
  --log-level DEBUG
```

**Linux（Shell）**：

```bash
cd /opt/lingofuse
./llm_proxy_tool \
  --backend-url http://169.254.154.148:1234/v1 \
  --backend-model "nvidia-nemotron-3.5-lightning-30b-a3b@iq4_xs" \
  --backend-key lm-studio \
  --mcp-reg-agent-app llm_proxy_agent \
  --mcp-tool-provider-app agent_main_app \
  --log-level DEBUG
```

**关键日志**（必须看到）：

```
[INFO] MCP middleware ready: 5 tool(s) cached
[INFO] LLM Tool Bridge service 'LLM_Service' running on ipc:llm_service
```

- `5 tool(s) cached` 里的数字应 > 0。若为 0，检查终端 1、终端 2 是否真的在跑。

### 3.4 终端 4：命令行测试

**Windows（PowerShell）**：

```powershell
.\llm_test.exe --content "5 加 7 等于几？请调用工具计算。"
```

**Linux（Shell）**：

```bash
./llm_test --content "5 加 7 等于几？请调用工具计算。"
```

**成功的表现**：LTB 的日志里出现

```
[DEBUG] Task xxx round 0: executing 1 of 1 tool call(s)
[DEBUG]   -> add({"a":5,"b":7})
[DEBUG]   <- {"result": 12}
```

而**客户端只看到最终答案**（如 `5 + 7 = 12`），完全不知道中间调用了工具。

---

## 4. 路径 A'：LM Studio 自己调工具（MCP 客户端）

**适用**：LM Studio **原生支持 MCP** 时。

### 4.1 启动四个进程

**终端 1、2**：信标 + 工具提供者（同 §3.1、§3.2）

**终端 3：MCP 网关（不是 LTB）**

**Windows（PowerShell）**：

```powershell
cd <DIST>
.\mcp_api_tool.exe `
  --transport http `
  --host 0.0.0.0 `
  --port 8000 `
  --endpoint ipc:agent `
  --reg-agent-app reg_agent `
  --tool-provider-app agent_main_app `
  --log-file .\mcp_api_tool.log
```

**Linux（Shell）**：

```bash
cd /opt/lingofuse
./mcp_api_tool \
  --transport http \
  --host 0.0.0.0 \
  --port 8000 \
  --endpoint ipc:agent \
  --reg-agent-app reg_agent \
  --tool-provider-app agent_main_app \
  --log-file ./mcp_api_tool.log
```

> ⚠️ **`--reg-agent-app reg_agent` 必须与 LTB 的 `llm_proxy_agent` 不同**——否则二者冲突。

### 4.2 让 LM Studio 连上去

在 LM Studio 的 MCP 设置里加：

```json
{
  "mcpServers": {
    "pascal-backend": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

然后**重启 LM Studio**。之后在 LM Studio 里提问，AI 会**自己决定**调哪个工具。

### 4.3 生成完整配置

不想手写配置？让 `mcp_api_tool` 帮你生成：

**Windows（PowerShell）**：

```powershell
.\mcp_api_tool.exe --generate-configs --output-dir .\mcp_configs
```

**Linux（Shell）**：

```bash
./mcp_api_tool --generate-configs --output-dir ./mcp_configs
```

生成的 `mcp_configs/` 里有 LM Studio / Claude / Continue.dev 等多份现成配置。

---

## 5. 路径 B'：LTB 与 MCP 网关共存

**两个都要跑**（LM Studio 走 MCP，Pascal GUI 走 LTB），**共享同一信标**：

| 终端 | 命令 | `reg_agent` 名 |
|---|---|---|
| 1 | `pascal_agent_service.exe` | — |
| 2 | `pascal_agent_api.exe` | — |
| 3 | `mcp_api_tool.exe --transport http --port 8000` | `reg_agent` |
| 4 | `llm_proxy_tool.exe --backend-url ... --backend-model "..."` | `llm_proxy_agent` |

**关键**：二者 `reg_agent` 名不同 → 可共存；共享 `ipc:agent` 信标。

---

## 6. 路径 C：本地推理（`llm_service`）——**本场景不用**

> ⚠️ `llm_service` 是**本地加载 GGUF 模型**的服务。你的场景是**用 LM Studio**，所以**不需要它**。
>
> 只有在**完全离线**、**不想用 LM Studio** 时才用 `llm_service`。而且它**不支持多模态**，也不支持 Structured Output。

仅供了解，命令如下（**不要跟你上面的 LM Studio 场景混用**）：

```powershell
.\llm_service.exe --model-path .\NVIDIA-Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-IQ4_XS.gguf
```

```bash
./llm_service --model-path ./NVIDIA-Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-IQ4_XS.gguf
```

---

## 7. 辅助工具速查

### 7.1 `HealthCheck` —— 环境体检

**Windows**：`.\HealthCheck.exe`
**Linux**：`./HealthCheck`

弹窗显示 LingoFuse 网络是否正常。首次部署时用一次。

### 7.2 `mcp_api_proxy` —— MCP stdio 调试代理

**只在你用 stdio 传输、且需要抓包时才用**：

**Windows（PowerShell）**：

```powershell
.\mcp_api_proxy.exe .\mcp_api_tool.exe --transport stdio
```

**Linux（Shell）**：

```bash
./mcp_api_proxy ./mcp_api_tool --transport stdio
```

每一条 stdin/stdout 都会落到同目录的 `proxy.log`。

### 7.3 `bridge` —— HTTP 桥接（给浏览器 / Node 用）

**Windows（PowerShell）**：

```powershell
.\bridge.exe --endpoint ipc:llm_service --app LLM_Service --port 8081
```

**Linux（Shell）**：

```bash
./bridge --endpoint ipc:llm_service --app LLM_Service --port 8081
```

然后：

```bash
curl -X POST http://127.0.0.1:8081/LLM_Service/generate -d '{"content":"你好"}'
```

### 7.4 `code_decl_to_mcp` —— 代码生成器（GUI）

**Windows**：`.\code_decl_to_mcp.exe`
**Linux**：`./code_decl_to_mcp`

粘贴 Pascal / C 声明 → 走 5 个 Tab → 生成 `.pas` 或 `.py` 工具提供者。**不依赖上面的信标 / LM Studio**，独立使用。

---

## 8. 端口 / 端点占用速查

| 服务 | 默认端点 | 默认 App 名 |
|---|---|---|
| `llm_service` | `ipc:llm_service` | `LLM_Service` |
| `llm_proxy` | `ipc:llm_service` | `LLM_Service` |
| `llm_proxy_tool` | `ipc:llm_service` | `LLM_Service` |
| `pascal_agent_service`（信标） | `ipc:agent` + `0.0.0.0:9897` | `agent_main_app` |
| `pascal_agent_api`（工具示例） | `ipc:agent` | `my_calculator` |
| `mcp_api_tool`（HTTP） | `0.0.0.0:8000` | — |
| `bridge`（HTTP） | `0.0.0.0:8081` | — |

**同时只能跑一个 `llm_*`**（默认共享 `ipc:llm_service`）。要共存改 `--endpoint` + `--app-name`：

```powershell
.\llm_proxy_tool.exe --endpoint ipc:llm_proxy_tool --app-name LLM_Proxy_Tool ...
```

```bash
./llm_proxy_tool --endpoint ipc:llm_proxy_tool --app-name LLM_Proxy_Tool ...
```

---

## 9. 排查速查

| 现象 | 原因 | 解决 |
|---|---|---|
| `[FATAL] Could not auto-discover backend model` | `--backend-model` 空 + 后端不可达 | 显式传 `--backend-model "nvidia-nemotron-3.5-lightning-30b-a3b@iq4_xs"` |
| `MCP middleware ready: 0 tool(s) cached` | 信标/工具提供者未起 | 检查终端 1、终端 2 |
| `Queue "llm_service0" is already occupied` | 已有 `llm_*` 在跑 | 只留一个，或改 `--endpoint` |
| 图片发过去后端说"没看到" | 当前模型不是 VLM | 换 VLM 模型，或不用图片 |
| `set_system_message` 报 `unsupported` | 你连的是 proxy | 用 `CreateSession(system_message=...)` |
| LTB 日志刷屏 `no found app` | `client_name` 不对 | 这是客户端问题，用 `llm_test` 或 `llm_tool_v3` 就正常 |
| LM Studio 拒绝连接 | 防火墙 | 放行 `1234` 端口 |
| 后端地址 `169.254.x.x` 连不上 | link-local 地址 | 确认两台机器在同一网段，或改用 `127.0.0.1` |

---

## 10. 最小可用启动清单（照抄即可）

### 场景：LTB + LM Studio（推荐）

**Windows（PowerShell，开 4 个窗口）**：

```powershell
# 窗口 1
cd <DIST>; .\pascal_agent_service.exe

# 窗口 2
cd <DIST>; .\pascal_agent_api.exe

# 窗口 3
cd <DIST>; .\llm_proxy_tool.exe `
  --backend-url http://169.254.154.148:1234/v1 `
  --backend-model "nvidia-nemotron-3.5-lightning-30b-a3b@iq4_xs" `
  --log-level DEBUG

# 窗口 4
cd <DIST>; .\llm_test.exe
```

**Linux（Shell，开 4 个终端）**：

```bash
# 终端 1
cd /opt/lingofuse && ./pascal_agent_service

# 终端 2
cd /opt/lingofuse && ./pascal_agent_api

# 终端 3
cd /opt/lingofuse && ./llm_proxy_tool \
  --backend-url http://169.254.154.148:1234/v1 \
  --backend-model "nvidia-nemotron-3.5-lightning-30b-a3b@iq4_xs" \
  --log-level DEBUG

# 终端 4
cd /opt/lingofuse && ./llm_test
```

**然后**：在窗口 4 里输入 `5 加 7 等于几？` → 回车。

**成功标志**：
- 窗口 4 看到 `5 + 7 = 12`
- 窗口 3 看到 `-> add({"a":5,"b":7})` 和 `<- {"result": 12}`

---

## 11. 相关文档

| 文档 | 说明 |
|---|---|
| `readme.md` | 项目总览与四大核心应用组件 |
| `Pascal_Integration_Guide.md` | Pascal 开发者切入指南 |
| `src/LingoFuse_LLM_Proxy_Tool_CLI_Guide.md` | LTB 完整参数手册 |
| `src/LingoFuse_LLM_Proxy_CLI_Guide.md` | `llm_proxy` 完整参数手册 |
| `src/LingoFuse_LLM_Pitfalls_For_AI.md` | 踩坑大全（P0–P9） |
| `src/llm_client_v3.md` | Pascal 客户端 SDK 文档（含 §16 Structured Output） |
| `code_generate_mcp.md` | 代码生成器使用手册 |
| `Build_Guide.md` | 编译指南 |

---

**文档版本**：v1.0
**维护者**：LingoFuse-pasAgent 团队
**反馈**：问题提 Issue，急事加 Q（600585）
