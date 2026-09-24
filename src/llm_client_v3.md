# llm_client_v3.md

> **用途**：让 AI 能在不读 `llm_client_v3.pas` 源码的情况下，独立完成以下任务：
> - 写出正确的调用代码
> - 定位并修复 bug
> - 添加新 API
> - 修改现有 API
> - 判断改动影响范围
> - **构造 Structured Output（结构化输出）请求，尤其是检测器方框标注**
> - **组合图片附件 + JSON Schema 实现"一步到位的检测器"**
>
> **使用方式**：
> - 要**写代码** → §1 + §2 + §3
> - 要**查 bug** → §4（错误消息原文索引）
> - 要**升级** → §5（模板）
> - 要**深入理解** → §6 + §7
> - 要**做结构化输出/检测器** → **§16 Structured Output 完整指南**（重点）
> - 遇到 §8 的场景 → 停止，回查源码
>
> **可信度标记**：`[源码]` = 逐行核对过 `.pas`；`[协议]` = 从服务端契约推断；`[教训]` = 来自实际踩坑；`[未核实]` = 需回查源码。
>
> **文档版本**：v2.4（Structured Output 组合扩展版 · 目录对齐版）
> **最后更新**：2026-09-24
> **SDK 源码位置**：本仓库 `src\llm_client_v3.pas`（v3.10）
> **GUI 演示**：本仓库 `src\llm_tool_v3.lpi`（v3.4）
> **相关文档**：
> - [`../Pascal_Integration_Guide.md`](../Pascal_Integration_Guide.md) — Pascal 开发者切入指南
> - [`LingoFuse_LLM_Ecosystem_User_Guide.md`](LingoFuse_LLM_Ecosystem_User_Guide.md) — 生态总览
> - [`LingoFuse_Pascal_Complete_Guide.md`](LingoFuse_Pascal_Complete_Guide.md) — Pascal 核心层完整指南（含踩坑知识库）
> - [`llm_client_v3_Structured_Output_Learning_Guide.md`](llm_client_v3_Structured_Output_Learning_Guide.md) — Structured Output 完整学习指南

---

## ⚠️ 阅读前必读：能力边界

**在阅读本文之前，请先理解以下四条关键事实**：

| # | 事实 | 说明 |
|:-:|------|------|
| 1 | **SDK 同时兼容 Delphi 7+ 和 FPC 3.0+** | 通过条件编译指令统一两端 |
| 2 | **SDK 支持三种服务端** | `llm_service` / `llm_proxy` / `llm_proxy_tool`（LTB）——通过 `ServerKind` 与能力矩阵区分 |
| 3 | **`GenerateWithImageFile` 等图片 API 只对代理服务端有效** | `llm_service` 是纯文本服务端，**收到图片附件会拒绝**（返回 `code: -1`）。图片要经 `llm_proxy` / LTB 转发到 VLM 后端。 |
| 4 | **Structured Output 只对代理服务端有效** | 所有 Structured Output 相关方法（`GenerateStructured` / `GenerateWithJsonSchema` / `GenerateWithImageFileAndSchema` / `GenerateWithAttachmentsAndSchema`）依赖代理层把 `options.response_format` 原样转发给后端。`llm_service`（本地 llama.cpp）在当前版本**不支持** `response_format`。 |

> **关键区分**：
> - **客户端 API 无差异**——SDK 本身对三种服务端使用**相同的 API**。
> - **服务端能力有差异**——`set_system_message` 只有 `llm_service` 支持；工具只有 LTB 支持；多模态转发和 Structured Output 转发只有 `llm_proxy` / LTB 支持。
> - **客户端必须做能力发现**——通过 `LLMSupported` / `HasVision` / `HasAttachments` 等接口查询服务端能力，**不要硬编码假设**。

---

## §1 API 速查表

### 1.1 构造与生命周期

| 方法 | 签名 | 副作用 | 线程 | 失败返回 |
|------|------|--------|------|---------|
| `Create` | `(AServerApp, AEndpoint: string; ATimeout: integer = 10000)` | 分配 `FCapabilities=nil`、`FLastException` | 调用者 | — |
| `Destroy` | 无参 | 调 `Disconnect`，释放 `FCapabilities` / `FLastException` | 调用者 | — |
| `Connect` | `(out ErrorMsg: string): boolean` | 见 §1.6 副作用表 | 调用者 | `False` + `ErrorMsg` |
| `Disconnect` | 无参 | 见 §1.6 副作用表 | 调用者 | — |

> **`AServerApp` 的典型值**：
> - `'LLM_Service'`（**默认，三种服务端共用**——大小写严格）
> - `'LLM_Proxy'`（`llm_proxy` 换端点时使用）
> - `'LLM_Proxy_Tool'`（LTB 换端点时使用）
>
> **`AEndpoint` 的典型值**：
> - `'ipc:llm_service'`（**默认**）
> - `'ipc:llm_proxy'` / `'ipc:llm_proxy_tool'`（换端点时使用）
> - `'0.0.0.0:9898'`（跨机 TCP）

### 1.2 会话管理

| 方法 | 参数 | 返回值 | 副作用 | 请求字段 |
|------|------|--------|--------|---------|
| `CreateSession` | `(out ASessionId, AError: string)` | boolean | **更新 `FCurrentSessionId`** | `{client_name}` |
| `CreateSession` | `(const ASystemMessage: string; out ASessionId, AError: string)` | boolean | **更新 `FCurrentSessionId`** | `{client_name, system_message?}` |
| `CloseSession` | `(const ASessionId: string; ACancelRunning: boolean; out AError: string)` | boolean | 若 `FCurrentSessionId = ASessionId` 则清空 | `{session_id, cancel_running}` |
| `CancelSession` | `(const ASessionId: string; out AError: string)` | boolean | 无 | `{session_id}` |
| `ListSessions` | `(out ASessionsJson, AError: string)` | boolean | 无 | `{client_name}` |

**契约** [源码]：
- `CloseSession` / `CancelSession` 传入空 `ASessionId` → 直接失败（不发网络）。
- `CreateSession` 成功但响应无 `session_id` → 失败并返回 `'Server did not return session_id'`。
- `ListSessions` 成功时 `ASessionsJson` 是**原始响应 JSON**（未再解析），调用者需自己 `TZ_JsonObject.ParseText`。

### 1.3 生成（9 个变体）

| 方法 | 参数 | 副作用 | 附加字段 |
|------|------|--------|---------|
| `Generate` | `(AContent, APrompt: string; var ASessionId: string; out AError: string)` | **更新 `FCurrentSessionId`** | `{content, prompt, session_id?}` 或 `{content, prompt, client_name}` |
| `GenerateWithAttachments` | `(+ ATexts, AImages; var ASessionId; out AError)` | 同上 | 附加 `attachments:[...]` |
| `GenerateWithTextFile` | `(AContent, APrompt, AFilePath: string; var ASessionId; out AError)` | 同上 | 读文件→构造文本附件 |
| `GenerateWithImageFile` | `(AContent, APrompt, AFilePath: string; var ASessionId; out AError)` | 同上 | 读文件→base64→构造图片附件 |
| `GenerateCurrent` | `(AContent, APrompt: string; out AError: string)` | **不改变 `FCurrentSessionId`** | 用 `FCurrentSessionId`（可能是空） |
| **`GenerateStructured`** | `(AContent, APrompt, AResponseFormatJson: string; var ASessionId; out AError)` | **更新 `FCurrentSessionId`** | 附加 `options.response_format`（**原始 JSON 字符串**） |
| **`GenerateWithJsonSchema`** | `(AContent, APrompt, ASchemaName, ASchemaJson: string; AStrict: boolean; var ASessionId; out AError)` | **更新 `FCurrentSessionId`** | 客户端自动组装外层 `{"type":"json_schema",...}` |
| **`GenerateWithImageFileAndSchema`**（v3.10） | `(AContent, APrompt, AFilePath, ASchemaName, ASchemaJson: string; AStrict: boolean; var ASessionId; out AError)` | **更新 `FCurrentSessionId`** | **图片文件 + JSON Schema 一步到位**（检测器推荐入口） |
| **`GenerateWithAttachmentsAndSchema`**（v3.10） | `(AContent, APrompt: string; ATexts, AImages; ASchemaName, ASchemaJson: string; AStrict: boolean; var ASessionId; out AError)` | **更新 `FCurrentSessionId`** | **附件数组 + JSON Schema**（完全控制） |

**`ASessionId` 优先级** [源码]：

```text
如果 ASessionId <> '' → 用它（并写回 FCurrentSessionId）
否则如果 FCurrentSessionId <> '' → 用它（续接）
否则 → 用 client_name := FClientName
```

**关键陷阱** [教训]：
- `Generate` 传 `ASessionId = ''` 时，**不会新开会话**——只要 `FCurrentSessionId` 非空就会续接。
- 想强制新会话：先 `Client.CurrentSessionId := ''`（有写属性）或 `CloseSession`。

**`GenerateWithImageFile` 的服务端约束** [协议]：
- **`llm_service` 会拒绝**——返回 `code: -1`，错误信息提示"Image attachments are not supported by llm_service in this revision"。
- **`llm_proxy` / LTB 只在传了 `--vision` 时才接受**——否则同样返回 `code: -1`。
- **客户端必须做能力发现**：调用前检查 `LLMSupported('attachments')` 和 `HasVision`。

**Structured Output 系列方法的服务端约束** [协议]：
- **`llm_service` 不支持**——它不转发 `response_format` 到本地推理路径。
- **`llm_proxy` / LTB 支持**——它们把 `options.response_format` 原样转发给后端（LM Studio / Ollama / vLLM 等）。
- **后端必须支持 Structured Outputs**——例如 LM Studio 加载 Qwen2.5-VL、Nemotron Omni 等支持 JSON Schema 的模型。
- **详见 §16**。

### 1.4 服务端全局设置

| 方法 | 参数 | 请求 | 副作用 | 备注 |
|------|------|------|--------|------|
| `SetSystemMessage` | `(const AMessage: string; out AError: string)` | `{content}` | 无 | **仅 `service` 支持**；能力已知不支持时**本地短路** |
| `Health` | `(out AHealthJson, AError: string)` | `{}` | 无 | `AHealthJson` 是原始 JSON 响应 |

### 1.5 能力发现

| 方法 | 语义 | 依据字段 |
|------|------|---------|
| `GetAPICapabilities` | 拉取能力矩阵并缓存 | — |
| `HasCapabilityInfo` | `FCapabilities <> nil` | — |
| `LLMSupported(name)` | 指定 API 是否被标记为 1 | `FCapabilities.i[name] = 1` |
| `IsToolBridge` | `tools=1 and tool_calls=1` | `[协议]` |
| `HasVision` | `vision=1` | `[协议]` |
| `HasAttachments` | `attachments=1` | `[协议]` |

**`LLMSupported` 契约** [源码]：
- `FCapabilities = nil` → False（**保守**）
- 键存在且值为 1 → True
- 键存在且值为 0 → False
- 键不存在 → False

**`HasVision` 的实际含义** [协议]：

⚠️ **`vision=0` 在所有 LingoFuse LLM 服务端上都是固定的**——因为服务端**自身不做视觉处理**，它只是转发者。

- `HasVision` 返回 `True` 的**唯一情况**：服务端**自身实现**了视觉处理（当前 v3 中没有任何服务端满足）。
- `HasVision` 返回 `False` 时**不代表**链路不支持图片——只代表**服务端不做视觉处理**。
- **图片能否被理解由后端决定**——客户端应通过 `HasAttachments` 判断"服务端是否接受附件字段"，再通过实际请求验证后端是否支持。

> **实践建议**：客户端判断"能否发图片"的逻辑应为：
> ```pascal
> if LLM.HasAttachments and LLM.IsToolBridge then
>   // LTB：转发图片到后端（需服务端传了 --vision）
> else if LLM.HasAttachments then
>   // llm_proxy：转发图片到后端（需服务端传了 --vision）
> else
>   // 服务端完全不接受附件
> ```
>
> **不要**仅凭 `HasVision` 判断"能否发图片"——它在本版本永远是 False。

### 1.6 `Connect` / `Disconnect` 的副作用清单

**`Connect` 成功时**：

| 字段 | 新值 |
|------|------|
| `FPrepared` | `True` |
| `FClientName` | `LF_Generate_AppNameEx()` 返回值 |
| `FApp` | `LF_CreateAppEx(...)` 返回值（非 nil） |
| `FConnected` | `True` |
| `FCapabilities` | 能力矩阵深拷贝（探测成功时） |
| `FCapabilitiesRawJson` | 原始响应 JSON 文本 |
| `FServerKind` | `'service'` / `'proxy'` / `''` |

**`Connect` 失败时**：
- 通过 `CleanupPartialConnect` 重置 `FPrepared` / `FConnected` / `FClientName` / `FCurrentSessionId` / `FCapabilities` / `FCapabilitiesRawJson` / `FServerKind`。

**`Disconnect` 时**：同失败清理，且 `FApp = nil`。

### 1.7 属性

