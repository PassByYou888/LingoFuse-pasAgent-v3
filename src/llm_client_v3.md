# llm_client_v3.md

> **用途**：让 AI 能在不读 `llm_client_v3.pas` 源码的情况下，独立完成以下任务：
> - 写出正确的调用代码
> - 定位并修复 bug
> - 添加新 API
> - 修改现有 API
> - 判断改动影响范围
>
> **使用方式**：
> - 要**写代码** → §1 + §2 + §3
> - 要**查 bug** → §4（错误消息原文索引）
> - 要**升级** → §5（模板）
> - 要**深入理解** → §6 + §7
> - 遇到 §8 的场景 → 停止，回查源码
>
> **可信度标记**：`[源码]` = 逐行核对过 `.pas`；`[协议]` = 从服务端契约推断；`[教训]` = 来自实际踩坑；`[未核实]` = 需回查源码。
>
> **文档版本**：v2.1（结构性重构版 · 能力边界修正版）
> **最后更新**：2026-09-17
> **SDK 源码位置**：本仓库 `src\llm_client_v3.pas`
> **GUI 演示**：本仓库 `src\llm_tool_v3.lpi`
> **相关文档**：
> - [`../Pascal_Integration_Guide.md`](../Pascal_Integration_Guide.md) — Pascal 开发者切入指南
> - [`LingoFuse_LLM_Ecosystem_User_Guide.md`](LingoFuse_LLM_Ecosystem_User_Guide.md) — 生态总览
> - [`LingoFuse_LLM_Pitfalls_For_AI.md`](LingoFuse_LLM_Pitfalls_For_AI.md) — 踩坑大全

---

## ⚠️ 阅读前必读：能力边界

**在阅读本文之前，请先理解以下三条关键事实**：

| # | 事实 | 说明 |
|:-:|------|------|
| 1 | **SDK 同时兼容 Delphi 7+ 和 FPC 3.0+** | 通过条件编译指令统一两端 |
| 2 | **SDK 支持三种服务端** | `llm_service` / `llm_proxy` / `llm_proxy_tool`（LTB）——通过 `ServerKind` 与能力矩阵区分 |
| 3 | **`GenerateWithImageFile` 等图片 API 只对代理服务端有效** | `llm_service` 是纯文本服务端，**收到图片附件会拒绝**（返回 `code: -1`）。图片要经 `llm_proxy` / LTB 转发到 VLM 后端。 |

> **关键区分**：
> - **客户端 API 无差异**——SDK 本身对三种服务端使用**相同的 API**。
> - **服务端能力有差异**——`set_system_message` 只有 `llm_service` 支持；工具只有 LTB 支持；多模态转发只有 `llm_proxy` / LTB 支持。
> - **客户端必须做能力发现**——通过 `LLMSupported` / `HasVision` 等接口查询服务端能力，**不要硬编码假设**。

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

### 1.3 生成（5 个变体）

| 方法 | 参数 | 副作用 | 附加字段 |
|------|------|--------|---------|
| `Generate` | `(AContent, APrompt: string; var ASessionId: string; out AError: string)` | **更新 `FCurrentSessionId`** | `{content, prompt, session_id?}` 或 `{content, prompt, client_name}` |
| `GenerateWithAttachments` | `(+ ATexts, AImages; var ASessionId; out AError)` | 同上 | 附加 `attachments:[...]` |
| `GenerateWithTextFile` | `(AContent, APrompt, AFilePath: string; var ASessionId; out AError)` | 同上 | 读文件→构造文本附件 |
| `GenerateWithImageFile` | `(AContent, APrompt, AFilePath: string; var ASessionId; out AError)` | 同上 | 读文件→base64→构造图片附件 |
| `GenerateCurrent` | `(AContent, APrompt: string; out AError: string)` | **不改变 `FCurrentSessionId`** | 用 `FCurrentSessionId`（可能是空） |

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

### 4.2 通用调用相关

| 错误消息原文 | 抛出位置 | 根因 | 修复 |
|--------------|----------|------|------|
| `Not connected to LingoFuse service` | `CallAPI` / `Generate` / `GenerateWithAttachments` | `FConnected = False` | 先调 `Connect` 并检查返回值 |
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

### 4.3 会话相关

