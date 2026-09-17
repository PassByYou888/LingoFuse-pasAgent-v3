(*
 * ============================================================================
 * llm_tool_v3_frm — LingoFuse LLM 客户端测试工具（主窗体）v3.4
 * ============================================================================
 *
 * 版本变更（v3.4）
 * ----------------
 *   1. 移除 v3.3 的"附件 + Structured Output 冲突拒绝"逻辑。
 *      现在两种情况都由底层 SDK 直接支持：
 *        - 有附件 + 有 schema → GenerateWithAttachmentsAndSchema
 *        - 无附件 + 有 schema → GenerateWithJsonSchema
 *   2. DoGenerateWithCurrentSettings 的 4 种组合分支简化：
 *        - 有 schema + 有附件 → GenerateWithAttachmentsAndSchema（新）
 *        - 有 schema + 无附件 → GenerateWithJsonSchema
 *        - 无 schema + 有附件 → GenerateWithAttachments
 *        - 无 schema + 无附件 → Generate
 *   3. new_session_ButtonClick / test_generate_ButtonClick 中的
 *      "usedAttachments" 判断简化为 HasAttachments，因为两种情况
 *      都能成功发送，发送后统一清空附件。
 *   4. 依赖 SDK：llm_client_v3.pas v3.10（含 GenerateWithAttachmentsAndSchema）。
 *
 * 版本变更（v3.3）
 * ----------------
 *   1. 新增"结构化输出"TabSheet：
 *        - EnableStructuredOutputCheckBox（启用开关）
 *        - SchemaNameEdit（schema 名）
 *        - StrictCheckBox（strict 严格模式）
 *        - SchemaMemo（schema 本体）
 *        - LoadDetectorTemplateButton（一键加载检测器模板）
 *   2. 新增 DoGenerateWithCurrentSettings 统一分流。
 *   3. "生成"/"新建会话"按钮统一走 DoGenerateWithCurrentSettings。
 *   4. Enabled_All / Disable_All 覆盖 TCustomCheckBox。
 *
 * 版本变更（v3.2）
 * ----------------
 *   1. 新增"附件"面板：文本文件、图片文件、剪贴板粘贴。
 *   2. 剪贴板图片自动转 PNG（通过 TPortableNetworkGraphic），
 *      保证 MIME 命中服务端白名单（image/png|jpeg|webp）。
 *   3. 发送 generate 时，如果存在附件，自动切换到
 *      LLM.GenerateWithAttachments。
 *   4. 本地预检使用 Length(UTF8String(S)) 计算 UTF-8 字节数，
 *      与服务端 llm_common/attachments.py 的语义完全一致
 *      （绕开 llm_client.UTF8ByteCount 在 FPC 下的缺陷）。
 *   5. FormClose 修正清理顺序：LLM.Disconnect → LF_Shutdown，
 *      避免回调线程访问已释放的 Form。
 *
 * ============================================================================
 *)

unit llm_tool_v3_frm;

{$DEFINE FPC_DELPHI_MODE}
{$I ..\zCore\src\Z.Define.inc}

interface

uses
  Classes, SysUtils, Forms, Controls, Graphics, Dialogs, StdCtrls, ExtCtrls,
  ComCtrls, Clipbrd, GraphType, FPImage, IntfGraphics,
  SynEdit, SynEditMiscClasses, SynHighlighterAny,
  llm_client_v3, lingofuse_import, lingofuse_helper,
  Z.Core, Z.PascalStrings, Z.UPascalStrings, Z.Json, Z.UnicodeMixedLib, Z.Status;