| 属性 | 类型 | 读/写 | 说明 |
|------|------|-------|------|
| `OnChunk` / `OnThink` / `OnFinish` / `OnError` / `OnClosed` | 事件 | 读/写 | 事件回调（**通知线程执行**） |
| `CurrentSessionId` | `string` | **读/写** | 可写（用于强制新会话） |
| `ClientName` | `string` | 读 | `LF_Generate_AppNameEx` 结果 |
| `Connected` | `boolean` | 读 | `FConnected` |
| `ServerKind` | `string` | 读 | `'service'` / `'proxy'` / `''` |
| `CapabilitiesRawJson` | `TZ_JsonString` | 读 | **不是 `string`**，取字符串用 `.Text` |
| `LastException` | `string` | 读 | 从 `FLastException.V` 取 |

---

## §2 类型与常量

### 2.1 事件类型

```pascal
TLLMChunkEvent  = procedure(const SessionId, Text: string) of object;
TLLMThinkEvent  = procedure(const SessionId, Text: string) of object;
TLLMFinishEvent = procedure(const SessionId, Reason: string) of object;
TLLMErrorEvent  = procedure(const SessionId, ErrorMsg: string) of object;
TLLMClosedEvent = procedure(const SessionId, Reason: string) of object;
```

**契约** [源码]：全是 `of object`（不是 `reference to`）；参数是 `string`（不是 `TZ_JsonString`）；**在 LingoFuse 通知线程执行**。

### 2.2 附件记录

```pascal
TLLMTextAttachment = record
  Name: TZ_JsonString;
  Mime: TZ_JsonString;
  Text: TZ_JsonString;
end;

TLLMImageAttachment = record
  Name: TZ_JsonString;
  Mime: TZ_JsonString;
  DataB64: TZ_JsonString;
end;

TLLMTextAttachmentArray = array of TLLMTextAttachment;
TLLMImageAttachmentArray = array of TLLMImageAttachment;
```

**为什么用 `TZ_JsonString`** [教训]：动态数组 + record + `string` 字段的 finalize 在某些编译器配置下不可靠。`TZ_JsonString`（`TPascalString` / `TUPascalString`）的内部缓冲是编译器明确会 finalize 的托管类型。

**赋值语法**：

```pascal
att.Name.Text := 'file.txt';        // 推荐（隐式走编译器转换）
att.Name.Bytes := TEncoding.UTF8.GetBytes('file.txt');  // 等价
```

### 2.3 常量

| 常量 | 值 | 说明 |
|------|-----|------|
| `API_NAME_STREAM` | `'llm_stream'` | 通知 API 名 |
| `API_NAME_GENERATE` | `'generate'` | |
| `API_NAME_CREATE_SESSION` | `'create_session'` | |
| `API_NAME_CLOSE_SESSION` | `'close_session'` | |
| `API_NAME_CANCEL_SESSION` | `'cancel_session'` | |
| `API_NAME_LIST_SESSIONS` | `'list_sessions'` | |
| `API_NAME_SET_SYSTEM_MESSAGE` | `'set_system_message'` | |
| `API_NAME_GET_CAPABILITIES` | `'get_api_capabilities'` | |
| `API_NAME_HEALTH` | `'health'` | |
| `ATTACHMENT_MAX_TEXT_BYTES_PER_FILE` | `262144` (256 KB) | |
| `ATTACHMENT_MAX_TEXT_BYTES_TOTAL` | `524288` (512 KB) | |
| `ATTACHMENT_MAX_IMAGE_B64_PER_FILE` | `8388608` (8 MB) | |
| `ATTACHMENT_MAX_IMAGE_B64_TOTAL` | `16777216` (16 MB) | |
| `ATTACHMENT_MAX_NAME_LEN` | `256` | 超出**截断不报错** |
| `ATTACHMENT_DEFAULT_NAME` | `'unnamed'` | |
| `ATTACHMENT_DEFAULT_TEXT_MIME` | `'text/plain'` | |
| `ATTACHMENT_DEFAULT_IMAGE_MIME` | `'image/png'` | |
| `LOG_PREFIX_LLM_CLIENT` | `'[llm_client] '` | `LogInfo` 前缀 |

### 2.4 内部字段（用于调试）

| 字段 | 类型 | 语义 |
|------|------|------|
| `FApp` | `TAppHnd` | LingoFuse 应用句柄 |
| `FServerApp` | `string` | 远端应用名 |
| `FEndpoint` | `string` | LingoFuse 端点 |
| `FTimeout` | `integer` | 每次 `LF_CallEx` 超时（ms） |
| `FConnected` | `boolean` | 是否成功 `Connect` |
| `FPrepared` | `boolean` | `LF_PrepareDone` 是否成功 |
| `FCurrentSessionId` | `string` | 隐式会话 ID |
| `FClientName` | `string` | 唯一客户端名 |
| `FCapabilities` | `TZ_JsonObject` | 能力矩阵深拷贝 |
| `FCapabilitiesRawJson` | `TZ_JsonString` | 原始响应 |
| `FServerKind` | `string` | `'service'` / `'proxy'` / `''` |
| `FLastException` | `TAtomString` | 通知线程写入的异常 |

---

## §3 最小可运行示例

### 3.1 最简：连接 + 生成 + 收 chunk

```pascal
uses
  Classes, SysUtils,
  lingofuse_import, llm_client_v3;

type
  TForm1 = class(TForm)
    Memo1: TMemo;
    procedure FormCreate(Sender: TObject);
    procedure FormClose(Sender: TObject; var Action: TCloseAction);
  private
    FClient: TLLMClient;
    procedure OnLLMChunk(const SessionId, Text: string);
    procedure OnLLMFinish(const SessionId, Reason: string);
    procedure OnLLMError(const SessionId, ErrorMsg: string);
  end;

procedure TForm1.FormCreate(Sender: TObject);
var
  E: string;
begin
  // ⚠️ 应用名必须是 'LLM_Service'（大写 S），端点必须是 'ipc:llm_service'
  FClient := TLLMClient.Create('LLM_Service', 'ipc:llm_service', 10000);

  FClient.OnChunk  := OnLLMChunk;
  FClient.OnFinish := OnLLMFinish;
  FClient.OnError  := OnLLMError;

  if not FClient.Connect(E) then
  begin
    ShowMessage('Connect failed: ' + E);
    Exit;
  end;

  // 发一条生成请求
  if not FClient.Generate('print("hello")', '解释这段代码', '', E) then
    ShowMessage('Generate failed: ' + E);
end;

// ⚠️ 此回调运行在 LingoFuse 通知线程，不能直接操作 UI！
procedure TForm1.OnLLMChunk(const SessionId, Text: string);
begin
  TThread.Queue(nil,
    procedure
    begin
      Memo1.Lines.Add(Text);
    end);
end;

procedure TForm1.OnLLMFinish(const SessionId, Reason: string);
begin
  TThread.Queue(nil,
    procedure
    begin
      Memo1.Lines.Add('--- finish: ' + Reason + ' ---');
    end);
end;

procedure TForm1.OnLLMError(const SessionId, ErrorMsg: string);
begin
  TThread.Queue(nil,
    procedure
    begin
      Memo1.Lines.Add('[ERROR] ' + ErrorMsg);
    end);
end;

procedure TForm1.FormClose(Sender: TObject; var Action: TCloseAction);
begin
  FClient.Free;
  LF_Shutdown();   // ⚠️ 由宿主程序负责（llm_client_v3 不调）
end;
```

**关键点**：
1. **回调必须 marshalling 到主线程**（`TThread.Queue`）。
2. **`LF_Shutdown` 由宿主程序调用**（`llm_client_v3` 只在 `Disconnect` 里 `LF_FreeApp` + `LF_ExitMainThread`）。
3. **`Generate` 第二参数是 prompt**，第三参数 `''` 表示"用当前会话或 client_name"。
4. **构造参数必须精确**——`'LLM_Service'`（大写 S）+ `'ipc:llm_service'`（不是 `'ipc:llm'`）。

### 3.2 带附件的生成（**仅对代理服务端有效**）

> ⚠️ **`llm_service` 不支持图片附件**。以下示例**仅对 `llm_proxy` / `llm_proxy_tool` 有效**。
>
> **调用前应做能力发现**：
> ```pascal
> if not FClient.LLMSupported('attachments') then
> begin
>   ShowMessage('当前服务端不接受附件');
>   Exit;
> end;
> ```

```pascal
var
  Texts: TLLMTextAttachmentArray;
  Images: TLLMImageAttachmentArray;
  E: string;
begin
  SetLength(Texts, 1);
  Texts[0].Name.Text := 'config.txt';
  Texts[0].Mime.Text := 'text/plain';
  Texts[0].Text.Text := 'key=value';

  SetLength(Images, 1);
  Images[0].Name.Text := 'chart.png';
  Images[0].Mime.Text := 'image/png';
  Images[0].DataB64.Text := SomeBase64;

  try
    FClient.GenerateWithAttachments('分析这些附件', '总结要点', Texts, Images, S, E);
  finally
    // ⚠️ 必须显式释放，不依赖编译器 finalize
    TLLMClient.ClearTextAttachments(Texts);
    TLLMClient.ClearImageAttachments(Images);
  end;
end;
```

### 3.3 显式会话管理

```pascal
var
  S, E: string;
begin
  // 1. 创建会话（带自定义系统消息）
  if not FClient.CreateSession('You are a Python expert.', S, E) then
  begin
    ShowMessage('CreateSession: ' + E);
    Exit;
  end;

  // 2. 用同一会话发多次请求
  FClient.Generate('Q1', '', S, E);   // S 被回写为同一会话
  FClient.Generate('Q2', '', S, E);   // 续接

  // 3. 关闭会话
  FClient.CloseSession(S, False, E);
end;
```

**关键点**：`CreateSession` 后，`FCurrentSessionId` 被更新，后续 `Generate` 传空字符串会自动续接。

### 3.4 能力发现

```pascal
var
  CapJson: TZ_JsonString;
  E: string;
begin
  if FClient.GetAPICapabilities(CapJson, E) then
  begin
    // 服务端种类
    DoStatus('Server kind: ' + FClient.ServerKind);

    // 检查具体能力
    if FClient.LLMSupported('set_system_message') then
      DoStatus('✅ 支持 set_system_message')
    else
      DoStatus('❌ 不支持 set_system_message（用 new_session 路径）');

    if FClient.LLMSupported('attachments') then
      DoStatus('✅ 接受附件字段')
    else
      DoStatus('❌ 不接受附件');

    if FClient.IsToolBridge then
      DoStatus('✅ 是 LTB，支持服务端工具执行')
    else
      DoStatus('❌ 不是 LTB，无服务端工具能力');

    // 注意：HasVision 在当前 v3 中永远返回 False
    // （服务端自身不做视觉处理；图片由后端决定）
  end
  else
    DoStatus('获取能力矩阵失败：' + E);
end;
```

**关键点**：
- **调用前必须做能力发现**——尤其在使用 `SetSystemMessage` / 附件 / 工具相关 API 前。
- **`HasVision` 不可用于判断"能否发图片"**——它在本版本永远是 False。
- **用 `HasAttachments` + 实际请求验证**判断图片路径是否可用。

### 3.5 一步到位的检测器（v3.10 推荐）

```pascal
var
  SchemaBody: string;
  S, E: string;
begin
  SchemaBody :=
    '{"type":"object","properties":{' +
    '"detections":{"type":"array","items":{' +
    '"type":"object","properties":{' +
    '"label":{"type":"string"},' +
    '"bbox":{"type":"array","items":{"type":"number","minimum":0,"maximum":1},' +
    '"minItems":4,"maxItems":4},' +
    '"confidence":{"type":"number","minimum":0,"maximum":1}},' +
    '"required":["label","bbox","confidence"]}}},' +
    '"required":["detections"]}';

  // ⚠️ 连接对象必须是 llm_proxy / llm_proxy_tool（含 --vision）
  if not FClient.GenerateWithImageFileAndSchema(
    '检测图片中的所有目标。返回归一化坐标（0~1）。',
    '',
    'test.png',
    'object_detection',
    SchemaBody,
    True,          // strict
    S, E) then
    ShowMessage('检测失败: ' + E);
end;
```

**这是 v3.10 新增的核心能力**：图片附件 + JSON Schema 一次调用完成，无需手动组合。详细说明见 §16。

---

## §4 错误消息原文索引

> **用途**：AI 拿到运行时错误文本，能**直接查表**定位根因和修复方案。
> 表格按**错误消息原文**排序，便于 grep。

### 4.1 连接相关

| 错误消息原文 | 抛出位置 | 根因 | 修复 |
|--------------|----------|------|------|
| `LF_PrepareClientEx failed (address invalid or already in use)` | `Connect` | `LF_PrepareClientEx` 返回 -1 | 检查 `FEndpoint` 格式；或开 `Overlap_Connection` |
| `LF_PrepareDone failed (network initialization error)` | `Connect` | `LF_PrepareDone() <> 1` | 检查 LingoFuse 库加载、端口占用 |
| `LF_Generate_AppNameEx returned an empty string` | `Connect` | 生成名失败 | 通常因 `LF_PrepareDone` 未真正启动 |
| `LF_CreateAppEx returned nil` | `Connect` | 分配失败 | 内存不足或 App 名非法 |
| `Failed to register notify callback for "llm_stream"` | `Connect` | `LF_RegisterNotifyEx` 返回非 1 | 检查 API 名是否已存在（同 App 重复注册） |
| `LF_BindApp returned 0 - no free client available` | `Connect` | 所有客户端已被占用，或主线程未启动 | 开 `Overlap_Connection=True`，或换物理地址 |
| `Unexpected exception in Connect: ...` | `Connect` | `try...except` 捕获的意外异常 | 看后缀的具体异常消息 |