| 错误消息原文 | 抛出位置 | 根因 | 修复 |
|--------------|----------|------|------|
| `CloseSession: empty session_id` | `CloseSession` | 传入空 `ASessionId` | 检查参数 |
| `CancelSession: empty session_id` | `CancelSession` | 传入空 `ASessionId` | 检查参数 |
| `Server did not return session_id` | `CreateSession` / `Generate` / `GenerateWithAttachments` | 服务端 `code:0` 但无 `session_id` | 检查服务端版本 |

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
| `Cannot open image file "X": ...` | `GenerateWithImageFile` | 文件不存在或无权限 | 检查路径 |
| `Image file "X" is empty (0 bytes).` | `GenerateWithImageFile` | 空文件 | 检查源文件 |

### 4.6 服务端拒绝相关（**v3 新增**）

| 错误消息原文 | 抛出位置 | 根因 | 修复 |
|--------------|----------|------|------|
| `Image attachments are not supported by this server: --vision is disabled` | 服务端返回（`code: -1`） | `llm_proxy` / LTB 未传 `--vision` | 服务端启动时加 `--vision` |
| `Image attachments are not supported by llm_service in this revision` | 服务端返回（`code: -1`） | 目标服务端是 `llm_service` | 改用 `llm_proxy` / LTB |
| `set_system_message is not supported by llm_proxy` | 服务端返回（`code: -1`） | 目标服务端是代理 | 改用 `CreateSession(system_message)` |

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

### 5.3 添加新事件类型

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

### 5.4 加新的"改 A 必须同步改 B"规则

**当修改以下内容时，必须检查的关联点**：

| 改了 A | 必须检查 B |
|--------|-----------|
| `Connect` 的 `LF_*` 调用顺序 | `FPrepared := True` 之前/之后的位置 |
| `FTimeout` 语义 | 所有 `CallAPI` 里的 `uint64(FTimeout)` |
| `FCapabilities` 的 JSON 结构 | `LLMSupported` / `IsToolBridge` / `HasVision` / `HasAttachments` |
| `FCurrentSessionId` 语义 | `CreateSession` / `CloseSession` / `Generate` / `GenerateWithAttachments` / `GenerateCurrent` |
| 附件字段类型 | `ClearTextAttachments` / `ClearImageAttachments` / `PopulateAttachmentArray` |
| 事件类型定义 | `HandleLLMNotify` 的分派 + `DoXxx` 方法 + 属性 |
| `SafeParseJson` 的返回契约 | 所有调用点（**失败时 `AJson` 必须是 nil**） |
| `CheckResponseCode` 的语义 | 所有调用点（**第二个参数是 API 名**） |

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

---

## §7 线程模型

### 7.1 线程归属表

| 方法/回调 | 执行线程 | 备注 |
|-----------|---------|------|
| `Create` / `Destroy` | 调用者 | |
| `Connect` / `Disconnect` | 调用者 | |
| 所有 Call API（`Generate` / `CreateSession` / ...） | 调用者 | **同步阻塞** |
| `OnChunk` / `OnThink` / `OnFinish` / `OnError` / `OnClosed` | **LingoFuse 通知线程** | **不是主线程** |
| `LastException` 属性读取 | 任意线程 | `TAtomString` 内部有锁 |

### 7.2 通知线程约束（**核心规则**）

1. **回调中不能调用任何 Call API** [源码]：会死锁（回调线程持有 LingoFuse 内部锁）。
2. **回调中不能直接操作 UI** [教训]：VCL/LCL 非线程安全。
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

**验证**（在测试进程里做）：
```pascal
procedure TForm1.OnLLMChunk(const SessionId, Text: string);
var
  S, E: string;
begin
  FClient.Generate('follow-up', '', S, E);   // ← 挂死
end;
```

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

**验证**：
```pascal
var
  Empty: TBytes;
  Jo: TZ_JsonObject;
begin
  Jo := TZ_JsonObject.Create;
  try
    Jo.Parae(Empty);   // ← 调试模式崩溃
  finally
    Jo.Free;
  end;
end;
```

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

**含义**：跨语言协议的关键。移植到其他语言必须复现。

### 8.5 `LF_ReadStringBytes` 的 fault-tolerant