type

  { Tllm_tool_v3_form — 主窗体 }
  Tllm_tool_v3_form = class(TForm)
    { ---- 原有控件 ---- }
    BottomPanel: TPanel;
    BottomSplitter: TSplitter;
    code_edit: TMemo;
    sse_Edit: TSynEdit;
    Update_Sys_Prompt_Button: TButton;
    conn_llm_Button: TButton;
    Label1: TLabel;
    Label2: TLabel;
    Label3: TLabel;
    Label4: TLabel;
    llm_APP_Edit: TLabeledEdit;
    LLM_Service_Edit: TLabeledEdit;
    LogMemo: TMemo;
    MainPageControl: TPageControl;
    LLM_Opt_TabSheet: TTabSheet;
    Input_TabSheet: TTabSheet;
    Output_TabSheet: TTabSheet;
    Panel1: TPanel;
    Panel2: TPanel;
    Panel3: TPanel;
    Splitter1: TSplitter;
    sys_prompt_Memo: TMemo;
    test_generate_Button: TButton;
    prompt_edit: TMemo;
    sysTimer: TTimer;
    new_session_Button: TButton;

    { ---- v3.2 新增：附件面板 ---- }
    AttachmentPanel: TPanel;
    AttachmentTopLabel: TLabel;
    AttachmentListBox: TListBox;
    AttachmentBtnsPanel: TPanel;
    BtnAddTextFile: TButton;
    BtnAddImageFile: TButton;
    BtnPasteClipboard: TButton;
    BtnClearAttachments: TButton;
    OpenDialog1: TOpenDialog;

    { ---- v3.3 新增：结构化输出面板 ---- }
    StructuredOutput_TabSheet: TTabSheet;          // 新增 Tab 页
    StructuredHintLabel: TLabel;                   // 顶部说明
    EnableStructuredOutputCheckBox: TCheckBox;     // 启用开关
    SchemaNameEdit: TLabeledEdit;                  // schema 名
    StrictCheckBox: TCheckBox;                     // strict 严格模式
    LoadDetectorTemplateButton: TButton;           // 一键加载模板
    SchemaBodyLabel: TLabel;                       // schema 本体标签
    SchemaMemo: TMemo;                             // schema 本体编辑

    { ---- 事件 ---- }
    procedure Update_Sys_Prompt_ButtonClick(Sender: TObject);
    procedure conn_llm_ButtonClick(Sender: TObject);
    procedure FormClose(Sender: TObject; var CloseAction: TCloseAction);
    procedure new_session_ButtonClick(Sender: TObject);
    procedure sysTimerTimer(Sender: TObject);
    procedure test_generate_ButtonClick(Sender: TObject);
    procedure BtnAddTextFileClick(Sender: TObject);
    procedure BtnAddImageFileClick(Sender: TObject);
    procedure BtnPasteClipboardClick(Sender: TObject);
    procedure BtnClearAttachmentsClick(Sender: TObject);
    procedure LoadDetectorTemplateButtonClick(Sender: TObject);

  private
    { 当前输出页显示的会话 ID；空表示"尚未选定"。 }
    FActiveSessionId: string;

    { v3.2：附件缓存 }
    FTextAttachments: TLLMTextAttachmentArray;
    FImageAttachments: TLLMImageAttachmentArray;

    { ---- 流式事件处理器（LingoFuse 通知线程执行） ---- }
    procedure Backcall_DoStatus(Text_: SystemString; const ID: integer);
    procedure Do_LLM_Chunk(const SessionId, Chunk: string);
    procedure Do_LLM_Think(const SessionId, Text: string);
    procedure Do_LLM_Error(const SessionId, ErrorMsg: string);
    procedure Do_LLM_Finish(const SessionId, Reason: string);
    procedure Do_LLM_Closed(const SessionId, Reason: string);

    { ---- 输出辅助 ---- }
    procedure AppendChunkToOutput(const Text: string);
    procedure ResetOutputHeader(const SessionId: string);

    { ---- 附件辅助 ---- }
    procedure RefreshAttachmentList;
    function HasAttachments: boolean;
    function TryReadFileToBytes(const AFileName: string; out ABytes: TBytes; out AError: string): boolean;
    function DecodeBytesToText(const ABytes: TBytes): string;
    function GuessImageMimeByExt(const AFileName: string): string;
    function BitmapToPngBytes(ABitmap: TBitmap; out ABytes: TBytes; out AError: string): boolean;

    { ---- v3.3：结构化输出分流 ---- }
    function IsStructuredOutputEnabled: boolean;
    function DoGenerateWithCurrentSettings(const AContent, APrompt: string;
      var ASessionId: string; out AError: string): boolean;

    { ---- 连接工作线程 ---- }
    procedure Do_Thread_Connect_Done;
    procedure Do_Thread_Connect;
  public
    constructor Create(AOwner: TComponent); override;
    destructor Destroy; override;
    procedure Enabled_All;
    procedure Disable_All;
  end;

var
  llm_tool_v3_form: Tllm_tool_v3_form;
  LLM: TLLMClient;

implementation

{$R *.lfm}

{ ============================================================================
  1. 输出辅助
  ============================================================================ }

{*
 * 把一段文本追加到输出区 sse_Edit 的末尾。
 * 说明：
 *   - 递归实现，将 \n 拆分为多行。
 *   - 每行超过 80 字符自动换行（纯 UI 优化）。
 *   - 必须在主线程调用（Streaming 回调已经通过 Sync 满足）。
 *}
procedure Tllm_tool_v3_form.AppendChunkToOutput(const Text: string);
var
  s, First, rest: string;
  p: integer;