> **相关原理**：`LF_PrepareClientEx` 的地址去重行为、`LF_BindApp` 的绑定规则，见 [`LingoFuse_Pascal_Complete_Guide.md`](LingoFuse_Pascal_Complete_Guide.md) 中 `LF-NET-001` / `LF-APP-005` / `LF-APP-006`。

### 4.2 通用调用相关

| 错误消息原文 | 抛出位置 | 根因 | 修复 |
|--------------|----------|------|------|
| `Not connected to LingoFuse service` | `CallAPI` / `Generate` / ... | `FConnected = False` | 先调 `Connect` 并检查返回值 |
| `LF_CreateDataEx returned nil for API "X"` | `CallAPI` | 分配失败 | 内存不足或 API 名非法 |
| `Failed to write request bytes for API "X"` | `CallAPI` | `LF_WriteStringBytes` 失败 | 罕见，通常是 handle 无效 |
| `LF_CallEx returned nil for API "X" (timeout or network error)` | `CallAPI` | `LF_CallEx` 返回 nil | 增大 `FTimeout`；检查目标 App 是否注册 |
| `Empty response from API "X"` | `CallAPI` | 服务端返回 size=0 | 服务端拒绝或未注册该 API |
| `Failed to read response bytes for API "X"` | `CallAPI` | `LF_ReadStringBytes` 失败 | 罕见 |
| `Empty response bytes` | `SafeParseJson` | 空字节缓冲 | 服务端返回空 |
| `Failed to create JSON root: ...` | `SafeParseJson` | `TZ_JsonObject.Create` 异常 | 内存不足 |
| `Invalid JSON response` | `SafeParseJson` | `Parae` 失败 | 服务端返回非法 JSON |
| `JSON parse exception: ...` | `SafeParseJson` | `Parse` 抛异常 | 服务端返回畸形数据 |
| `X response missing "code"` | `CheckResponseCode` | 响应无 `code` 字段 | 服务端协议不匹配 |
| `X failed (code N)` | `CheckResponseCode` | `code <> 0` 但无 `error` 字段 | 服务端未提供详情 |

> **相关原理**：`LF_Call` 超时返回 size=0 的空句柄（非 nil），见 [`LingoFuse_Pascal_Complete_Guide.md`](LingoFuse_Pascal_Complete_Guide.md) 中 `LF-CALL-001`。

### 4.3 会话相关

| 错误消息原文 | 抛出位置 | 根因 | 修复 |
|--------------|----------|------|------|
| `CloseSession: empty session_id` | `CloseSession` | 传入空 `ASessionId` | 检查参数 |
| `CancelSession: empty session_id` | `CancelSession` | 传入空 `ASessionId` | 检查参数 |
| `Server did not return session_id` | `CreateSession` / `Generate` / ... / `GenerateWithAttachmentsAndSchema` | 服务端 `code:0` 但无 `session_id` | 检查服务端版本 |

### 4.4 能力与设置相关

| 错误消息原文 | 抛出位置 | 根因 | 修复 |
|--------------|----------|------|------|
| `set_system_message is not supported by this server (kind=X)` | `SetSystemMessage` | 能力矩阵已拉取且不支持 | 改用 `CreateSession(system_message)` |
| `get_api_capabilities response missing "capabilities"` | `GetAPICapabilities` | 响应缺 `capabilities` 字段 | 检查服务端版本 |
| `Failed to clone capabilities: ...` | `GetAPICapabilities` | `TZ_JsonObject.Assign` 异常 | 罕见 |

### 4.5 附件相关

| 错误消息原文 | 抛出位置 | 根因 | 修复 |
|--------------|----------|------|------|
| `PopulateAttachmentArray: nil target array` | `PopulateAttachmentArray` | 目标数组为 nil | `joReq.A['attachments']` 应非 nil（内部 bug 排查） |
| `Text attachment "X" is N bytes, exceeds per-file limit N` | `PopulateAttachmentArray` | 单文件超 256 KB | 拆分文件 |
| `Cumulative text size N exceeds total limit N` | `PopulateAttachmentArray` | 累计超 512 KB | 减少附件 |
| `Image attachment "X" has empty data_b64` | `PopulateAttachmentArray` | `DataB64` 空 | 检查 base64 编码 |
| `Image attachment "X" is N base64 chars, exceeds per-file limit N` | `PopulateAttachmentArray` | 单图超 8 MB | 压缩图片 |
| `Cumulative image size N exceeds total limit N` | `PopulateAttachmentArray` | 累计超 16 MB | 减少图片 |
| `Cannot open text file "X": ...` | `GenerateWithTextFile` | 文件不存在或无权限 | 检查路径 |
| `Cannot open image file "X": ...` | `GenerateWithImageFile` / `BuildImageAttachmentFromFile` | 文件不存在或无权限 | 检查路径 |
| `Image file "X" is empty (0 bytes).` | `GenerateWithImageFile` / `BuildImageAttachmentFromFile` | 空文件 | 检查源文件 |
| `Cannot read image file "X": ...` | `BuildImageAttachmentFromFile` | 读取文件时异常 | 检查磁盘/权限 |
| `Image file "X" would encode to about N base64 chars, exceeding per-file limit N` | `BuildImageAttachmentFromFile` | 图片可能过大 | 压缩图片 |
| `Base64 encoding failed: ...` | `BuildImageAttachmentFromFile` | `umlBase64EncodeBytes` 异常 | 罕见 |

### 4.6 Structured Output 相关

| 错误消息原文 | 抛出位置 | 根因 | 修复 |
|--------------|----------|------|------|
| `GenerateStructured: empty response_format JSON` | `GenerateStructured` | 传入空字符串 | 检查参数 |
| `GenerateStructured: response_format is not valid JSON` | `GenerateStructured` | `ParseText` 失败 | 检查 JSON 语法 |
| `BuildSchemaResponseFormatJson: empty schema name` | `GenerateWithJsonSchema` / `BuildSchemaResponseFormatJson` | `ASchemaName = ''` | 提供 schema 名 |
| `BuildSchemaResponseFormatJson: empty schema JSON` | `GenerateWithJsonSchema` / `BuildSchemaResponseFormatJson` | `ASchemaJson = ''` | 提供 schema 本体 |
| `BuildSchemaResponseFormatJson: schema JSON is not valid` | `GenerateWithJsonSchema` / `BuildSchemaResponseFormatJson` | schema 本体解析失败 | 检查 JSON 语法 |
| `SendGenerateCombined: response_format is not valid JSON` | `SendGenerateCombined` | envelope 解析失败 | 检查 `BuildSchemaResponseFormatJson` 的输入 |
| `BuildImageAttachmentFromFile: empty file path` | `BuildImageAttachmentFromFile` | 传入空路径 | 检查参数 |

### 4.7 服务端拒绝相关

| 错误消息原文 | 抛出位置 | 根因 | 修复 |
|--------------|----------|------|------|
| `Image attachments are not supported by this server: --vision is disabled` | 服务端返回（`code: -1`） | `llm_proxy` / LTB 未传 `--vision` | 服务端启动时加 `--vision` |
| `Image attachments are not supported by llm_service in this revision` | 服务端返回（`code: -1`） | 目标服务端是 `llm_service` | 改用 `llm_proxy` / LTB |
| `set_system_message is not supported by llm_proxy` | 服务端返回（`code: -1`） | 目标服务端是代理 | 改用 `CreateSession(system_message)` |
| **`response_format is not supported by the backend`** | 后端返回（通常是 400/422） | 后端不支持 Structured Outputs | 换支持 JSON Schema 的模型/后端 |
| **`response_format: unrecognized type json_schema`** | 后端返回（400） | 请求结构嵌套错误 | 确保 schema 直接放在 `json_schema` 下，不额外嵌套 |

---

## §5 升级模板

### 5.1 添加新 API（无附件）

**照抄 `Generate` 结构**：

```pascal
function TLLMClient.Translate(const AText, ATargetLang: string;
  out ATranslated, AError: string): boolean;
var
  joReq, joResp: TZ_JsonObject;
  reqBytes, respBytes: TBytes;
begin
  Result := False;
  AError := '';
  ATranslated := '';

  // 步骤 1：连接检查（仅 Call API 需要，纯静态方法不需要）
  if not FConnected then
  begin
    AError := 'Not connected to LingoFuse service';
    Exit;
  end;

  // 步骤 2：构造请求
  joReq := TZ_JsonObject.Create;
  try
    joReq.S['text'] := AText;
    joReq.S['target_lang'] := ATargetLang;
    joReq.S['client_name'] := FClientName;   // 或 session_id
    reqBytes := joReq.ToBytes;
  finally
    DisposeObject(joReq);
  end;

  // 步骤 3：发送 + 解析
  if not CallAPI('translate', reqBytes, respBytes, AError) then Exit;
  if not SafeParseJson(respBytes, joResp, AError) then Exit;

  // 步骤 4：检查 code 并提取响应
  try
    if not CheckResponseCode(joResp, 'translate', AError) then Exit;
    ATranslated := joResp.S['translated'];
    Result := True;
  finally
    DisposeObject(joResp);
  end;
end;
```

**必须遵循的规则** [源码]：
1. **早返回前清空 `out` 参数**（避免调用者读到垃圾）。
2. **`joReq` / `joResp` 必须在 `try...finally` 内释放**。
3. **`SafeParseJson` 失败时 `joResp` 已是 nil**，不要再释放。
4. **`CheckResponseCode` 的第二个参数是 API 名**（用于错误消息）。

### 5.2 添加新 API（有附件）

在 5.1 的基础上，请求构造部分加：

```pascal
attachmentsArr := joReq.A['attachments'];
PopulateAttachmentArray(ATexts, AImages, attachmentsArr, AError);
if AError <> '' then Exit;
```

### 5.3 添加新 API（带 `options.response_format`）

参考 `GenerateStructured` 的写法：

```pascal
joOptions := joReq.O['options'];
joResponseFormat := joOptions.O['response_format'];
if not joResponseFormat.ParseText(AResponseFormatJson) then
begin
  AError := '...';
  Exit;
end;
```

**关键点**：`ParseText` 直接把调用方提供的 JSON 字符串塞进目标子对象，`O[]` 会自动创建缺失的键。

### 5.4 添加新 API（附件 + schema 组合）

参考 `SendGenerateCombined` 的写法（v3.10）：

```pascal
(* 1. 构造基础请求 *)
joReq := TZ_JsonObject.Create;
try
  joReq.S['content'] := AContent;
  joReq.S['prompt'] := APrompt;

  (* 2. session 解析 *)
  if ASessionId <> '' then
    joReq.S['session_id'] := ASessionId
  else if FCurrentSessionId <> '' then
    joReq.S['session_id'] := FCurrentSessionId
  else
    joReq.S['client_name'] := FClientName;

  (* 3. 附件（可选） *)
  if (Length(ATexts) > 0) or (Length(AImages) > 0) then
  begin
    attachmentsArr := joReq.A['attachments'];
    PopulateAttachmentArray(ATexts, AImages, attachmentsArr, AError);
    if AError <> '' then Exit;
  end;

  (* 4. response_format（可选） *)
  if AResponseFormatJson <> '' then
  begin
    joOptions := joReq.O['options'];
    joResponseFormat := joOptions.O['response_format'];
    if not joResponseFormat.ParseText(AResponseFormatJson) then
    begin
      AError := '...';
      Exit;
    end;
  end;

  reqBytes := joReq.ToBytes;
finally
  DisposeObject(joReq);
end;
```

**关键点**：附件和 `response_format` 可以同时存在，两者都是 `joReq` 的子对象，互不冲突。

### 5.5 添加新事件类型

**步骤**：
1. 在 `type` 区加事件类型：
   ```pascal
   TLLMNewEvent = procedure(const SessionId, Data: string) of object;
   ```
2. 在 `TLLMClient` 的 `private` 区加字段：`FOnNew: TLLMNewEvent;`
3. 加 `DoNew` 分派方法：
   ```pascal
   procedure TLLMClient.DoNew(const SessionId, Data: string);
   begin
     if Assigned(FOnNew) then FOnNew(SessionId, Data);
   end;
   ```
4. 在 `HandleLLMNotify` 里加分派：
   ```pascal
   else if msgType = 'new' then
   begin
     Data := jo.S['data'];
     DoNew(SessionId, Data);
   end;
   ```
5. 加 public 属性：
   ```pascal
   property OnNew: TLLMNewEvent read FOnNew write FOnNew;
   ```

### 5.6 加新的"改 A 必须同步改 B"规则

**当修改以下内容时，必须检查的关联点**：