**验证**：
```pascal
// 服务端发不含 NUL 的 JSON
var
  Data: TBytes;
begin
  Data := Client.LF_ReadStringBytes(InputHnd);
  // 即使没有 NUL，Data 也会包含全部剩余字节
end;
```

**含义**：HTTP 桥接（`bridge.py`）发来的 JSON 通常无 NUL，能正常读。

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

**含义**：调用后**不能再用 `Src`**。

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

**反例**（v3.8 早期版本我犯过的错）：
```pascal
// ❌ 多余中转
var
  Js: TZ_JsonString;
begin
  Js.Bytes := ARawBytes;   // 多一次 UTF-8 解码
  AJson.ParseText(Js);     // 绕路
end;

// ✅ 直接
AJson.Parae(ARawBytes);
```

### 8.9 回调异常的静默

**症状**：`OnChunk` 里抛异常，UI 无任何提示。

**验证**：
```pascal
procedure TForm1.OnLLMChunk(const SessionId, Text: string);
begin
  raise Exception.Create('boom');   // ← 被 HandleLLMNotify 吞掉
end;
```

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

**调试方法**：临时在 `HandleLLMNotify` 每个 `Exit` 前加 `OutputDebugString`。

### 8.11 **图片附件被服务端拒绝**（**v3 新增**）

**症状**：调用 `GenerateWithImageFile` 或 `GenerateWithAttachments`（含图片），返回 `code: -1`。

**可能原因**（三种）：

1. **连接的是 `llm_service`**：服务端**不支持多模态**，明确拒绝。
2. **连接的是 `llm_proxy` / LTB，但服务端未传 `--vision`**：默认拒绝图片附件。
3. **图片大小超限**：单图 base64 超 8 MB，或累计超 16 MB。

**修复**：

```pascal
// ✅ 调用前做能力发现
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

// 检查单文件大小
if Length(ImageB64) > ATTACHMENT_MAX_IMAGE_B64_PER_FILE then
begin
  ShowMessage('图片过大，请压缩后重试');
  Exit;
end;

// 再调用
Client.GenerateWithImageFile(...);
```

**根因**：
- `llm_service` 是纯文本服务端（`vision: 0`，且主动拒绝）。
- `llm_proxy` / LTB 默认 `--vision` 关闭，需显式开启。

**相关文档**：
- [`LingoFuse_LLM_Pitfalls_For_AI.md`](LingoFuse_LLM_Pitfalls_For_AI.md) 中 P8-1 / P8-3
- [`LingoFuse_LLM_Proxy_CLI_Guide.md`](LingoFuse_LLM_Proxy_CLI_Guide.md) 第 5.5 节
- [`LingoFuse_LLM_Proxy_Tool_CLI_Guide.md`](LingoFuse_LLM_Proxy_Tool_CLI_Guide.md) 第 4.5 节

---

## §9 服务端能力差异

### 9.1 三种服务端

| 服务端 | `server_kind` | 特征 | 多模态 |
|--------|---------------|------|:------:|
| `llm_service.py` | `'service'` | 本地模型，含 `set_system_message`；**纯文本** | ❌ **不支持** |
| `llm_proxy.py` | `'proxy'` | 无状态转发；**图片由后端决定** | ✅ **转发**（需 `--vision`） |
| `llm_proxy_tool.py` | `'proxy'` | 无状态转发 + **服务端工具执行**；**图片由后端决定** | ✅ **转发**（需 `--vision`） |

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
| `attachments` | 服务端是否**接受** `attachments` 字段 | 1 = 接受（不会因附件存在就拒绝） |
| `vision` | 服务端**自身是否做视觉处理** | **0 在所有服务端都成立**——服务端只转发 |
| `tools` / `tool_calls` / `tool_results` | 服务端是否支持工具相关行为 | 只有 LTB 为 1 |
| `set_system_message` | 服务端是否支持切换全局 system message | 只有 `llm_service` 为 1 |

> **`vision=0` 的坑**：它**不表示**"链路不支持多模态"——它只表示"**服务端自身不做视觉处理**"。图片能否被理解，由**后端**决定。
>
> **客户端判断"能否发图片"的正确逻辑**：
> 1. 检查 `ServerKind`——是 `'service'` 则**不能发图片**。
> 2. 检查 `attachments`——为 1 时服务端**接受**附件字段。
> 3. 实际调用后，观察是否返回 `code: -1`——据此判断后端是否支持。