begin
  if sse_Edit.Lines.Count <= 0 then
    sse_Edit.Lines.Add('');

  s := StringReplace(Text, #13, '', [rfReplaceAll]);
  if s = '' then
    Exit;

  p := Pos(#10, s);
  if p > 0 then
  begin
    First := Copy(s, 1, p - 1);
    rest := Copy(s, p + 1, MaxInt);

    sse_Edit.Lines[sse_Edit.Lines.Count - 1] :=
      sse_Edit.Lines[sse_Edit.Lines.Count - 1] + First;
    sse_Edit.Lines.Add('');

    if rest <> '' then
      AppendChunkToOutput(rest);
  end
  else
  begin
    sse_Edit.Lines[sse_Edit.Lines.Count - 1] :=
      sse_Edit.Lines[sse_Edit.Lines.Count - 1] + s;

    if length(sse_Edit.Lines[sse_Edit.Lines.Count - 1]) > 80 then
      sse_Edit.Lines.Add('');
  end;
end;

{* 清空输出区并写入新会话的头部信息。 *}
procedure Tllm_tool_v3_form.ResetOutputHeader(const SessionId: string);
begin
  sse_Edit.Clear;
  sse_Edit.Lines.Add('[session ' + SessionId + ']');
  sse_Edit.Lines.Add('');
  sse_Edit.ReadOnly := False;
end;

{ ============================================================================
  2. 附件辅助
  ============================================================================ }

{*
 * 判断是否有任何附件（文本或图片）。
 *}
function Tllm_tool_v3_form.HasAttachments: boolean;
begin
  Result := (length(FTextAttachments) > 0) or (length(FImageAttachments) > 0);
end;

{*
 * 以二进制方式读取文件到 TBytes。失败时 AError 内包含原因。
 * 不在此处做大小校验——调用方应根据不同附件类型的上限进行校验。
 *}
function Tllm_tool_v3_form.TryReadFileToBytes(const AFileName: string; out ABytes: TBytes; out AError: string): boolean;
var
  fs: TFileStream;
begin
  Result := False;
  AError := '';
  SetLength(ABytes, 0);

  try
    fs := TFileStream.Create(AFileName, fmOpenRead or fmShareDenyNone);
  except
    on E: Exception do
    begin
      AError := '打开文件失败: ' + E.Message;
      Exit;
    end;
  end;

  try
    try
      SetLength(ABytes, fs.Size);
      if fs.Size > 0 then
        fs.ReadBuffer(ABytes[0], fs.Size);
      Result := True;
    except
      on E: Exception do
      begin
        SetLength(ABytes, 0);
        AError := '读取文件失败: ' + E.Message;
      end;
    end;
  finally
    fs.Free;
  end;
end;

{*
 * 将任意文本文件的字节内容解码为 Pascal string。
 * 解码顺序：UTF-8 → GBK(936) → Latin-1 兜底。
 * 与服务端 Python 侧的 "UTF-8 with fallback" 行为对齐。
 *}
function Tllm_tool_v3_form.DecodeBytesToText(const ABytes: TBytes): string;
var
  gbk: TEncoding;
begin
  if length(ABytes) = 0 then
    Exit('');

  { 先尝试 UTF-8 }
  try
    Result := TEncoding.UTF8.GetString(ABytes);
    Exit;
  except
  end;

  { 再尝试 GBK（Windows 中文环境常见） }
  try
    gbk := TEncoding.GetEncoding(936);
    try
      Result := gbk.GetString(ABytes);
      Exit;
    finally
      gbk.Free;
    end;
  except
  end;

  { Latin-1 兜底：任何字节序列都可解码 }
  SetLength(Result, length(ABytes));
  if length(ABytes) > 0 then
    Move(ABytes[0], Result[1], length(ABytes));
end;

{*
 * 根据文件扩展名猜测图片 MIME。
 * 服务端白名单：image/png|jpeg|webp，'image/jpg' 会被规整为 image/jpeg。
 * 未知扩展名默认按 PNG 处理，服务端会拒绝并给出明确错误。
 *}
function Tllm_tool_v3_form.GuessImageMimeByExt(const AFileName: string): string;
var
  ext: string;
begin
  ext := LowerCase(ExtractFileExt(AFileName));
  if ext = '.png' then
    Result := 'image/png'
  else if (ext = '.jpg') or (ext = '.jpeg') then
    Result := 'image/jpeg'
  else if ext = '.webp' then
    Result := 'image/webp'
  else
    Result := 'image/png';
end;

{*
 * 将 TBitmap 转成 PNG 字节流。
 *
 * 为什么必须转 PNG：剪贴板里的位图默认保存为 BMP，而 BMP 不在服务端
 * 允许的图片 MIME 白名单里（image/png|jpeg|webp）。把它转成 PNG 后
 * 就能顺利通过校验。
 *}
function Tllm_tool_v3_form.BitmapToPngBytes(ABitmap: TBitmap; out ABytes: TBytes; out AError: string): boolean;
var
  png: TPortableNetworkGraphic;
  ms: TMemoryStream;
begin
  Result := False;
  AError := '';
  SetLength(ABytes, 0);

  if ABitmap = nil then
  begin
    AError := '位图为空';
    Exit;
  end;

  png := TPortableNetworkGraphic.Create;
  try
    try
      png.Assign(ABitmap);
    except
      on E: Exception do
      begin
        AError := 'PNG 转换失败: ' + E.Message;
        Exit;
      end;
    end;

    ms := TMemoryStream.Create;
    try
      try
        png.SaveToStream(ms);
        ms.Position := 0;
        SetLength(ABytes, ms.Size);
        if ms.Size > 0 then
          ms.ReadBuffer(ABytes[0], ms.Size);
        Result := True;
      except
        on E: Exception do
        begin
          SetLength(ABytes, 0);
          AError := 'PNG 写入失败: ' + E.Message;
        end;
      end;
    finally
      ms.Free;
    end;
  finally
    png.Free;
  end;
end;

{*
 * 刷新附件列表显示。
 * 显示格式：
 *   [文本] <name> (<字符数> chars, <utf8 字节数> bytes)
 *   [图片] <name> (<base64 字符数> b64 chars)
 *}
procedure Tllm_tool_v3_form.RefreshAttachmentList;
var
  i: integer;
  utf8len: int64;
begin
  AttachmentListBox.Items.BeginUpdate;
  try
    AttachmentListBox.Items.Clear;

    for i := 0 to High(FTextAttachments) do
    begin
      utf8len := length(utf8string(FTextAttachments[i].Text));
      AttachmentListBox.Items.Add(Format('[文本] %s  (%d 字符 / %d UTF-8 字节)', [FTextAttachments[i].Name.Text,
        FTextAttachments[i].Text.L, utf8len]));
    end;

    for i := 0 to High(FImageAttachments) do
      AttachmentListBox.Items.Add(Format('[图片] %s  (%d base64 字符)', [FImageAttachments[i].Name.Text, FImageAttachments[i].DataB64.L]));
  finally
    AttachmentListBox.Items.EndUpdate;
  end;
end;

{*
 * "添加文本文件"按钮：
 *   1. 打开 OpenDialog1
 *   2. 读文件 → 检查大小 → 解码 → 加入 FTextAttachments
 *   3. 刷新列表
 *}
procedure Tllm_tool_v3_form.BtnAddTextFileClick(Sender: TObject);
var
  rawBytes: TBytes;
  err: string;
  att: TLLMTextAttachment;
  byteCount: int64;
begin
  OpenDialog1.Title := '选择要附加的文本文件';
  OpenDialog1.Filter :=
    '所有文本文件 (*.txt;*.md;*.py;*.pas;*.c;*.h;*.json;*.log;*.csv;*.xml;*.yml;*.yaml)|' +
    '*.txt;*.md;*.py;*.pas;*.c;*.h;*.cpp;*.json;*.log;*.csv;*.xml;*.yml;*.yaml|' + '所有文件 (*.*)|*.*';
  OpenDialog1.Options := [ofFileMustExist, ofEnableSizing];

  if not OpenDialog1.Execute then
    Exit;

  if not TryReadFileToBytes(OpenDialog1.FileName, rawBytes, err) then
  begin
    DoStatus('加载文本文件失败: ' + err);
    Exit;
  end;

  byteCount := length(rawBytes);
  if byteCount > ATTACHMENT_MAX_TEXT_BYTES_PER_FILE then
  begin
    DoStatus(Format('文本文件 "%s" 大小 %d 字节，超过单文件上限 %d 字节，已拒绝。',
      [ExtractFileName(OpenDialog1.FileName), byteCount, ATTACHMENT_MAX_TEXT_BYTES_PER_FILE]));
    Exit;
  end;

  att.Name := ExtractFileName(OpenDialog1.FileName);
  att.Mime := 'text/plain';
  att.Text := DecodeBytesToText(rawBytes);

  SetLength(FTextAttachments, length(FTextAttachments) + 1);
  FTextAttachments[High(FTextAttachments)] := att;

  RefreshAttachmentList;
  DoStatus(Format('已添加文本附件: %s (%d 字节)', [att.Name.Text, byteCount]));
end;

{*
 * "添加图片文件"按钮：
 *   1. 打开 OpenDialog1
 *   2. 读文件 → 检查大小 → base64 编码 → 加入 FImageAttachments
 *   3. 刷新列表
 *}
procedure Tllm_tool_v3_form.BtnAddImageFileClick(Sender: TObject);
var
  rawBytes: TBytes;
  err: string;
  att: TLLMImageAttachment;
  b64: TPascalString;
  byteCount: int64;
  b64Estimate: int64;
begin
  OpenDialog1.Title := '选择要附加的图片文件';
  OpenDialog1.Filter :=
    '图片文件 (*.png;*.jpg;*.jpeg;*.webp)|*.png;*.jpg;*.jpeg;*.webp|' + '所有文件 (*.*)|*.*';
  OpenDialog1.Options := [ofFileMustExist, ofEnableSizing];

  if not OpenDialog1.Execute then
    Exit;

  if not TryReadFileToBytes(OpenDialog1.FileName, rawBytes, err) then
  begin
    DoStatus('加载图片失败: ' + err);
    Exit;
  end;

  byteCount := length(rawBytes);
  if byteCount = 0 then
  begin
    DoStatus('图片文件为空，已拒绝。');
    Exit;
  end;

  { base64 是 4/3 膨胀 + 少量 padding，这里按估算值预检 }
  b64Estimate := ((byteCount + 2) div 3) * 4;
  if b64Estimate > ATTACHMENT_MAX_IMAGE_B64_PER_FILE then
  begin
    DoStatus(Format('图片 "%s" 编码后约 %d base64 字符，超过单文件上限 %d，已拒绝。',
      [ExtractFileName(OpenDialog1.FileName), b64Estimate, ATTACHMENT_MAX_IMAGE_B64_PER_FILE]));
    Exit;
  end;

  att.Name := ExtractFileName(OpenDialog1.FileName);
  att.Mime := GuessImageMimeByExt(OpenDialog1.FileName);

  { 注意：umlBase64EncodeBytes 使用 var sour 参数，
    可能以零拷贝方式"偷走" rawBytes，之后 rawBytes 不可再用。 }
  b64 := '';
  umlBase64EncodeBytes(rawBytes, b64);
  att.DataB64 := b64.Text;

  SetLength(FImageAttachments, length(FImageAttachments) + 1);
  FImageAttachments[High(FImageAttachments)] := att;

  RefreshAttachmentList;
  DoStatus(Format('已添加图片附件: %s (%d 字节, %d base64 字符)', [att.Name.Text, byteCount, att.DataB64.L]));
end;

{*
 * "从剪贴板粘贴"按钮：
 *   优先处理位图 → 转 PNG → 加入 image 附件
 *   否则处理文本 → 加入 text 附件
 *   都没有 → 提示
 *}
procedure Tllm_tool_v3_form.BtnPasteClipboardClick(Sender: TObject);
var
  bmp: TBitmap;
  pngBytes: TBytes;
  err: string;
  att: TLLMImageAttachment;
  b64: TPascalString;
  textAtt: TLLMTextAttachment;
  clipText: string;
  b64Estimate: int64;
begin
  { ---- 优先处理位图 ---- }
  if Clipboard.HasFormat(CF_BITMAP) then
  begin
    bmp := TBitmap.Create;
    try
      try
        bmp.Assign(Clipboard);
      except
        on E: Exception do
        begin
          DoStatus('读取剪贴板位图失败: ' + E.Message);
          Exit;
        end;
      end;

      if not BitmapToPngBytes(bmp, pngBytes, err) then
      begin
        DoStatus('位图转 PNG 失败: ' + err);
        Exit;
      end;

      b64Estimate := ((length(pngBytes) + 2) div 3) * 4;
      if b64Estimate > ATTACHMENT_MAX_IMAGE_B64_PER_FILE then
      begin
        DoStatus(Format('剪贴板图片编码后约 %d base64 字符，超过单文件上限 %d，已拒绝。',
          [b64Estimate, ATTACHMENT_MAX_IMAGE_B64_PER_FILE]));
        Exit;
      end;

      att.Name := Format('clipboard_%s.png', [FormatDateTime('yyyymmdd_hhnnss', Now)]);
      att.Mime := 'image/png';

      b64 := '';
      umlBase64EncodeBytes(pngBytes, b64);
      att.DataB64 := b64.Text;

      SetLength(FImageAttachments, length(FImageAttachments) + 1);
      FImageAttachments[High(FImageAttachments)] := att;

      RefreshAttachmentList;
      DoStatus(Format('已从剪贴板粘贴图片: %s (%d base64 字符)', [att.Name.Text, att.DataB64.L]));
    finally
      bmp.Free;
    end;
    Exit;
  end;

  { ---- 其次处理文本 ---- }
  if Clipboard.HasFormat(CF_TEXT) then
  begin
    clipText := Clipboard.AsText;
    if clipText = '' then
    begin
      DoStatus('剪贴板文本为空，忽略。');
      Exit;
    end;

    if length(utf8string(clipText)) > ATTACHMENT_MAX_TEXT_BYTES_PER_FILE then
    begin
      DoStatus(Format('剪贴板文本 UTF-8 长度超过单文件上限 %d 字节，已拒绝。', [ATTACHMENT_MAX_TEXT_BYTES_PER_FILE]));
      Exit;
    end;

    textAtt.Name := Format('clipboard_%s.txt', [FormatDateTime('yyyymmdd_hhnnss', Now)]);
    textAtt.Mime := 'text/plain';
    textAtt.Text := clipText;

    SetLength(FTextAttachments, length(FTextAttachments) + 1);
    FTextAttachments[High(FTextAttachments)] := textAtt;

    RefreshAttachmentList;
    DoStatus(Format('已从剪贴板粘贴文本 (%d 字符)', [length(clipText)]));
    Exit;
  end;

  DoStatus('剪贴板内没有可用的文本或图片。');
end;

{*
 * "清空附件"按钮。
 *}
procedure Tllm_tool_v3_form.BtnClearAttachmentsClick(Sender: TObject);
begin
  SetLength(FTextAttachments, 0);
  SetLength(FImageAttachments, 0);
  RefreshAttachmentList;
  DoStatus('已清空全部附件。');
end;

{ ============================================================================
  3. 结构化输出辅助
  ============================================================================ }

{*
 * 判断当前是否"开启结构化输出"。
 * 条件是：勾选启用 且 schema 本体非空（去掉首尾空白）。
 *}
function Tllm_tool_v3_form.IsStructuredOutputEnabled: boolean;
var
  s: string;
begin
  if not EnableStructuredOutputCheckBox.Checked then
    Exit(False);
  s := Trim(SchemaMemo.Text);
  Result := s <> '';
end;

{*
 * 统一的 generate 分流入口。
 *
 * 优先级与规则（v3.4）：
 *   1. 有 schema + 有附件 → LLM.GenerateWithAttachmentsAndSchema
 *   2. 有 schema + 无附件 → LLM.GenerateWithJsonSchema
 *   3. 无 schema + 有附件 → LLM.GenerateWithAttachments
 *   4. 无 schema + 无附件 → LLM.Generate
 *
 * 说明：
 *   - 情况 1 是 v3.4 新增能力。SDK 从 v3.10 起支持"附件 + schema"
 *     的组合，因此不再需要拒绝。
 *   - 调用方（new_session_ButtonClick / test_generate_ButtonClick）
 *     需要在成功后自行更新 FActiveSessionId 和清空附件。
 *}
function Tllm_tool_v3_form.DoGenerateWithCurrentSettings(
  const AContent, APrompt: string;
  var ASessionId: string;
  out AError: string): boolean;
var
  schemaName: string;
  schemaBody: string;
  useStructured: boolean;
  useAttach: boolean;
begin
  Result := False;
  AError := '';

  if LLM = nil then
  begin
    AError := '尚未连接 LLM 服务。';
    Exit;
  end;

  useStructured := IsStructuredOutputEnabled;
  useAttach := HasAttachments;

  { 先取 schema 参数，避免多次访问 UI 控件 }
  schemaName := '';
  schemaBody := '';
  if useStructured then
  begin
    schemaName := Trim(SchemaNameEdit.Text);
    if schemaName = '' then
      schemaName := 'response_schema';
    schemaBody := SchemaMemo.Text;
  end;

  { 情况 1：schema + 附件（v3.4 组合方法） }
  if useStructured and useAttach then
  begin
    DoStatus(Format('发起 Structured Output + 附件请求（schema=%s, strict=%s, texts=%d, images=%d）',
      [schemaName, BoolToStr(StrictCheckBox.Checked, True),
       length(FTextAttachments), length(FImageAttachments)]));
    Result := LLM.GenerateWithAttachmentsAndSchema(
      AContent, APrompt,
      FTextAttachments, FImageAttachments,
      schemaName, schemaBody,
      StrictCheckBox.Checked,
      ASessionId, AError);
    Exit;
  end;

  { 情况 2：纯 schema }
  if useStructured then
  begin
    DoStatus(Format('发起 Structured Output 请求（schema=%s, strict=%s）',
      [schemaName, BoolToStr(StrictCheckBox.Checked, True)]));
    Result := LLM.GenerateWithJsonSchema(
      AContent, APrompt,
      schemaName, schemaBody,
      StrictCheckBox.Checked,
      ASessionId, AError);
    Exit;
  end;

  { 情况 3：纯附件 }
  if useAttach then
  begin
    Result := LLM.GenerateWithAttachments(
      AContent, APrompt,
      FTextAttachments, FImageAttachments,
      ASessionId, AError);
    Exit;
  end;

  { 情况 4：普通文本 }
  Result := LLM.Generate(AContent, APrompt, ASessionId, AError);
end;

{*
 * "加载检测器模板"按钮：
 *   把内置的"标签 + 方框 + 置信度"模板填入 SchemaMemo，
 *   并自动勾选启用 Structured Output 和 strict 模式。
 *}
procedure Tllm_tool_v3_form.LoadDetectorTemplateButtonClick(Sender: TObject);
const
  DetectorTemplate: string =
    '{' + sLineBreak +
    '  "type": "object",' + sLineBreak +
    '  "properties": {' + sLineBreak +
    '    "detections": {' + sLineBreak +
    '      "type": "array",' + sLineBreak +
    '      "description": "List of detected objects with confidence scores",' + sLineBreak +
    '      "items": {' + sLineBreak +
    '        "type": "object",' + sLineBreak +
    '        "properties": {' + sLineBreak +
    '          "label": {' + sLineBreak +
    '            "type": "string",' + sLineBreak +
    '            "description": "Object class name, e.g. person, car, dog"' + sLineBreak +
    '          },' + sLineBreak +
    '          "bbox": {' + sLineBreak +
    '            "type": "array",' + sLineBreak +
    '            "description": "Normalized bbox [x_min, y_min, x_max, y_max], values in 0~1",' + sLineBreak +
    '            "items": { "type": "number", "minimum": 0, "maximum": 1 },' + sLineBreak +
    '            "minItems": 4,' + sLineBreak +
    '            "maxItems": 4' + sLineBreak +
    '          },' + sLineBreak +
    '          "confidence": {' + sLineBreak +
    '            "type": "number",' + sLineBreak +
    '            "description": "Detection confidence, 0.0 to 1.0",' + sLineBreak +
    '            "minimum": 0,' + sLineBreak +
    '            "maximum": 1' + sLineBreak +
    '          }' + sLineBreak +
    '        },' + sLineBreak +
    '        "required": ["label", "bbox", "confidence"]' + sLineBreak +
    '      }' + sLineBreak +
    '    }' + sLineBreak +
    '  },' + sLineBreak +
    '  "required": ["detections"]' + sLineBreak +
    '}';
begin
  SchemaNameEdit.Text := 'object_detection';
  StrictCheckBox.Checked := True;
  EnableStructuredOutputCheckBox.Checked := True;
  SchemaMemo.Lines.Text := DetectorTemplate;

  MainPageControl.ActivePage := StructuredOutput_TabSheet;

  DoStatus('已加载检测器方框标注模板（object_detection）');
  DoStatus('提示：模板包含 label + bbox + confidence 三个字段，');
  DoStatus('       bbox 是归一化 [x_min, y_min, x_max, y_max]。');
  DoStatus('       可编辑 SchemaMemo 微调，然后切到"输入"页点击"生成"。');
  DoStatus('       v3.4：附件 + schema 现在可以同时启用（会自动走组合方法）。');
end;

{ ============================================================================
  4. 流式事件处理器
  ============================================================================ }

procedure Tllm_tool_v3_form.Do_LLM_Chunk(const SessionId, Chunk: string);
begin
  if (FActiveSessionId <> '') and (SessionId <> FActiveSessionId) then
    Exit;
  AppendChunkToOutput(Chunk);
end;

procedure Tllm_tool_v3_form.Do_LLM_Think(const SessionId, Text: string);
begin
  if (FActiveSessionId <> '') and (SessionId <> FActiveSessionId) then
    Exit;
  AppendChunkToOutput(Text);
end;

procedure Tllm_tool_v3_form.Do_LLM_Error(const SessionId, ErrorMsg: string);
begin
  if (FActiveSessionId <> '') and (SessionId <> FActiveSessionId) then
    Exit;
  AppendChunkToOutput(sLineBreak + '[ERROR] ' + ErrorMsg + sLineBreak);
  sse_Edit.ReadOnly := False;
end;

procedure Tllm_tool_v3_form.Do_LLM_Finish(const SessionId, Reason: string);
begin
  if (FActiveSessionId <> '') and (SessionId <> FActiveSessionId) then
    Exit;
  sse_Edit.Lines.Add('');
  sse_Edit.Lines.Add('--- 会话结束 (reason=' + Reason + ') ---');
  sse_Edit.ReadOnly := False;
end;

procedure Tllm_tool_v3_form.Do_LLM_Closed(const SessionId, Reason: string);
begin
  if SessionId <> FActiveSessionId then
    Exit;
  FActiveSessionId := '';
  sse_Edit.Lines.Add('');
  sse_Edit.Lines.Add('--- 会话被关闭 (reason=' + Reason + ') ---');
  sse_Edit.ReadOnly := False;
end;

{ ============================================================================
  5. 按钮事件处理器
  ============================================================================ }

{*
 * "更新系统消息"按钮：仅对 llm_service 有效。
 *}
procedure Tllm_tool_v3_form.Update_Sys_Prompt_ButtonClick(Sender: TObject);
var
  err: string;
  n: TP_String;
begin
  if LLM = nil then Exit;

  if LLM.HasCapabilityInfo and (not LLM.LLMSupported(API_NAME_SET_SYSTEM_MESSAGE)) then
  begin
    DoStatus('当前服务端类型为 "' + LLM.ServerKind + '"，不支持 set_system_message，已跳过。');
    DoStatus('提示：点击"新建会话"按钮可以让新的系统提示词生效。');
    Exit;
  end;

  n := sys_prompt_Memo.Lines.Text;
  n := n.TrimChar(#13#10#32#9);

  if not LLM.SetSystemMessage(n, err) then
  begin
    DoStatus('更新系统消息失败: ' + err);
    DoStatus('提示：点击"新建会话"按钮可以让新的系统提示词生效。');
    Exit;
  end;

  DoStatus('已更新系统消息（仅对之后新建的会话生效）');
end;

{*
 * "连接"按钮。
 *}
procedure Tllm_tool_v3_form.conn_llm_ButtonClick(Sender: TObject);
begin
  if LLM <> nil then Exit;
  Disable_All;
  TCompute.RunM_NP(Do_Thread_Connect);
end;

{*
 * "新建会话"按钮：
 *   1. 创建会话（携带 sys_prompt_Memo 内容作为 system message）
 *   2. 用 DoGenerateWithCurrentSettings 发起首次生成
 *   3. 成功后清空附件（若本次请求使用了附件）
 *
 * v3.4：只要本次请求有附件（无论是否同时携带 schema），
 *       发送成功后都清空附件，避免下一轮意外重复发送。
 *}
procedure Tllm_tool_v3_form.new_session_ButtonClick(Sender: TObject);
var
  sid, err: string;
  sys_msg: TP_String;
  ok: boolean;
  usedAttachments: boolean;
begin
  if LLM = nil then
  begin
    DoStatus('尚未连接 LLM 服务，请先点击"连接"按钮。');
    Exit;
  end;

  sys_msg := sys_prompt_Memo.Lines.Text;
  sys_msg := sys_msg.TrimChar(#13#10#32#9);

  if not LLM.CreateSession(sys_msg, sid, err) then
  begin
    DoStatus('创建新会话失败: ' + err);
    Exit;
  end;

  FActiveSessionId := sid;
  MainPageControl.ActivePage := Output_TabSheet;
  ResetOutputHeader(sid);
  sse_Edit.ReadOnly := True;

  DoStatus('已创建新会话: ' + sid);
  if sys_msg <> '' then
    DoStatus('系统提示词长度: ' + umlIntToStr(sys_msg.L) + ' 字符');

  { v3.4：附件与 schema 现在可以共存；只要使用附件就在成功后清空 }
  usedAttachments := HasAttachments;

  ok := DoGenerateWithCurrentSettings(code_edit.Text, prompt_edit.Lines.Text, sid, err);

  if ok then
  begin
    FActiveSessionId := sid;
    MainPageControl.ActivePage := Output_TabSheet;
    ResetOutputHeader(sid);
    sse_Edit.ReadOnly := True;

    if usedAttachments then
    begin
      DoStatus(Format('首发已发送（携带 %d 个文本、%d 个图片附件）',
        [length(FTextAttachments), length(FImageAttachments)]));
      { 首发后自动清空附件，避免下一轮意外重复发送 }
      SetLength(FTextAttachments, 0);
      SetLength(FImageAttachments, 0);
      RefreshAttachmentList;
    end;
  end
  else
    DoStatus(err);
end;

{*
 * "发送 generate 请求"按钮。
 *
 * v3.4：分发逻辑统一到 DoGenerateWithCurrentSettings：
 *   - schema + 附件 → GenerateWithAttachmentsAndSchema
 *   - schema + 无附件 → GenerateWithJsonSchema
 *   - 无 schema + 有附件 → GenerateWithAttachments
 *   - 都无 → Generate
 *}
procedure Tllm_tool_v3_form.test_generate_ButtonClick(Sender: TObject);
var
  sid, err: string;
  ok: boolean;
  usedAttachments: boolean;
begin
  if LLM = nil then Exit;

  sid := FActiveSessionId;
  usedAttachments := HasAttachments;

  ok := DoGenerateWithCurrentSettings(code_edit.Text, prompt_edit.Lines.Text, sid, err);

  if ok then
  begin
    FActiveSessionId := sid;
    MainPageControl.ActivePage := Output_TabSheet;
    ResetOutputHeader(sid);
    sse_Edit.ReadOnly := True;

    if usedAttachments then
    begin
      DoStatus(Format('已发送（携带 %d 个文本、%d 个图片附件）',
        [length(FTextAttachments), length(FImageAttachments)]));
      SetLength(FTextAttachments, 0);
      SetLength(FImageAttachments, 0);
      RefreshAttachmentList;
    end;
  end
  else
    DoStatus(err);
end;

{ ============================================================================
  6. sysTimer：驱动软同步与日志
  ============================================================================ }

procedure Tllm_tool_v3_form.sysTimerTimer(Sender: TObject);
begin
  while LF_GetStatusCount > 0 do
    DoStatus(LF_GetStatusEx);

  Check_Soft_Thread_Synchronize(0);
  LF_Sync;

  code_edit.Enabled := not HasAttachments();
  if not code_edit.Enabled then
  begin
  end;
end;

{ ============================================================================
  7. 连接工作线程
  ============================================================================ }

procedure Tllm_tool_v3_form.Do_Thread_Connect;
var
  err: string;
  n: TP_String;
begin
  if LLM = nil then
  begin
    LLM := TLLMClient.Create(llm_APP_Edit.Text, LLM_Service_Edit.Text, 5000);

    LLM.OnChunk := Do_LLM_Chunk;
    LLM.OnThink := Do_LLM_Think;
    LLM.OnError := Do_LLM_Error;
    LLM.OnFinish := Do_LLM_Finish;
    LLM.OnClosed := Do_LLM_Closed;

    if not LLM.Connect(err) then
    begin
      DoStatus(err);
      disposeObjectAndNil(LLM);
      Exit;
    end;

    if not LF.CheckApp(llm_APP_Edit.Text) then
    begin
      disposeObjectAndNil(LLM);
      Exit;
    end;

    DoStatus('app "%s" 确认握手', [llm_APP_Edit.Text]);

    if LLM.HasCapabilityInfo then
      DoStatus('服务端类型: %s（能力矩阵已获取）', [LLM.ServerKind])
    else
      DoStatus('服务端未声明能力矩阵，相关调用将按兼容模式处理');
  end;

  if LLM.HasCapabilityInfo and (not LLM.LLMSupported(API_NAME_SET_SYSTEM_MESSAGE)) then
  begin
    DoStatus('（可选）当前服务端类型为 "%s"，不支持 set_system_message，已跳过同步默认系统消息。',
      [LLM.ServerKind]);
    DoStatus('提示：点击"新建会话"按钮可以让系统提示词生效。');
  end
  else
  begin
    n := sys_prompt_Memo.Lines.Text;
    n := n.TrimChar(#13#10#32#9);
    if not LLM.SetSystemMessage(n, err) then
      DoStatus('（可选）同步默认系统消息失败：' + err + '。请点击"新建会话"让系统提示词生效。')
    else
      DoStatus('已同步默认系统消息');
  end;

  TCompute.SyncM(Do_Thread_Connect_Done);
end;

procedure Tllm_tool_v3_form.Do_Thread_Connect_Done;
begin
  Enabled_All();
  sys_prompt_Memo.Enabled := LLM.LLMSupported(API_NAME_SET_SYSTEM_MESSAGE);
  Update_Sys_Prompt_Button.Enabled := LLM.LLMSupported(API_NAME_SET_SYSTEM_MESSAGE);
  if not LLM.LLMSupported(API_NAME_SET_SYSTEM_MESSAGE) then
  begin
    sys_prompt_Memo.Text :=
      'llm_proxy 的系统提示词只能在智能体工具端设置!!' + #13#10 + '只有使用 llm_service 才能支持这个功能!!';
  end;

  { v3.4：根据服务端类型提示 Structured Output 是否可用 }
  if LLM.ServerKind = 'service' then
  begin
    DoStatus('[提示] 当前为 llm_service，不支持 Structured Output。');
    DoStatus('        如需使用"结构化输出"面板，请改用 llm_proxy / llm_proxy_tool。');
  end
  else if LLM.ServerKind = 'proxy' then
  begin
    DoStatus('[提示] 检测到代理服务端，Structured Output 已可用（需后端支持 JSON Schema）。');
    DoStatus('        v3.4：附件 + schema 组合已支持，可同时发送图片与 JSON Schema。');
  end;
end;

{ ============================================================================
  8. 生命周期
  ============================================================================ }

procedure Tllm_tool_v3_form.Backcall_DoStatus(Text_: SystemString; const ID: integer);
begin
  if LogMemo.Lines.Count > 5000 then
    LogMemo.Lines.Clear;
  LogMemo.Lines.Add(UTF8Encode(Text_));
end;

constructor Tllm_tool_v3_form.Create(AOwner: TComponent);
begin
  inherited Create(AOwner);
  AddDoStatusHook(self, Backcall_DoStatus);

  LLM := nil;
  FActiveSessionId := '';
  SetLength(FTextAttachments, 0);
  SetLength(FImageAttachments, 0);

  Disable_All();

  { 连接前仅开放连接参数与附件面板 }
  conn_llm_Button.Enabled := True;
  LLM_Service_Edit.Enabled := True;
  llm_APP_Edit.Enabled := True;
  sys_prompt_Memo.Enabled := True;
  sse_Edit.Enabled := True;

  AttachmentPanel.Enabled := True;
  AttachmentListBox.Enabled := True;
  BtnAddTextFile.Enabled := True;
  BtnAddImageFile.Enabled := True;
  BtnPasteClipboard.Enabled := True;
  BtnClearAttachments.Enabled := True;

  { 结构化输出面板的初始化 —— 连接前允许编辑 }
  StructuredOutput_TabSheet.Enabled := True;
  StructuredHintLabel.Enabled := True;
  EnableStructuredOutputCheckBox.Enabled := True;
  SchemaNameEdit.Enabled := True;
  StrictCheckBox.Enabled := True;
  LoadDetectorTemplateButton.Enabled := True;
  SchemaBodyLabel.Enabled := True;
  SchemaMemo.Enabled := True;

  { 默认值：schema 名、strict 已勾选、本体为空 }
  if SchemaNameEdit.Text = '' then
    SchemaNameEdit.Text := 'object_detection';
  StrictCheckBox.Checked := True;
  EnableStructuredOutputCheckBox.Checked := False;

  RefreshAttachmentList;
end;

destructor Tllm_tool_v3_form.Destroy;
begin
  RemoveDoStatusHook(self);
  SetLength(FTextAttachments, 0);
  SetLength(FImageAttachments, 0);
  inherited Destroy;
end;

procedure Tllm_tool_v3_form.Enabled_All;
var
  i: integer;
begin
  conn_llm_Button.Enabled := True;
  test_generate_Button.Enabled := True;
  for i := 0 to ComponentCount - 1 do
    if (Components[i] is TControl) then
    begin
      if (Components[i] is TCustomEdit) or (Components[i] is TCustomMemo) or (Components[i] is TSynEditBase) or
        (Components[i] is TCustomLabel) or (Components[i] is TCustomButton) or (Components[i] is TCustomListBox) or
        (Components[i] is TCustomCheckBox) then
        TControl(Components[i]).Enabled := True;
    end;
end;

procedure Tllm_tool_v3_form.Disable_All;
var
  i: integer;
begin
  conn_llm_Button.Enabled := False;
  test_generate_Button.Enabled := False;
  for i := 0 to ComponentCount - 1 do
    if (Components[i] is TControl) then
    begin
      if (Components[i] is TCustomEdit) or (Components[i] is TCustomMemo) or (Components[i] is TSynEditBase) or
        (Components[i] is TCustomLabel) or (Components[i] is TCustomButton) or (Components[i] is TCustomListBox) or
        (Components[i] is TCustomCheckBox) then
        TControl(Components[i]).Enabled := False;
    end;
end;

{*
 * FormClose：
 *   先断开客户端（内部 ExitMainThread + FreeApp），再 LF_Shutdown。
 *   与 LingoFuse_LLM_Pitfalls_For_AI.md P4-2 的推荐顺序一致。
 *}
procedure Tllm_tool_v3_form.FormClose(Sender: TObject; var CloseAction: TCloseAction);
begin
  CloseAction := caFree;
  try
    if LLM <> nil then
    begin
      try
        LLM.Disconnect;
      except
      end;
      disposeObjectAndNil(LLM);
    end;
  finally
    LF_Shutdown;
  end;
end;

end.