| 改了 A | 必须检查 B |
|--------|-----------|
| `Connect` 的 `LF_*` 调用顺序 | `FPrepared := True` 之前/之后的位置 |
| `FTimeout` 语义 | 所有 `CallAPI` 里的 `uint64(FTimeout)` |
| `FCapabilities` 的 JSON 结构 | `LLMSupported` / `IsToolBridge` / `HasVision` / `HasAttachments` |
| `FCurrentSessionId` 语义 | 所有 Generate 系列方法（含 `GenerateStructured` / `GenerateWithJsonSchema` / `GenerateWithImageFileAndSchema` / `GenerateWithAttachmentsAndSchema` / `GenerateCurrent` / `SendGenerateCombined`） |
| 附件字段类型 | `ClearTextAttachments` / `ClearImageAttachments` / `PopulateAttachmentArray` / `BuildImageAttachmentFromFile` |
| 事件类型定义 | `HandleLLMNotify` 的分派 + `DoXxx` 方法 + 属性 |
| `SafeParseJson` 的返回契约 | 所有调用点（**失败时 `AJson` 必须是 nil**） |
| `CheckResponseCode` 的语义 | 所有调用点（**第二个参数是 API 名**） |
| **`GenerateStructured` 的 `response_format` 结构** | **`BuildSchemaResponseFormatJson` 的组装逻辑；文档 §16 的所有模板** |
| **`BuildSchemaResponseFormatJson` 的输出结构** | **`GenerateWithJsonSchema` / `GenerateWithImageFileAndSchema` / `GenerateWithAttachmentsAndSchema` 都依赖它** |

---

## §6 内存与所有权

### 6.1 谁创建，谁释放

| 对象 | 创建者 | 释放者 | 备注 |
|------|--------|--------|------|
| `TDataHnd`（`LF_CreateDataEx` 返回） | `CallAPI` | `CallAPI`（`try...finally`） | 调用者不管理 |
| `TDataHnd`（`LF_CallEx` 返回） | `LF_CallEx` | `CallAPI`（`try...finally`） | 调用者不管理 |
| `TZ_JsonObject`（请求） | 各 Call API | 各 Call API | `try...finally` |
| `TZ_JsonObject`（响应） | `SafeParseJson` | 各 Call API | `try...finally`；**`SafeParseJson` 失败时已释放** |
| `FCapabilities` | `GetAPICapabilities` / `FetchCapabilities` | `TLLMClient.Destroy` | 不要外部释放 |
| `FLastException` | `TLLMClient.Create` | `TLLMClient.Destroy` | 不要外部释放 |
| 附件数组（`TLLMTextAttachmentArray`） | **调用者** | **调用者** | 用 `ClearTextAttachments` |
| 事件回调参数的 `string` | `HandleLLMNotify` | 编译器托管 | 回调返回后可能失效 |
| **`GenerateWithImageFileAndSchema` 内部构造的 attachments** | 方法内部 | 方法内部（`finally` 中清理） | **调用者不需要操心** |

### 6.2 释放模式（模板）

**附件**：

```pascal
var
  Texts: TLLMTextAttachmentArray;
begin
  SetLength(Texts, N);
  try
    // 填充 ...
    Client.GenerateWithAttachments(..., Texts, ..., S, E);
  finally
    TLLMClient.ClearTextAttachments(Texts);   // ← 必须
  end;
end;
```

**`GetAPICapabilities` 的返回**：

```pascal
var
  CapJson: TZ_JsonString;   // ⚠️ 不是 string
  E: string;
begin
  if Client.GetAPICapabilities(CapJson, E) then
  begin
    // CapJson.Text 是 JSON 文本
    // CapJson 会在 CapJson 离开作用域时自动释放内部缓冲
  end;
end;
```

**`GenerateWithImageFileAndSchema` 的调用**（**无需手动清理**）：

```pascal
// ✅ 只需传文件路径；方法内部负责读文件、编码、发请求、清理
Client.GenerateWithImageFileAndSchema('检测', '', 'a.png',
                                      'object_detection', Body, True, S, E);
```

---

## §7 线程模型

### 7.1 线程归属表

| 方法/回调 | 执行线程 | 备注 |
|-----------|---------|------|
| `Create` / `Destroy` | 调用者 | |
| `Connect` / `Disconnect` | 调用者 | |
| 所有 Call API（含 `GenerateWithImageFileAndSchema` / `GenerateWithAttachmentsAndSchema`） | 调用者 | **同步阻塞**（读文件 + base64 + 发请求） |
| `OnChunk` / `OnThink` / `OnFinish` / `OnError` / `OnClosed` | **LingoFuse 通知线程** | **不是主线程** |
| `LastException` 属性读取 | 任意线程 | `TAtomString` 内部有锁 |

### 7.2 通知线程约束（**核心规则**）

1. **回调中不能调用任何 Call API** [源码]：会死锁（回调线程持有 LingoFuse 内部锁）。相关原理见 [`LingoFuse_Pascal_Complete_Guide.md`](LingoFuse_Pascal_Complete_Guide.md) 中 `LF-CB-002`。
2. **回调中不能直接操作 UI** [教训]：VCL/LCL 非线程安全。相关原理见 [`LingoFuse_Pascal_Complete_Guide.md`](LingoFuse_Pascal_Complete_Guide.md) 中 `LF-CB-004` / `LF-NET-005`。
3. **回调中维护跨消息状态要加锁** [未核实]：不确定通知线程是单一还是池化。
4. **回调中不要 `DoStatus`** [源码]：`llm_client_v3` 从不这样做，避免 `Z.Status` 队列重入。

**正确 marshalling 模板**：

```pascal
procedure TForm1.OnLLMChunk(const SessionId, Text: string);
begin
  TThread.Queue(nil,
    procedure
    begin
      // 这里在主线程
      Memo1.Lines.Add(Text);
    end);
end;
```

---

## §8 深坑与验证方法

### 8.1 回调在主线程之外

**症状**：UI 偶发崩溃，多核机器概率更高。

**验证**：

```pascal
procedure TForm1.OnLLMChunk(const SessionId, Text: string);
begin
  OutputDebugString(PChar('chunk tid=' + IntToStr(GetCurrentThreadId)));
end;
// 同时在 FormCreate 里打一次主线程 ID
OutputDebugString(PChar('main tid=' + IntToStr(GetCurrentThreadId)));
```

**修复**：见 §7.2 marshalling 模板。

### 8.2 回调中调用 Call API 死锁

**症状**：整个进程挂住，Ctrl+C 无响应。

**修复**：

```pascal
TThread.CreateAnonymousThread(
  procedure
  var
    S, E: string;
  begin
    FClient.Generate('follow-up', '', S, E);
  end).Start;
```

### 8.3 `Parae` 对空 `TBytes` 越界

**症状**：调试模式下崩溃；Release 优化下"看起来正常"。

**修复**：`if Length(Bytes) = 0 then Exit;` 后再 `Parae`。

### 8.4 `LF_WriteStringBytes` 追加 NUL

**验证**：

```pascal
var
  H: TDataHnd;
begin
  H := LF_CreateDataEx('test');
  try
    LF_WriteStringBytes(H, TEncoding.UTF8.GetBytes('hello'));
    WriteLn(LF_GetSize(H));   // 期望 6（'hello' 5 + NUL 1）
  finally
    LF_FreeData(H);
  end;
end;
```

**含义**：跨语言协议的关键。相关原理见 [`LingoFuse_Pascal_Complete_Guide.md`](LingoFuse_Pascal_Complete_Guide.md) 中 `LF-DATA-005` / `LF-XLANG-001`。

### 8.5 `LF_ReadStringBytes` 的 fault-tolerant

**验证**：

```pascal
var
  Data: TBytes;
begin
  Data := Client.LF_ReadStringBytes(InputHnd);
  // 即使没有 NUL，Data 也会包含全部剩余字节
end;
```

**含义**：HTTP 桥接（`bridge.py`）发来的 JSON 通常无 NUL，能正常读。相关原理见 [`LingoFuse_Pascal_Complete_Guide.md`](LingoFuse_Pascal_Complete_Guide.md) 中 `LF-DATA-004`。

### 8.6 `umlBase64EncodeBytes` 消费源缓冲

**验证**：

```pascal
var
  Src, Dst: TBytes;
begin
  Src := TEncoding.UTF8.GetBytes('hello');
  WriteLn(Length(Src));              // 5
  umlBase64EncodeBytes(Src, Dst);
  WriteLn(Length(Src));              // 0 ← 源被消费
  WriteLn(TEncoding.ASCII.GetString(Dst));  // 'aGVsbG8='
end;
```

**含义**：调用后**不能再用 `Src`**。v3.10 的 `BuildImageAttachmentFromFile` 严格遵循此规则。

### 8.7 `FCurrentSessionId` 的隐式续接

**验证**：

```pascal
var
  S, E: string;
begin
  S := '';
  Client.Generate('first', '', S, E);
  WriteLn('S1 = ', S);
  S := '';   // 想清空
  Client.Generate('second', '', S, E);
  WriteLn('S2 = ', S);   // ← S2 = S1（仍然续接）
end;
```

**修复**：想强制新会话，用 `Client.CurrentSessionId := ''` 或先 `CloseSession`。

### 8.8 `Parae` 是 `TBytes` 的原生入口

**反例**：

```pascal
// ❌ 多余中转
var
  Js: TZ_JsonString;
begin
  Js.Bytes := ARawBytes;
  AJson.ParseText(Js);
end;

// ✅ 直接
AJson.Parae(ARawBytes);
```

### 8.9 回调异常的静默

**症状**：`OnChunk` 里抛异常，UI 无任何提示。

**含义**：异常写入 `FLastException`，需要**主动轮询**才能发现：

```pascal
if Client.LastException <> '' then
  ShowMessage('Last error: ' + Client.LastException);
```

### 8.10 `HandleLLMNotify` 静默丢弃消息

**症状**：服务端发了消息，但 `OnChunk` 未触发。

**可能原因**：
- `Input_ = nil`
- `LF_GetSize(Input_) <= 0`
- `LF_ReadStringBytes` 返回空
- `js.Len <= 0`
- `jo.ParseText(js)` 失败
- 缺 `type` 字段
- `type` 是未知值

### 8.11 图片附件被服务端拒绝

**症状**：返回 `code: -1`。

**可能原因**：
1. 连接的是 `llm_service`：不支持多模态。
2. 连接的是 `llm_proxy` / LTB，但未传 `--vision`。
3. 图片超限：单图 base64 超 8 MB，或累计超 16 MB。

**修复**：

```pascal
if not FClient.LLMSupported('attachments') then
begin
  ShowMessage('服务端不接受附件字段');
  Exit;
end;

if FClient.ServerKind = 'service' then
begin
  ShowMessage('llm_service 不支持多模态，请改用 llm_proxy / LTB');
  Exit;
end;
```

### 8.12 Structured Output 请求被后端拒绝

**症状**：`AError` 里出现 400 / 422 或 `unrecognized type json_schema`。

**可能原因**：
1. 连接的是 `llm_service`：不转发 `response_format`。
2. 后端不支持 Structured Outputs。
3. schema 结构嵌套错误。
4. `strict` 字段类型错误。

**修复**：

```pascal
if FClient.ServerKind = 'service' then
begin
  ShowMessage('llm_service 不支持 Structured Output，请改用 llm_proxy / LTB');
  Exit;
end;
// 使用 v3.10 的组合方法（自动组装正确的 envelope）
FClient.GenerateWithImageFileAndSchema(...);
```

### 8.13 Structured Output 被忽略（后端静默返回纯文本）

**症状**：调用成功，但模型返回自由文本。

**可能原因**：
1. 后端忽略了 `response_format`。
2. 模型不支持 Structured Outputs。
3. prompt 冲突。

**修复**：
1. 确认后端版本（LM Studio 0.3.0+）。
2. 确认模型支持（Qwen2.5 系列 / Qwen2.5-VL / Nemotron Omni）。
3. prompt 与 schema 保持一致。

### 8.14 v3.10 组合方法的坐标顺序与归一化约定

**症状**：JSON 合法，`label` 也对，但 `bbox` 位置和实际不符。

**可能原因**：
- **坐标顺序反了**：模型返回 `[y1,x1,y2,x2]`，但下游按 `[x1,y1,x2,y2]` 使用。
- **未归一化**：模型返回像素坐标（`0~1920`），不是 `0~1`。
- **上下颠倒**：某些模型用图像坐标系（左上为原点），某些用数学坐标系（左下为原点）。

**修复**：
1. **在提示词里明确说明**：
   ```
   返回归一化坐标（0~1），格式为 [x_min, y_min, x_max, y_max]，
   (x_min, y_min) 是左上角，(x_max, y_max) 是右下角。
   ```
2. **先做单目标测试**：用一张只有一个明显物体的图，验证 bbox 数值。
3. **Schema 里加约束**：
   ```json
   "bbox": {
     "type": "array",
     "items": { "type": "number", "minimum": 0, "maximum": 1 },
     "minItems": 4,
     "maxItems": 4
   }
   ```
   这样模型无法输出超出 `[0,1]` 的值。

---

## §9 服务端能力差异

### 9.1 三种服务端