### 9.3 `set_system_message` 的行为差异

| 服务端 | 直接调用 | 能力已知后调用 |
|--------|----------|----------------|
| `service` | 走网络 | 走网络（**服务端支持**） |
| `proxy` / `proxy_tool` | 走网络 → 服务端拒绝 | **本地短路**（不发网络） |

**`llm_service` 支持 `set_system_message` 的原因**：它拥有进程内的 `_system_message` 字段，`set_system_message` 修改的是"新会话的默认值"。这是无状态代理无法实现的。

---

## §10 版本迁移

### 10.1 v3.6 → v3.7：清理 6 个辅助函数

**v3.6 的错误**：误以为 `lingofuse_import` 只提供原始 C ABI（`LF_CreateData(pansichar)` 等），手写了 6 个 UTF-8 转换辅助函数。

**v3.7 的修正**：发现 `lingofuse_import` 已有 `*Ex` 系列，删除全部 6 个辅助。净减 15% 代码。

**教训**：**看到 `Xxx` 和 `XxxEx` 成对存在，优先用 `XxxEx`**。

### 10.2 v3.7 → v3.8：破坏性 API 变更

| 变更 | 迁移动作 |
|------|---------|
| `CapabilitiesRawJson: string` → `TZ_JsonString` | 调用方 `S := Client.CapabilitiesRawJson` 改为 `S := Client.CapabilitiesRawJson.Text` |
| `GetAPICapabilities` 参数 `var string` → `var TZ_JsonString` | 调用方 `var S: string` 改为 `var S: TZ_JsonString`；取文本用 `.Text` |
| 附件字段 `string` → `TZ_JsonString` | 调用方 `Att.Name := 'x'` 改为 `Att.Name.Text := 'x'`（或保留，隐式转换） |

**`TZ_JsonString` 的访问方式**：

```pascal
var
  Js: TZ_JsonString;
begin
  Js.Text := 'hello';                   // 写入（文本）
  WriteLn(Js.Text);                     // 读取（文本）
  Js.Bytes := TEncoding.UTF8.GetBytes('hello');   // 写入（原始字节）
  WriteLn(Length(Js.Bytes));            // 读取字节长度
end;
```

### 10.3 v3.8 早期 → final：`SafeParseJson` 直连 `Parae`

**早期错误**：引入 `TZ_JsonString` 中转。

**修正**：直接 `AJson.Parae(ARawBytes)`。

**教训**：**注释与代码不一致立刻审查**——早期版本的注释写"guard before Parae()"，代码却用了 `ParseText`。

### 10.4 v2.0 → v2.1：文档侧修正

**本次修正**（仅文档，不改 SDK 源码）：

- 修正文档标题与源文件引用（`llm_client.pas` → `llm_client_v3.pas`）。
- 修正构造示例参数（`'llm_service', 'ipc:llm'` → `'LLM_Service', 'ipc:llm_service'`）。
- 补充能力矩阵中 `vision=0` 的语义说明。
- 补充图片附件被拒绝的排查章节（§8.11）。
- 补充能力发现示例（§3.4）。

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

**违反顺序的后果**：
- 步骤 6 提前 → 生成的名称缺少隧道信息
- 步骤 5 提前 → 早期失败会误调 `LF_ExitMainThread`

### 11.2 修改 `FTimeout`

**受影响的调用**：
- `CallAPI` 里的 `LF_CallEx(FServerApp, hnd, uint64(FTimeout))`

**检查点**：
- `FTimeout` 是 `integer`，转为 `uint64` 前**不能为负**（构造参数默认 10000）
- 值是毫秒

### 11.3 修改 `FCurrentSessionId` 语义

**受影响的调用**：
- `CreateSession`：成功后更新
- `CloseSession`：如果匹配则清空
- `Generate` / `GenerateWithAttachments`：如果 `ASessionId=''` 用它
- `GenerateCurrent`：读它（不写）

### 11.4 修改附件记录字段

**受影响的点**：
- `ClearTextAttachments` / `ClearImageAttachments`（逐字段清空）
- `PopulateAttachmentArray`（读字段）
- `GenerateWithTextFile` / `GenerateWithImageFile`（写字段）

### 11.5 修改 `SafeParseJson` 契约