| 服务端 | `server_kind` | 特征 | 多模态 | Structured Output |
|--------|---------------|------|:------:|:-----------------:|
| `llm_service.py` | `'service'` | 本地模型，含 `set_system_message`；**纯文本** | ❌ | ❌ |
| `llm_proxy.py` | `'proxy'` | 无状态转发；**图片由后端决定** | ✅（需 `--vision`） | ✅ |
| `llm_proxy_tool.py` | `'proxy'` | 无状态转发 + **服务端工具执行** | ✅（需 `--vision`） | ✅ |

**`proxy_tool` 与 `proxy` 的唯一区别**：

```pascal
IsToolBridge = LLMSupported('tools') and LLMSupported('tool_calls');
```

**`server_kind` 都是 `'proxy'`** [协议]——不要只凭 `ServerKind` 判断。

### 9.2 能力矩阵字段

```json
{
  "code": 0,
  "server_kind": "service",
  "capabilities": {
    "generate": 1,
    "create_session": 1,
    "close_session": 1,
    "cancel_session": 1,
    "list_sessions": 1,
    "set_system_message": 1,
    "health": 1,
    "llm_stream": 1,
    "attachments": 1,
    "vision": 0,
    "tools": 0,
    "tool_calls": 0,
    "tool_results": 0
  }
}
```

**关键字段语义**：

| 字段 | 语义 | 说明 |
|------|------|------|
| `attachments` | 服务端是否**接受** `attachments` 字段 | 1 = 接受 |
| `vision` | 服务端**自身是否做视觉处理** | **0 在所有服务端都成立** |
| `tools` / `tool_calls` / `tool_results` | 服务端是否支持工具相关行为 | 只有 LTB 为 1 |
| `set_system_message` | 服务端是否支持切换全局 system message | 只有 `llm_service` 为 1 |
| （无 `response_format` 字段） | Structured Output 是否可用 | **不在能力矩阵里**——由服务端类型 + 后端能力共同决定 |

### 9.3 `set_system_message` 的行为差异

| 服务端 | 直接调用 | 能力已知后调用 |
|--------|----------|----------------|
| `service` | 走网络 | 走网络（**服务端支持**） |
| `proxy` / `proxy_tool` | 走网络 → 服务端拒绝 | **本地短路**（不发网络） |

---

## §10 版本迁移

### 10.1 v3.6 → v3.7：清理 6 个辅助函数

**教训**：**看到 `Xxx` 和 `XxxEx` 成对存在，优先用 `XxxEx`**。

### 10.2 v3.7 → v3.8：破坏性 API 变更

| 变更 | 迁移动作 |
|------|---------|
| `CapabilitiesRawJson: string` → `TZ_JsonString` | 调用方取 `.Text` |
| `GetAPICapabilities` 参数 `var string` → `var TZ_JsonString` | 调用方改类型；取文本用 `.Text` |
| 附件字段 `string` → `TZ_JsonString` | `Att.Name := 'x'` → `Att.Name.Text := 'x'` |

### 10.3 v3.8 → v3.9：新增 Structured Output（非破坏性）

| 变更 | 迁移动作 |
|------|---------|
| 新增 `GenerateStructured` | 纯新增，无迁移动作 |
| 新增 `GenerateWithJsonSchema` | 纯新增，无迁移动作 |
| 模块版本字符串 `'Dynamic LLM Client (v3.8)'` → `(v3.9)` | 仅 App 描述文本变化 |

### 10.4 v3.9 → v3.10：新增组合方法（非破坏性）

| 变更 | 迁移动作 |
|------|---------|
| 新增 `GenerateWithImageFileAndSchema` | 纯新增，无迁移动作 |
| 新增 `GenerateWithAttachmentsAndSchema` | 纯新增，无迁移动作 |
| `GenerateWithJsonSchema` 内部重构（改用 `BuildSchemaResponseFormatJson`） | 行为不变；调用方无感 |
| 模块版本字符串 `(v3.9)` → `(v3.10)` | 仅文本 |

**兼容性**：所有已有代码**无需修改**。

**新增私有方法**（不改变公开 API 表面）：
- `BuildImageAttachmentFromFile`
- `BuildSchemaResponseFormatJson`
- `SendGenerateCombined`

### 10.5 文档侧变更

**v2.2**：新增 §16 Structured Output 完整指南（5 个检测器模板）。

**v2.3**：更新 §16 增加两个组合方法（v3.10）；多处同步更新；GUI 流程更新为 v3.4。

**v2.4**：移除对已删除文档的引用（`LingoFuse_LLM_Pitfalls_For_AI.md`）；§4 / §7 / §8 中的 Pitfalls 引用改为指向 [`LingoFuse_Pascal_Complete_Guide.md`](LingoFuse_Pascal_Complete_Guide.md) 中等价条目；新增 [`llm_client_v3_Structured_Output_Learning_Guide.md`](llm_client_v3_Structured_Output_Learning_Guide.md) 引用。

---

## §11 修改影响面分析

> **用途**：AI 改动代码前，快速定位相关点。

### 11.1 修改 `Connect` 的检查顺序

**必须保持的顺序**：
1. `LF_ResetPrepare`
2. `LF_SetOptionEx`（`Wait_Connection_ReadyOk` / `Overlap_Connection`）
3. `LF_PrepareClientEx`
4. `LF_PrepareDone`
5. **`FPrepared := True`** ← 分水岭
6. `LF_Generate_AppNameEx`（**必须在 `PrepareDone` 之后**）
7. `LF_CreateAppEx`
8. `LF_RegisterNotifyEx`
9. `LF_BindApp`
10. `FConnected := True`
11. 能力探测

> **相关原理**：`LF_Generate_AppName` 必须在 `LF_PrepareDone` 之后，见 [`LingoFuse_Pascal_Complete_Guide.md`](LingoFuse_Pascal_Complete_Guide.md) 中 `LF-APP-003`。

### 11.2 修改 `FTimeout`

**受影响的调用**：
- `CallAPI` 里的 `LF_CallEx(FServerApp, hnd, uint64(FTimeout))`

**检查点**：`FTimeout` 是 `integer`，转为 `uint64` 前**不能为负**；值是毫秒。

### 11.3 修改 `FCurrentSessionId` 语义

**受影响的调用**：
- `CreateSession`：成功后更新
- `CloseSession`：如果匹配则清空
- `Generate` / `GenerateWithAttachments`：如果 `ASessionId=''` 用它
- **`GenerateStructured` / `GenerateWithJsonSchema` / `GenerateWithImageFileAndSchema` / `GenerateWithAttachmentsAndSchema`：与 `Generate` 相同**
- **`SendGenerateCombined`：与 `Generate` 相同**
- `GenerateCurrent`：读它（不写）

### 11.4 修改附件记录字段

**受影响的点**：
- `ClearTextAttachments` / `ClearImageAttachments`
- `PopulateAttachmentArray`
- `GenerateWithTextFile` / `GenerateWithImageFile`
- **`BuildImageAttachmentFromFile`**（v3.10）
- **`SendGenerateCombined`**（v3.10）

### 11.5 修改 `SafeParseJson` 契约

**契约**：
- **成功**：`AJson` 非 nil，返回 `True`
- **失败**：`AJson` 为 nil，返回 `False`，`AError` 非空

**所有调用点**都依赖这个契约（含 `SendGenerateCombined`）。

### 11.6 修改 `CheckResponseCode` 契约

**契约**：
- **成功**：返回 `True`，`AError` 为空
- **失败**：返回 `False`，`AError` 是 `error` 字段或 `'<APIName> failed (code N)'`

### 11.7 修改 `response_format` 结构

**受影响的点**：
- **`BuildSchemaResponseFormatJson`**：唯一组装 envelope 的地方
- **`GenerateStructured`**：直接接收 caller 提供的字符串
- **`GenerateWithJsonSchema` / `GenerateWithImageFileAndSchema` / `GenerateWithAttachmentsAndSchema`**：全部依赖 `BuildSchemaResponseFormatJson`
- **文档 §16 的所有模板**：必须同步
- **后端兼容性**：改动结构可能让已有后端拒绝请求

**检查点**：
- `response_format` 的顶层是 `{"type": "json_schema", "json_schema": {...}}`
- `json_schema` 内直接包含 `name` / `strict` / `schema`，**不额外嵌套**

---

## §12 常见任务快速索引

### 12.1 "我要写一个 LLM 客户端"

→ §3.1 模板

### 12.2 "我要加一个新 API"

→ §5.1 模板

### 12.3 "我要加一个新事件类型"

→ §5.5

### 12.4 "程序运行时挂了"

→ §4 错误消息对照表

### 12.5 "UI 崩溃了"

→ §8.1

### 12.6 "程序死锁了"

→ §8.2

### 12.7 "内存泄漏了"

→ §6.1

### 12.8 "附件上传失败"

→ §4.5

### 12.9 "图片附件被拒绝"

→ §8.11 + §4.7

### 12.10 "升级破坏性变更"

→ §10.2

### 12.11 "改动会不会破坏别的"

→ §11

### 12.12 "如何判断服务端支持什么"

→ §3.4

### 12.13 "我要做检测器方框标注"

→ **§16.3** 直接复制模板

### 12.14 "我要做结构化输出但不是检测器"

→ **§16.5** 通用模板

### 12.15 "Structured Output 不生效"

→ §8.12 / §8.13

### 12.16 **"我要图片 + 检测器一步到位"**

→ **§3.5** + **§16.3 模板 2** + **§16.7**
→ 用 **`GenerateWithImageFileAndSchema`**
→ 无需手动组合附件和 schema

### 12.17 **"我要多附件 + schema"**

→ **§5.4** + **§16.7**
→ 用 **`GenerateWithAttachmentsAndSchema`**

### 12.18 **"GUI 怎么用"**

→ **§16.9**

---

## §13 诚实的不确定清单

> **用途**：AI 遇到以下场景**必须停止**，回查源码或询问人类。
>
> **状态标注**：✅ 已解决；⏳ 未解决。

| # | 不确定点 | 状态 | 说明 |
|---|---------|:----:|------|
| 1 | `LF_CallEx` 失败时返回 nil 还是空句柄 | ✅ | **返回 size=0 的空句柄**（见 `LF-CALL-001`） |
| 2 | 通知回调是单线程还是池化 | ⏳ | 建议打线程 ID 观察 |
| 3 | `ClearTextAttachments` 是否真的必要 | ⏳ | 建议保留 |
| 4 | `umlBase64EncodeBytes` 消费源是否所有版本一致 | ⏳ | 读源码确认 |
| 5 | `server_kind` 字段在所有版本是否存在 | ✅ | 三种服务端都返回 |
| 6 | `ATTACHMENT_ALLOWED_IMAGE_MIMES` 的用途 | ⏳ | 定义了但未使用 |
| 7 | `GenerateWithTextFile` 的 Latin-1 fallback 正确性 | ⏳ | 造非 UTF-8/GBK 文件实测 |
| 8 | `TAtomString.Create('')` 是否所有平台行为一致 | ⏳ | 读 `Z.Core.pas` |
| 9 | LM Studio 对 `response_format` 的具体支持边界 | ⏳ | 需要实测 |
| 10 | 不同 VLM 对 bbox 坐标的期望格式 | ⏳ | 需要实测 |

---

## §14 关键规则总结（AI 速记）

> **写代码必守**：
> 1. 构造参数用 `'LLM_Service'` + `'ipc:llm_service'`（**大小写严格**）。
> 2. 回调在**通知线程**执行——UI 操作必须 `TThread.Queue`。
> 3. 回调中**不能**调用 Call API——会死锁。
> 4. 附件数组必须 **`ClearTextAttachments` / `ClearImageAttachments`**（v3.10 的组合方法内部已处理，无需外部管理）。
> 5. `Connect` 后**检查返回值**——能力探测失败会静默。
> 6. `Generate` 传空 `ASessionId` 会**续接**会话。
> 7. **调用前做能力发现**——尤其 `SetSystemMessage` / 附件 / 工具相关 API。
> 8. **图片附件前检查 `ServerKind`**——`'service'` 直接拒绝。
> 9. **`HasVision` 不可用于判断"能否发图片"**——它在本版本永远是 False。
> 10. **Structured Output 前检查 `ServerKind`**——`'service'` 不支持，必须用 `'proxy'`。
> 11. **`response_format` 的 `json_schema` 直接嵌在顶层**——不要再嵌套一层 `json_schema`。
> 12. **v3.10 一步到位检测器**：直接用 `GenerateWithImageFileAndSchema`，不用手动组合。

> **改代码必守**：
> 1. 所有请求构造用 `TZ_JsonObject`，`try...finally` 释放。
> 2. 解析响应用 `SafeParseJson`，**失败时 `AJson` 已 nil**。
> 3. 错误检查用 `CheckResponseCode`，**第二个参数是 API 名**。
> 4. `Parae(TBytes)` 前先检查 `Length > 0`。
> 5. 新 API **照抄 `Generate` 结构**。
> 6. **Structured Output 相关结构改动**必须同步改 §16 文档模板。
> 7. **改 `BuildSchemaResponseFormatJson` 输出结构**会影响 4 个公开方法。