**契约**：
- **成功**：`AJson` 非 nil，返回 `True`
- **失败**：`AJson` 为 nil，返回 `False`，`AError` 非空

**所有调用点**都依赖这个契约：
- `CreateSession` / `CloseSession` / `CancelSession` / `ListSessions`
- `Generate` / `GenerateWithAttachments` / `SetSystemMessage` / `Health`
- `GetAPICapabilities`

### 11.6 修改 `CheckResponseCode` 契约

**契约**：
- **成功**：返回 `True`，`AError` 为空
- **失败**：返回 `False`，`AError` 是 `error` 字段或 `'<APIName> failed (code N)'`

**所有调用点**都依赖第二个参数是 API 名。

---

## §12 常见任务快速索引

### 12.1 "我要写一个 LLM 客户端"

→ §3.1 模板 → 记得 `TThread.Queue` marshalling → 记得 `LF_Shutdown` 由宿主调用 → **用 `'LLM_Service'` + `'ipc:llm_service'`**

### 12.2 "我要加一个新 API"

→ §5.1 模板 → 从 `Generate` 抄结构

### 12.3 "我要加一个新事件类型"

→ §5.3 步骤 1-5

### 12.4 "程序运行时挂了"

→ §4 错误消息对照表 → 找消息原文 → 按"修复"列

### 12.5 "UI 崩溃了"

→ §8.1 → 检查回调是否 marshalling

### 12.6 "程序死锁了"

→ §8.2 → 检查回调是否调用 Call API

### 12.7 "内存泄漏了"

→ §6.1 谁创建谁释放 → 检查附件数组是否 `Clear*`

### 12.8 "附件上传失败"

→ §4.5 附件错误对照 → 检查大小是否超限

### 12.9 **"图片附件被拒绝"**（v3 新增）

→ §8.11 + §4.6 → 检查：
1. 目标服务端是 `llm_service`？→ 改用代理
2. 代理未传 `--vision`？→ 服务端启动参数加 `--vision`
3. 图片超限？→ 压缩

### 12.10 "升级破坏性变更"

→ §10.2 迁移表

### 12.11 "改动会不会破坏别的"

→ §11 影响面分析

### 12.12 **"如何判断服务端支持什么"**

→ §3.4 能力发现示例 → 用 `LLMSupported` / `IsToolBridge` / `HasAttachments` → **不要**用 `HasVision` 判断图片能力

---

## §13 诚实的不确定清单

> **用途**：AI 遇到以下场景**必须停止**，回查源码或询问人类。
>
> **状态标注**：✅ 已解决（在后续版本或文档中确认）；⏳ 未解决（仍需回查源码）。

| # | 不确定点 | 状态 | 说明 |
|---|---------|:----:|------|
| 1 | `LF_CallEx` 失败时返回 nil 还是空句柄 | ✅ 已解决 | 由 **LF-CALL-001** 明确：**返回 size=0 的空句柄，不是 nil**。`CallAPI` 需检查 `LF_GetSize`。 |
| 2 | 通知回调是单线程还是池化 | ⏳ 未解决 | 无明确结论。建议在 `HandleLLMNotify` 打印 `GetCurrentThreadId`，发 100 条消息看 ID。 |
| 3 | `ClearTextAttachments` 是否真的必要 | ⏳ 未解决 | 用户建议保留。写最小复现：循环 `SetLength` + 赋值，观察内存。 |
| 4 | `umlBase64EncodeBytes` 消费源是否所有版本一致 | ⏳ 未解决 | 读 `Z.UnicodeMixedLib.pas` 实现确认。 |
| 5 | `server_kind` 字段在所有版本是否存在 | ✅ 已解决 | 由 **LingoFuse_LLM_Ecosystem_User_Guide.md** 明确：三种服务端都返回 `server_kind`。 |
| 6 | `ATTACHMENT_ALLOWED_IMAGE_MIMES` 的用途 | ⏳ 未解决 | 定义了但未使用。需问原作者。 |
| 7 | `GenerateWithTextFile` 的 Latin-1 fallback 正确性 | ⏳ 未解决 | 造非 UTF-8/GBK 文件实测。 |
| 8 | `TAtomString.Create('')` 是否所有平台行为一致 | ⏳ 未解决 | 读 `Z.Core.pas` §3.2。 |

---

## §14 关键规则总结（AI 速记）