> **禁用清单**：
> - ❌ 不用 `ParseText` 解析 `TBytes`（用 `Parae`）
> - ❌ 不引入 `lingofuse_helper`（用 `lingofuse_import` 的 `*Ex`）
> - ❌ 不用 `reference to procedure` 做事件（用 `of object`）
> - ❌ 不在 `Disconnect` 里调 `LF_Shutdown`（宿主负责）
> - ❌ 不读 `FCapabilities` 原始 JSON 判 `nil`（用 `LLMSupported`）
> - ❌ **不用 `HasVision` 判断"能否发图片"**（用 `ServerKind` + `HasAttachments`）
> - ❌ **不在 `response_format` 下再套一层 `json_schema`**

---

## §15 相关文档

| 文档 | 说明 |
|------|------|
| [`../Pascal_Integration_Guide.md`](../Pascal_Integration_Guide.md) | Pascal 开发者切入指南（六大场景） |
| [`../Build_Guide.md`](../Build_Guide.md) | 编译指南（含 `llm_client_v3` / `llm_tool_v3`） |
| [`../readme.md`](../readme.md) | 项目总览与四大核心应用组件 |
| [`LingoFuse_LLM_Ecosystem_User_Guide.md`](LingoFuse_LLM_Ecosystem_User_Guide.md) | 生态总览（四大应用组件 + 两条路径） |
| [`LingoFuse_LLM_Proxy_CLI_Guide.md`](LingoFuse_LLM_Proxy_CLI_Guide.md) | 纯转发代理命令行手册（第 5.5 节多模态） |
| [`LingoFuse_LLM_Proxy_Tool_CLI_Guide.md`](LingoFuse_LLM_Proxy_Tool_CLI_Guide.md) | LTB 命令行手册（第 4.5 节多模态） |
| [`LingoFuse_LLM_Service_CLI_guide.md`](LingoFuse_LLM_Service_CLI_guide.md) | 本地推理服务手册 |
| [`LingoFuse_Pascal_Complete_Guide.md`](LingoFuse_Pascal_Complete_Guide.md) | Pascal 核心层完整指南（含踩坑知识库） |
| [`llm_client_v3_Structured_Output_Learning_Guide.md`](llm_client_v3_Structured_Output_Learning_Guide.md) | Structured Output 完整学习指南 |
| [`QUICK_START_LLM_STACK.md`](QUICK_START_LLM_STACK.md) | LLM 栈快速上手 |

### 代码生成器

> ⚠️ **MCP-API 代码生成工具已独立到专用仓库：**
>
> ### 👉 [https://github.com/PassByYou888/LingoFuse-Tools](https://github.com/PassByYou888/LingoFuse-Tools)
>
> 一份声明 → **几十种目标语言的 API 接口**。声明规范、使用手册、生成器源码与预编译包均以该仓库为准。

**GUI 演示源码**：本仓库 `src\llm_tool_v3_frm.pas`（v3.4）—— 展示 SDK 全部关键用法（多会话、流式、附件、能力发现、结构化输出、一步到位检测器）。

---

## §16 Structured Output 完整指南

> **本章是本文档的核心章节。**
>
> 目标：让 AI 和人类开发者**不用读源码**就能在 Pascal 项目里，通过 LingoFuse 生态拿到**结构化的 JSON 输出**——尤其是**检测器方框标注**。
>
> 📖 **想系统学习 Structured Output 的原理**（从起源、规范、JSON Schema 基础，到后端实现、最佳实践）→ 阅读 [`llm_client_v3_Structured_Output_Learning_Guide.md`](llm_client_v3_Structured_Output_Learning_Guide.md)。
>
> 阅读顺序：
> 1. **§16.1** 什么是 Structured Output
> 2. **§16.2** 四个客户端 API（v3.10 新增 2 个）
> 3. **§16.3** 🎯 **检测器方框标注模板（核心）**
> 4. **§16.4** 从客户端到模型的完整调用链
> 5. **§16.5** 其他常用 JSON Schema 模板
> 6. **§16.6** 常见坑与调试
> 7. **§16.7** 完整可运行 Pascal 示例（一步到位检测器）
> 8. **§16.8** 小结
> 9. **§16.9** GUI 使用流程（v3.4）

---

### §16.1 什么是 Structured Output

**Structured Output（结构化输出）** 是一种让大模型**严格按照调用方提供的 JSON Schema 生成输出**的机制。

**传统提示词方式**：

```
用户：请用 JSON 格式返回检测结果，包含标签和方框。
模型：好的，这是 JSON：{ ... }   ← 可能包含多余文本，或格式错误
```

**Structured Output 方式**：

```
用户：（附带 schema）请检测。
模型：{"detections":[{"label":"person","bbox":[0.1,0.2,0.5,0.6]}]}   ← 严格符合 schema，无多余内容
```

**关键优势**：
- **不再需要事后用正则/JSON.parse 提取**——模型输出的就是合法 JSON。
- **字段名、字段类型、必填字段由 schema 强制**。
- **后端（LM Studio 等）在生成时约束解码**——是"在 token 层面强制"。

**支持链路**：

```
客户端（llm_client_v3）
    ↓ options.response_format
LingoFuse 代理（llm_proxy / llm_proxy_tool）
    ↓ 原样转发
OpenAI 兼容后端（LM Studio / Ollama / vLLM）
    ↓ 强制按 schema 生成
大模型（Qwen2.5-VL / Nemotron Omni / ...）
```

**不支持链路**：

```
llm_client_v3 → llm_service（本地 llama.cpp）
    ❌ 不转发 response_format
```

---

### §16.2 四个客户端 API

SDK 从 v3.10 起提供**四个** Structured Output 入口。**99% 场景用 `GenerateWithImageFileAndSchema`**。

#### 方法 A：`GenerateStructured`（底层、灵活）

```pascal
function TLLMClient.GenerateStructured(
  const AContent, APrompt: string;
  const AResponseFormatJson: string;    // 完整的 response_format JSON
  var ASessionId: string;
  out AError: string): boolean;
```

**作用**：把调用方提供的**完整 `response_format` JSON 字符串**，原样塞进请求的 `options.response_format`。

**适合场景**：需要精细控制（例如使用 `json_object` 而不是 `json_schema`）。

#### 方法 B：`GenerateWithJsonSchema`（纯 schema，无附件）

```pascal
function TLLMClient.GenerateWithJsonSchema(
  const AContent, APrompt: string;
  const ASchemaName: string;
  const ASchemaJson: string;
  const AStrict: boolean;
  var ASessionId: string;
  out AError: string): boolean;
```

**作用**：只需要 schema 的**名字**和**本体**，SDK 自动组装外层 envelope。

**适合场景**：不需要附件（纯文本问答 + 结构化输出）。

#### 方法 C：`GenerateWithImageFileAndSchema`（**v3.10 新增 · 推荐**）

```pascal
function TLLMClient.GenerateWithImageFileAndSchema(
  const AContent, APrompt, AFilePath: string;   // 图片文件路径
  const ASchemaName: string;
  const ASchemaJson: string;
  const AStrict: boolean;
  var ASessionId: string;
  out AError: string): boolean;
```

**作用**：**图片文件 + JSON Schema 一步到位**。

**适合场景**：**检测器场景的唯一推荐入口**。图片 + schema 一次调用完成，SDK 内部自动：
1. 读文件（含空文件检查、base64 膨胀预检）
2. 猜 MIME（基于扩展名）
3. base64 编码（`umlBase64EncodeBytes`）
4. 组装 `response_format` envelope
5. 发送 `generate` 请求（含 attachments + options.response_format）

**调用者不需要手动管理 `attachments` 数组。**

#### 方法 D：`GenerateWithAttachmentsAndSchema`（**v3.10 新增**）

```pascal
function TLLMClient.GenerateWithAttachmentsAndSchema(
  const AContent, APrompt: string;
  const ATexts: TLLMTextAttachmentArray;
  const AImages: TLLMImageAttachmentArray;
  const ASchemaName: string;
  const ASchemaJson: string;
  const AStrict: boolean;
  var ASessionId: string;
  out AError: string): boolean;
```

**作用**：**附件数组 + JSON Schema**。附件和 schema 都完全由调用者控制。

**适合场景**：
- 有多个附件（多个图片 / 多个文本文件）。
- 需要精确控制附件名、MIME。
- 需要附加参考文本（如类别映射表）连同图片一起发送。

**`AStrict` 的意义**（对 B/C/D 三个方法）：
- `True`（推荐）：**严格模式**。模型必须输出完全符合 schema 的 JSON。
- `False`：**宽松模式**。模型可以输出 schema 之外的额外字段。

---

### §16.3 🎯 检测器方框标注模板（核心）

> **本节是全文重点。** 直接复制下面的 JSON 模板，按需微调。

**约定**：
- **坐标格式**：归一化到 `[0, 1]` 的 `[x_min, y_min, x_max, y_max]`
  - `(x_min, y_min)` = 方框左上角
  - `(x_max, y_max)` = 方框右下角
- **为什么用归一化**：不受图像分辨率影响。
- **为什么顺序是 `x,y,x,y`**：与 Pascal 的 `TRect`、OpenCV、PIL 一致。

---

#### 模板 1：基础版（标签 + 方框）—— **最简可用**

```json
{
  "type": "object",
  "properties": {
    "detections": {
      "type": "array",
      "description": "List of detected objects",
      "items": {
        "type": "object",
        "properties": {
          "label": {
            "type": "string",
            "description": "Object class name, e.g. person, car, dog"
          },
          "bbox": {
            "type": "array",
            "description": "Normalized bounding box as [x_min, y_min, x_max, y_max], values in 0~1",
            "items": {
              "type": "number",
              "minimum": 0,
              "maximum": 1
            },
            "minItems": 4,
            "maxItems": 4
          }
        },
        "required": ["label", "bbox"]
      }
    }
  },
  "required": ["detections"]
}
```

**使用场景**：学习、原型、内部工具。

**Pascal 调用**（v3.10 一步到位）：

```pascal
var
  SchemaBody: string;
  S, E: string;
begin
  SchemaBody :=
    '{"type":"object","properties":{' +
    '"detections":{"type":"array","items":{' +
    '"type":"object","properties":{' +
    '"label":{"type":"string"},' +
    '"bbox":{"type":"array","items":{"type":"number","minimum":0,"maximum":1},' +
    '"minItems":4,"maxItems":4}},' +
    '"required":["label","bbox"]}}},' +
    '"required":["detections"]}';

  if not LLM.GenerateWithImageFileAndSchema(
    '检测图片中的所有目标，返回归一化坐标',
    '',
    'test.png',
    'object_detection',
    SchemaBody,
    True,
    S, E) then
    DoStatus('失败: ' + E);
end;
```

**预期输出**：

```json
{
  "detections": [
    {"label": "person", "bbox": [0.12, 0.23, 0.45, 0.78]},
    {"label": "dog",    "bbox": [0.50, 0.30, 0.80, 0.65]}
  ]
}
```

---

#### 模板 2：标准版（标签 + 方框 + 置信度）—— **生产推荐**

```json
{
  "type": "object",
  "properties": {
    "detections": {
      "type": "array",
      "description": "List of detected objects with confidence scores",
      "items": {
        "type": "object",
        "properties": {
          "label": {
            "type": "string",
            "description": "Object class name"
          },
          "bbox": {
            "type": "array",
            "description": "Normalized bounding box [x_min, y_min, x_max, y_max], values in 0~1",
            "items": {
              "type": "number",
              "minimum": 0,
              "maximum": 1
            },
            "minItems": 4,
            "maxItems": 4
          },
          "confidence": {
            "type": "number",
            "description": "Detection confidence, 0.0 to 1.0",
            "minimum": 0,
            "maximum": 1
          }
        },
        "required": ["label", "bbox", "confidence"]
      }
    }
  },
  "required": ["detections"]
}
```

**使用场景**：需要按置信度过滤、排序、可视化的生产环境。

**Pascal 调用**：

```pascal
SchemaBody :=
  '{"type":"object","properties":{' +
  '"detections":{"type":"array","items":{' +
  '"type":"object","properties":{' +
  '"label":{"type":"string"},' +
  '"bbox":{"type":"array","items":{"type":"number","minimum":0,"maximum":1},' +
  '"minItems":4,"maxItems":4},' +
  '"confidence":{"type":"number","minimum":0,"maximum":1}},' +
  '"required":["label","bbox","confidence"]}}},' +
  '"required":["detections"]}';

LLM.GenerateWithImageFileAndSchema('检测图片中的目标', '', 'test.png',
                                   'object_detection', SchemaBody, True, S, E);
```

**预期输出**：

```json
{
  "detections": [
    {"label": "person", "bbox": [0.12, 0.23, 0.45, 0.78], "confidence": 0.95},
    {"label": "dog",    "bbox": [0.50, 0.30, 0.80, 0.65], "confidence": 0.88}
  ]
}
```

---

#### 模板 3：带类别 ID 版（label_id + label + bbox）—— **对接已有检测器时**