> **写代码必守**：
> 1. 构造参数用 `'LLM_Service'` + `'ipc:llm_service'`（**大小写严格**）。
> 2. `OnChunk` 等回调在**通知线程**执行——UI 操作必须 `TThread.Queue`。
> 3. 回调中**不能**调用 Call API——会死锁。
> 4. 附件数组必须 **`ClearTextAttachments` / `ClearImageAttachments`**。
> 5. `Connect` 后**检查返回值**——能力探测失败会静默。
> 6. `Generate` 传空 `ASessionId` 会**续接**会话。
> 7. **调用前做能力发现**——尤其 `SetSystemMessage` / 附件 / 工具相关 API。
> 8. **图片附件前检查 `ServerKind`**——`'service'` 直接拒绝。
> 9. **`HasVision` 不可用于判断"能否发图片"**——它在本版本永远是 False。

> **改代码必守**：
> 1. 所有请求构造用 `TZ_JsonObject`，`try...finally` 释放。
> 2. 解析响应用 `SafeParseJson`，**失败时 `AJson` 已 nil**。
> 3. 错误检查用 `CheckResponseCode`，**第二个参数是 API 名**。
> 4. `Parae(TBytes)` 前先检查 `Length > 0`。
> 5. 新 API **照抄 `Generate` 结构**。

> **禁用清单**：
> - ❌ 不用 `ParseText` 解析 `TBytes`（用 `Parae`）
> - ❌ 不引入 `lingofuse_helper`（用 `lingofuse_import` 的 `*Ex`）
> - ❌ 不用 `reference to procedure` 做事件（用 `of object`）
> - ❌ 不在 `Disconnect` 里调 `LF_Shutdown`（宿主负责）
> - ❌ 不读 `FCapabilities` 原始 JSON 判 `nil`（用 `LLMSupported`）
> - ❌ **不用 `HasVision` 判断"能否发图片"**（用 `ServerKind` + `HasAttachments`）

---

## §15 相关文档

| 文档 | 说明 |
|------|------|
| [`../Pascal_Integration_Guide.md`](../Pascal_Integration_Guide.md) | Pascal 开发者切入指南（六大场景） |
| [`../Build_Guide.md`](../Build_Guide.md) | 编译指南（含 `llm_client_v3` / `llm_tool_v3`） |
| [`../readme.md`](../readme.md) | 项目总览与四大核心应用组件 |
| [`LingoFuse_LLM_Ecosystem_User_Guide.md`](LingoFuse_LLM_Ecosystem_User_Guide.md) | 生态总览（四大应用组件 + 两条路径） |
| [`LingoFuse_LLM_Pitfalls_For_AI.md`](LingoFuse_LLM_Pitfalls_For_AI.md) | 踩坑大全（含 P8 多模态专项） |
| [`LingoFuse_LLM_Proxy_CLI_Guide.md`](LingoFuse_LLM_Proxy_CLI_Guide.md) | 纯转发代理命令行手册（多模态转发，第 5.5 节） |
| [`LingoFuse_LLM_Proxy_Tool_CLI_Guide.md`](LingoFuse_LLM_Proxy_Tool_CLI_Guide.md) | LTB 命令行手册（多模态转发，第 4.5 节） |
| [`LingoFuse_LLM_Service_CLI_guide.md`](LingoFuse_LLM_Service_CLI_guide.md) | 本地推理服务手册（明确不支持多模态） |
| [`LingoFuse_Pascal_Complete_Guide.md`](LingoFuse_Pascal_Complete_Guide.md) | Pascal 核心层完整指南（含 LF-XXX-NNN 踩坑知识库） |

**GUI 演示源码**：本仓库 `src\llm_tool_v3_frm.pas` —— 展示 SDK 全部关键用法（多会话、流式、附件、能力发现）。

---

**文档版本**：v2.1（结构性重构版 · 能力边界修正版——修正文档标题与源文件引用、修正构造示例参数、补充能力矩阵 `vision=0` 语义、新增图片附件排查章节、补充能力发现示例、更新诚实清单状态）

**维护方式**：发现新的错误消息、新坑、新模板，追加到对应章节
**核心承诺**：AI 读完本文档能独立完成 90% 的 `llm_client_v3` 任务，剩下 10% 见 §13 诚实清单