```json
{
  "type": "object",
  "properties": {
    "detections": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "label": {
            "type": "string",
            "description": "Human-readable class name"
          },
          "label_id": {
            "type": "integer",
            "description": "Numeric class id (0 = person, 1 = car, 2 = dog, ...)",
            "minimum": 0
          },
          "bbox": {
            "type": "array",
            "description": "Normalized bbox [x_min, y_min, x_max, y_max], values in 0~1",
            "items": {
              "type": "number",
              "minimum": 0,
              "maximum": 1
            },
            "minItems": 4,
            "maxItems": 4
          },
          "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1
          }
        },
        "required": ["label", "label_id", "bbox", "confidence"]
      }
    }
  },
  "required": ["detections"]
}
```

**使用场景**：需要把结果**注入到已有 Pascal 检测器**（使用 `TDetection = record label_id: integer; ...` 结构）时。

**关于 `label_id` 的映射**：
- **模型不知道你的类别体系**——除非你在提示词里明确说明"0 = person, 1 = car, ..."。
- 推荐做法：**在 system message 或 prompt 里列出类别映射**。
- 或者：**只用 `label`（字符串）**，由 Pascal 侧做"字符串 → ID"的映射（更稳健）。

**Pascal 调用**（用 `GenerateWithAttachmentsAndSchema` 附加类别映射文本）：

```pascal
var
  Texts: TLLMTextAttachmentArray;
begin
  SetLength(Texts, 1);
  Texts[0].Name.Text := 'labels.txt';
  Texts[0].Mime.Text := 'text/plain';
  Texts[0].Text.Text := '0=person, 1=car, 2=dog, 3=cat';
  try
    LLM.GenerateWithAttachmentsAndSchema(
      '根据 labels.txt 中的类别映射检测图片',
      '', Texts, nil,     // 也可以同时有 Images
      'object_detection', SchemaBody, True,
      S, E);
  finally
    TLLMClient.ClearTextAttachments(Texts);
  end;
end;
```

---

#### 模板 4：多任务版（检测 + 分类 + 描述）—— **复杂场景**

```json
{
  "type": "object",
  "properties": {
    "summary": {
      "type": "string",
      "description": "One-sentence summary of the image"
    },
    "detections": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "label": {
            "type": "string"
          },
          "bbox": {
            "type": "array",
            "items": {
              "type": "number",
              "minimum": 0,
              "maximum": 1
            },
            "minItems": 4,
            "maxItems": 4
          },
          "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1
          },
          "attributes": {
            "type": "object",
            "description": "Additional attributes for this object",
            "properties": {
              "color": {
                "type": "string"
              },
              "orientation": {
                "type": "string",
                "enum": ["front", "back", "left", "right", "unknown"]
              }
            }
          }
        },
        "required": ["label", "bbox", "confidence"]
      }
    }
  },
  "required": ["summary", "detections"]
}
```

**使用场景**：智能体需要"整体理解 + 局部定位 + 属性提取"。

**注意**：**不要嵌套太深**——某些后端对深层嵌套支持有限。

---

#### 模板 5：分段/区域检测（segmentation-lite）—— **多边形状**

```json
{
  "type": "object",
  "properties": {
    "regions": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "label": {
            "type": "string"
          },
          "polygon": {
            "type": "array",
            "description": "Polygon vertices as normalized [x, y] pairs, clockwise order",
            "items": {
              "type": "array",
              "items": {
                "type": "number",
                "minimum": 0,
                "maximum": 1
              },
              "minItems": 2,
              "maxItems": 2
            },
            "minItems": 3
          }
        },
        "required": ["label", "polygon"]
      }
    }
  },
  "required": ["regions"]
}
```

**使用场景**：需要**非矩形**区域（如人体轮廓、道路边界）时。

**注意**：
- **多边形点数不确定**——`minItems: 3` 只保证至少 3 个点，上不封顶。
- **部分后端对动态数组长度的支持有限**——若报错，降级到矩形 `bbox`。

---

### §16.4 从客户端到模型的完整调用链

一次检测器请求（使用 `GenerateWithImageFileAndSchema`）的**完整链路**：

```
[1] Pascal 客户端
      ↓ LLM.GenerateWithImageFileAndSchema(
      ↓   '检测图片', '', 'photo.png',
      ↓   'object_detection', SchemaBody, True, S, E)
      ↓
[2] llm_client_v3.GenerateWithImageFileAndSchema
      ↓ 步骤 2.1：BuildSchemaResponseFormatJson
      ↓   → response_format = {
      ↓       "type": "json_schema",
      ↓       "json_schema": { "name": "object_detection",
      ↓                        "strict": true,
      ↓                        "schema": <SchemaBody> } }
      ↓ 步骤 2.2：BuildImageAttachmentFromFile
      ↓   → images[0] = { Name: 'photo.png',
      ↓                    Mime: 'image/png',
      ↓                    DataB64: '<base64>' }
      ↓ 步骤 2.3：SendGenerateCombined
      ↓   → 组装 generate 请求：
      ↓     {
      ↓       "content": "检测图片",
      ↓       "prompt": "",
      ↓       "session_id": "...",
      ↓       "attachments": [ { "kind":"image",
      ↓                         "name":"photo.png",
      ↓                         "mime":"image/png",
      ↓                         "data_b64":"..." } ],
      ↓       "options": { "response_format": { ... } }
      ↓     }
      ↓ LF_CallEx('LLM_Service', ...)
      ↓
[3] llm_proxy / llm_proxy_tool
      ↓ sanitize_options 保留 options.response_format
      ↓ 通过 OpenAIStreamClient.stream_chat 转发
      ↓
[4] LM Studio（或 Ollama / vLLM）
      ↓ POST /v1/chat/completions
      ↓ {
      ↓   "model": "qwen2-vl-7b-instruct",
      ↓   "messages": [ { "role":"user",
      ↓                   "content":[ {"type":"text",...},
      ↓                               {"type":"image_url",...} ] } ],
      ↓   "stream": true,
      ↓   "response_format": { ... }    ← 原样转发
      ↓ }
      ↓
[5] 大模型（Qwen2.5-VL 等）
      ↓ 后端在 token 层面约束解码
      ↓ 只生成符合 schema 的 token
      ↓
[6] SSE 流回传
      ↓ data: {"choices":[{"delta":{"content":"{\"detections\":"}}]}
      ↓ data: {"choices":[{"delta":{"content":"[{\"label\":\"person\""}}]}
      ↓ ...
      ↓ data: [DONE]
      ↓
[7] llm_proxy / llm_proxy_tool
      ↓ 提取 choices[0].delta.content，累加为完整字符串
      ↓ 通过 llm_stream 逐片推送 chunk 事件
      ↓
[8] llm_client_v3
      ↓ OnChunk 触发多次，累积完整 JSON
      ↓ OnFinish 触发，reason='stop'
      ↓
[9] Pascal 客户端
      ↓ 收到完整 JSON 字符串
      ↓ 用 TZ_JsonObject.ParseText 解析
      ↓ 提取 detections 数组
```

**关键点**：
- **模型输出的 `content` 是 JSON 字符串**，不是对象。
- **回调是流式的**——`OnChunk` 会被触发多次，需要客户端**累加**。
- **`OnFinish` 时才是完整 JSON**。

**Pascal 侧接收模板**：

```pascal
type
  TForm1 = class(TForm)
  private
    FClient: TLLMClient;
    FAccum: TStringBuilder;
    procedure OnLLMChunk(const SessionId, Text: string);
    procedure OnLLMFinish(const SessionId, Reason: string);
  end;

procedure TForm1.OnLLMChunk(const SessionId, Text: string);
begin
  TThread.Queue(nil,
    procedure
    begin
      FAccum.Append(Text);
      Memo1.Lines.Add(Text);
    end);
end;

procedure TForm1.OnLLMFinish(const SessionId, Reason: string);
var
  FullJson: string;
begin
  TThread.Queue(nil,
    procedure
    var
      J: TZ_JsonObject;
      D: TZ_JsonArray;
      I: integer;
      L: string;
      B: TZ_JsonArray;
    begin
      FullJson := FAccum.ToString;
      FAccum.Clear;

      J := TZ_JsonObject.Create;
      try
        if not J.ParseText(FullJson) then
        begin
          Memo1.Lines.Add('[ERROR] Invalid JSON returned');
          Exit;
        end;

        D := J.a['detections'];
        Memo1.Lines.Add('检测到 ' + IntToStr(D.Count) + ' 个目标：');
        for I := 0 to D.Count - 1 do
        begin
          L := D.O[I].S['label'];
          B := D.O[I].a['bbox'];
          Memo1.Lines.Add(
            Format('  %s  bbox=[%.3f, %.3f, %.3f, %.3f]',
              [L, B.F[0], B.F[1], B.F[2], B.F[3]]));
        end;
      finally
        J.Free;
      end;
    end);
end;
```

---

### §16.5 其他常用 JSON Schema 模板

#### 模板 A：单标签分类

```json
{
  "type": "object",
  "properties": {
    "category": {
      "type": "string",
      "enum": ["cat", "dog", "bird", "other"]
    },
    "confidence": {
      "type": "number",
      "minimum": 0,
      "maximum": 1
    }
  },
  "required": ["category", "confidence"]
}
```

**用途**：图像分类、意图识别。

---

#### 模板 B：OCR 文字提取

```json
{
  "type": "object",
  "properties": {
    "texts": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "content": {
            "type": "string"
          },
          "bbox": {
            "type": "array",
            "items": {
              "type": "number",
              "minimum": 0,
              "maximum": 1
            },
            "minItems": 4,
            "maxItems": 4
          }
        },
        "required": ["content", "bbox"]
      }
    }
  },
  "required": ["texts"]
}
```

---

#### 模板 C：关键点检测

```json
{
  "type": "object",
  "properties": {
    "keypoints": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "name": { "type": "string" },
          "x": { "type": "number", "minimum": 0, "maximum": 1 },
          "y": { "type": "number", "minimum": 0, "maximum": 1 },
          "visibility": { "type": "number", "minimum": 0, "maximum": 1 }
        },
        "required": ["name", "x", "y", "visibility"]
      }
    }
  },
  "required": ["keypoints"]
}
```

**用途**：人体姿态、面部关键点、UI 元素定位。

---

#### 模板 D：表格结构化

```json
{
  "type": "object",
  "properties": {
    "headers": {
      "type": "array",
      "items": { "type": "string" }
    },
    "rows": {
      "type": "array",
      "items": {
        "type": "array",
        "items": { "type": "string" }
      }
    }
  },
  "required": ["headers", "rows"]
}
```

**用途**：表格图像转结构化数据。

---

#### 模板 E：纯 `json_object`（无 schema）

如果你只需要"合法 JSON"，不约束字段：

```pascal
var
  RF: string;
begin
  RF := '{"type":"json_object"}';
  LLM.GenerateStructured('提取图片中的所有信息', '', RF, S, E);
end;
```

**注意**：
- **不推荐用于检测器**——模型可能返回任意 JSON 结构。
- **LM Studio 有已知限制**：使用 `json_object` 时**必须**在 prompt 里提到"JSON"关键字，否则会报错。

---

### §16.6 常见坑与调试

#### 坑 1：`llm_service` 不支持 Structured Output

**症状**：调用成功返回 `code: 0`，但模型回复是**自由文本**，不是 JSON。

**根因**：`llm_service` 本地推理路径**不转发** `options.response_format`。

**修复**：

```pascal
if LLM.ServerKind = 'service' then
begin
  ShowMessage('llm_service 不支持 Structured Output');
  ShowMessage('请启动 llm_proxy 或 llm_proxy_tool');
  Exit;
end;
```

---

#### 坑 2：`json_schema` 嵌套了两层

**症状**：后端返回 400，错误信息含 `unrecognized type json_schema`。

**错误结构**：

```json
{
  "type": "json_schema",
  "json_schema": {
    "json_schema": {    ← ❌ 多了一层
      "name": "...",
      "schema": { ... }
    }
  }
}
```

**正确结构**：

```json
{
  "type": "json_schema",
  "json_schema": {
    "name": "...",
    "strict": true,
    "schema": { ... }
  }
}
```

**修复**：**用 `GenerateWithImageFileAndSchema`** 或 **`GenerateWithJsonSchema`**，SDK 自动组装正确的结构。

---

#### 坑 3：`strict: "true"` 用了字符串

**症状**：后端 400 或静默忽略 schema。

**修复**：用 `GenerateWithImageFileAndSchema` / `GenerateWithJsonSchema`，`AStrict: boolean` 类型天然防错。

---

#### 坑 4：坐标格式与模型不匹配

**症状**：JSON 合法，但 bbox 坐标位置和实际不符。

**可能原因**：
- **坐标顺序错误**：某些模型期望 `[y1, x1, y2, x2]`。
- **归一化方式错误**：某些模型用**像素坐标**（`0~1920`）。
- **图像预处理差异**。

**修复**：
1. **在提示词里明确说明**：
   ```
   请返回归一化坐标（0~1），格式为 [x_min, y_min, x_max, y_max]，
   其中 (x_min, y_min) 是左上角，(x_max, y_max) 是右下角。
   ```
2. **先做小测试**：用一张只有一个明显物体的图，验证坐标。
3. **Schema 里加约束**：
   ```json
   "bbox": {
     "type": "array",
     "items": { "type": "number", "minimum": 0, "maximum": 1 },
     "minItems": 4,
     "maxItems": 4
   }
   ```

---

#### 坑 5：模型输出缺少 `detections` 字段

**症状**：JSON 合法，但没有 `detections` 字段。

**修复**：
1. **检查 `required`**：确保 schema 里有 `"required": ["detections"]`。
2. **在提示词里加强**：`"请始终返回 detections 字段，即使为空也要返回 []"`。

---

#### 坑 6：`confidence` 超出 `0~1`

**症状**：模型返回 `confidence: 95`，不是 `0.95`。

**修复**：
1. **在提示词里明确**：`"confidence 必须是 0.0 ~ 1.0 之间的小数"`
2. **Pascal 侧做归一化**：
   ```pascal
   var Conf: double;
   begin
     Conf := D.O[I].F['confidence'];
     if Conf > 1 then Conf := Conf / 100;
   end;
   ```

---

#### 坑 7：后端不支持 Structured Outputs

**症状**：请求发出后，后端返回 400，或忽略 `response_format` 并返回纯文本。

**修复**：
1. **后端版本**：LM Studio 需要 0.3.0+；Ollama 需要 0.3.0+。
2. **模型支持**：使用 Qwen2.5 系列、Qwen2.5-VL 系列、Nemotron Omni 等。
3. **手工验证**：用 `curl` 直接向后端发一个带 `response_format` 的请求。

---

#### 调试技巧：打开代理层 DEBUG 日志

**启动 llm_proxy 时**：

```powershell
.\llm_proxy.exe `
  --backend-url http://127.0.0.1:1234/v1 `
  --backend-model "qwen2-vl-7b-instruct" `
  --vision `
  --log-level DEBUG
```

**观察输出**：

```
[DEBUG] Backend request: url=... model=... msgs=N tools=no response_format=yes
```

- **`response_format=yes`** → 代理确实转发了。
- **`response_format=no`** → 检查客户端是否真的传了 `options.response_format`。

---

### §16.7 完整可运行 Pascal 示例（一步到位检测器）

> **前置条件**：
> 1. LM Studio 加载 **Qwen2.5-VL-7B** 或 **Nemotron Omni + mmproj**。
> 2. LM Studio 开启本地服务器（默认端口 `1234`）。
> 3. 启动 `llm_proxy.exe --backend-url http://127.0.0.1:1234/v1 --backend-model "qwen2-vl-7b-instruct" --vision`。

```pascal
unit detector_demo_frm;

interface

uses
  Classes, SysUtils, Forms, Controls, StdCtrls, ExtCtrls,
  llm_client_v3, lingofuse_import,
  Z.Core, Z.Json, Z.PascalStrings, Z.UPascalStrings;

type
  TDetectorDemoForm = class(TForm)
    Memo1: TMemo;
    BtnConnect: TButton;
    BtnDetect: TButton;
    OpenDialog1: TOpenDialog;
    procedure BtnConnectClick(Sender: TObject);
    procedure BtnDetectClick(Sender: TObject);
    procedure FormClose(Sender: TObject; var Action: TCloseAction);
  private
    FClient: TLLMClient;
    FAccum: TStringBuilder;
    procedure OnLLMChunk(const SessionId, Text: string);
    procedure OnLLMFinish(const SessionId, Reason: string);
    procedure OnLLMError(const SessionId, ErrorMsg: string);
  end;

var
  DetectorDemoForm: TDetectorDemoForm;

implementation

{$R *.lfm}

const
  (* Detector schema body, mirroring the "standard template" in §16.3. *)
  DETECTOR_SCHEMA_BODY =
    '{"type":"object","properties":{' +
    '"detections":{"type":"array","items":{' +
    '"type":"object","properties":{' +
    '"label":{"type":"string","description":"Object class name"},' +
    '"bbox":{"type":"array","description":"Normalized bbox [x_min,y_min,x_max,y_max], 0~1",' +
    '"items":{"type":"number","minimum":0,"maximum":1},' +
    '"minItems":4,"maxItems":4},' +
    '"confidence":{"type":"number","minimum":0,"maximum":1}},' +
    '"required":["label","bbox","confidence"]}}},' +
    '"required":["detections"]}';

procedure TDetectorDemoForm.FormCreate(Sender: TObject);
begin
  FAccum := TStringBuilder.Create;
end;

procedure TDetectorDemoForm.FormClose(Sender: TObject;
  var Action: TCloseAction);
begin
  FAccum.Free;
  if FClient <> nil then
  begin
    FClient.Free;
    FClient := nil;
  end;
  LF_Shutdown;
end;

procedure TDetectorDemoForm.BtnConnectClick(Sender: TObject);
var
  E: string;
begin
  if FClient <> nil then
  begin
    Memo1.Lines.Add('[INFO] Already connected');
    Exit;
  end;

  FClient := TLLMClient.Create('LLM_Service', 'ipc:llm_service', 60000);
  FClient.OnChunk  := OnLLMChunk;
  FClient.OnFinish := OnLLMFinish;
  FClient.OnError  := OnLLMError;

  if not FClient.Connect(E) then
  begin
    Memo1.Lines.Add('[ERROR] Connect failed: ' + E);
    FreeAndNil(FClient);
    Exit;
  end;

  Memo1.Lines.Add('[INFO] Connected. ServerKind=' + FClient.ServerKind);

  if FClient.ServerKind = 'service' then
  begin
    Memo1.Lines.Add('[WARN] llm_service does not support Structured Output.');
    Memo1.Lines.Add('[WARN] Please use llm_proxy or llm_proxy_tool instead.');
  end;
end;

procedure TDetectorDemoForm.BtnDetectClick(Sender: TObject);
var
  S, E: string;
begin
  if FClient = nil then
  begin
    Memo1.Lines.Add('[ERROR] Not connected');
    Exit;
  end;

  if not OpenDialog1.Execute then
    Exit;

  Memo1.Lines.Clear;
  Memo1.Lines.Add('[INFO] Detecting: ' + OpenDialog1.FileName);
  FAccum.Clear;

  S := '';

  { ✅ v3.10 一步到位：图片 + schema 一起发送 }
  if not FClient.GenerateWithImageFileAndSchema(
    '检测图片中的所有目标。返回归一化坐标（0~1），格式为 [x_min, y_min, x_max, y_max]。',
    '',
    OpenDialog1.FileName,
    'object_detection',
    DETECTOR_SCHEMA_BODY,
    True,      // strict
    S, E) then
  begin
    Memo1.Lines.Add('[ERROR] GenerateWithImageFileAndSchema failed: ' + E);
    Exit;
  end;

  Memo1.Lines.Add('[INFO] Request queued, session=' + S);
end;

procedure TDetectorDemoForm.OnLLMChunk(const SessionId, Text: string);
begin
  TThread.Queue(nil,
    procedure
    begin
      FAccum.Append(Text);
      Memo1.Lines.Add(Text);
    end);
end;

procedure TDetectorDemoForm.OnLLMFinish(const SessionId, Reason: string);
var
  FullJson: string;
begin
  TThread.Queue(nil,
    procedure
    var
      J: TZ_JsonObject;
      D: TZ_JsonArray;
      I: integer;
      L: string;
      B: TZ_JsonArray;
    begin
      FullJson := FAccum.ToString;
      FAccum.Clear;

      Memo1.Lines.Add('');
      Memo1.Lines.Add('--- finish: ' + Reason + ' ---');

      J := TZ_JsonObject.Create;
      try
        if not J.ParseText(FullJson) then
        begin
          Memo1.Lines.Add('[ERROR] Invalid JSON: ' + FullJson);
          Exit;
        end;

        if not J.Exists('detections') then
        begin
          Memo1.Lines.Add('[WARN] No detections field');
          Exit;
        end;

        D := J.a['detections'];
        Memo1.Lines.Add('Detected ' + IntToStr(D.Count) + ' object(s):');

        for I := 0 to D.Count - 1 do
        begin
          L := D.O[I].S['label'];
          B := D.O[I].a['bbox'];
          Memo1.Lines.Add(
            Format('  #%d  %s  bbox=[%.4f, %.4f, %.4f, %.4f]  conf=%.4f',
              [I + 1, L,
               B.F[0], B.F[1], B.F[2], B.F[3],
               D.O[I].F['confidence']]));
        end;
      finally
        J.Free;
      end;
    end);
end;

procedure TDetectorDemoForm.OnLLMError(const SessionId, ErrorMsg: string);
begin
  TThread.Queue(nil,
    procedure
    begin
      Memo1.Lines.Add('[ERROR] ' + ErrorMsg);
    end);
end;

end.
```

**关键变化（相比 v2.2 文档）**：
- **不再需要手工构造 attachments 数组**——`GenerateWithImageFileAndSchema` 内部搞定。
- **不再需要手工调用两次 API**（一次发图，一次发 schema）。
- **代码量减少约 60%**。

---

### §16.8 小结

| 你的目标 | 用哪个模板 | 用哪个 API（v3.10） |
|---------|-----------|-----------|
| 最简检测器 | 模板 1 | **`GenerateWithImageFileAndSchema`** |
| **生产检测器（推荐）** | **模板 2** | **`GenerateWithImageFileAndSchema`** |
| 对接已有检测器 | 模板 3 | `GenerateWithAttachmentsAndSchema`（附加类别映射文本）|
| 多任务（检测 + 描述） | 模板 4 | `GenerateWithImageFileAndSchema` |
| 多边形状区域 | 模板 5 | `GenerateWithImageFileAndSchema` |
| 分类 | 模板 A | `GenerateWithImageFileAndSchema` |
| OCR | 模板 B | `GenerateWithImageFileAndSchema` |
| 关键点 | 模板 C | `GenerateWithImageFileAndSchema` |
| 表格 | 模板 D | `GenerateWithImageFileAndSchema` |
| **多附件 + schema** | 模板 3 | **`GenerateWithAttachmentsAndSchema`** |
| **纯 schema 无附件** | 模板 1-5 | `GenerateWithJsonSchema` |
| 完全自由 JSON | — | `GenerateStructured` + `{"type":"json_object"}` |

**四条铁律**：
1. **`json_schema` 直接放在顶层，不要再嵌 `json_schema`**。
2. **`strict` 是布尔值，不是字符串**。
3. **`llm_service` 不支持，必须用 `llm_proxy` / LTB**。
4. **v3.10 一步到位**：图片 + schema 用 `GenerateWithImageFileAndSchema`，不要手动组合。

---

### §16.9 GUI 使用流程（v3.4）

> `llm_tool_v3.exe`（GUI 演示）已内置 Structured Output 面板。

**步骤**：

1. **连接**：
   - 切到 `LLM参数` 页
   - 填端点 `ipc:llm_service`（默认）+ APP `LLM_Service`（默认）
   - 点"链接端点"

2. **准备检测器**：
   - 切到 `结构化输出` 页
   - 点"加载检测器模板"
   - `SchemaMemo` 会自动填入标准检测器 schema（标签 + 方框 + 置信度）
   - 勾选"启用 Structured Output"
   - 保持 `strict 严格模式` 勾选

3. **添加图片**：
   - 切到 `输入` 页
   - 在附件面板点"添加图片文件..."（或"从剪贴板粘贴"）
   - 图片会被加入 `FImageAttachments` 数组

4. **写提示词**：
   - `code_edit`（顶部）：`检测图片中的所有目标。返回归一化坐标（0~1）。`
   - `prompt_edit`（中部）：可写更详细说明

5. **生成**：
   - 点"生成"（或"新建会话"）

**底层自动分流**（v3.4 `DoGenerateWithCurrentSettings`）：
- **有 schema + 有附件** → `GenerateWithAttachmentsAndSchema`
- 有 schema + 无附件 → `GenerateWithJsonSchema`
- 无 schema + 有附件 → `GenerateWithAttachments`
- 无 schema + 无附件 → `Generate`

**`结构化输出` 面板控件对照**：

| 控件 | 作用 |
|------|------|
| `EnableStructuredOutputCheckBox` | 总开关 |
| `SchemaNameEdit` | schema 名（默认 `object_detection`） |
| `StrictCheckBox` | strict 严格模式（默认勾选） |
| `LoadDetectorTemplateButton` | 一键加载内置检测器模板 |
| `SchemaMemo` | schema 本体编辑区 |

---

**文档版本**：v2.4（Structured Output 组合扩展版 · 目录对齐版——移除对已删除文档 `LingoFuse_LLM_Pitfalls_For_AI.md` 的引用；§4 / §7 / §8 / §11 中的 Pitfalls 引用改为指向 [`LingoFuse_Pascal_Complete_Guide.md`](LingoFuse_Pascal_Complete_Guide.md) 中等价条目；新增 [`llm_client_v3_Structured_Output_Learning_Guide.md`](llm_client_v3_Structured_Output_Learning_Guide.md) 与 [`QUICK_START_LLM_STACK.md`](QUICK_START_LLM_STACK.md) 引用；MCP-API 生成器统一指向 [LingoFuse-Tools](https://github.com/PassByYou888/LingoFuse-Tools)；对齐实际仓库文档清单）

**维护方式**：发现新的错误消息、新坑、新模板，追加到对应章节
**核心承诺**：AI 读完本文档能独立完成 95% 的 `llm_client_v3` 任务（含检测器一步到位），剩下 5% 见 §13 诚实清单