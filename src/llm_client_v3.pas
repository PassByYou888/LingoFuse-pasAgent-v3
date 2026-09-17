(*
 * =============================================================================
 * llm_client_v3 - Pascal client for LingoFuse LLM Service (v3.11)
 * =============================================================================
 *
 * High-level, event-driven Pascal client for a LingoFuse LLM service.
 * Speaks the structured streaming protocol defined by llm_service.py /
 * llm_proxy.py / llm_proxy_tool.py (v3.0 protocol and later).
 *
 * Three backends, one protocol
 * ----------------------------
 *   llm_service.py     (server_kind = "service")
 *       Local inference server (llama.cpp / transformers).
 *
 *   llm_proxy.py       (server_kind = "proxy")
 *       Stateless forwarder to an OpenAI-compatible HTTP backend.
 *
 *   llm_proxy_tool.py  (server_kind = "proxy", tools=1)
 *       Same as llm_proxy.py plus server-side MCP tool execution.
 *
 * This client transparently works against all three. All Call API
 * methods behave identically EXCEPT SetSystemMessage, which is
 * unsupported on the two proxy siblings.
 *
 * v3.11 changes (JSON safety revision)
 * ------------------------------------
 *   * ALL JSON parsing now goes through TZ_JsonObject.Parae(TBytes).
 *     ParseText(TZ_JsonString) is NEVER used, because it goes through
 *     a `string` (AnsiString on FPC) intermediate that can silently
 *     mangle UTF-8 multi-byte sequences.
 *
 *   * Structured-Output entry points accept and return the schema /
 *     response_format as TBytes. Convenience string overloads are
 *     provided and are documented to accept only valid Unicode
 *     strings (never raw UTF-8 byte streams that were mis-cast to
 *     AnsiString).
 *
 *   * Every LingoFuse call goes through the LF_xxxEx helpers that are
 *     UTF-8 aware (LF_CreateDataEx, LF_CreateAppEx, LF_CallEx,
 *     LF_Generate_AppNameEx, LF_PrepareClientEx, LF_SetOptionEx,
 *     LF_RegisterNotifyEx, LF_GetStatusEx).
 *
 *   * All comments use  ... form. Braces are not used because
 *     they are error-prone inside JSON strings.
 *
 *   * All diagnostic messages are in English.
 *
 * Z.Json safety rule (applies to every function in this unit)
 * -----------------------------------------------------------
 *   Parae / Assign / LoadFromStream replace the receiver's FInstance.
 *   A TZ_JsonObject child's FInstance points into the parent's
 *   underlying JSON tree; replacing it would leave a dangling pointer
 *   in the parent. Therefore these three methods may ONLY be called
 *   on a root object (Parent = nil). To inject parsed JSON into a
 *   child node, parse on a standalone root, obtain the compact JSON
 *   string via ToJSONString(False), and concatenate it.
 *
 * Dependency policy
 * -----------------
 * This unit imports ONLY the low-level C ABI unit (lingofuse_import).
 * It does NOT import lingofuse_helper.
 *
 * Z framework API usage
 * ---------------------
 *   - TZ_JsonObject / TZ_JsonArray (Z.Json)
 *         Request construction and response parsing.
 *         Key contracts used here:
 *           * TZ_JsonObject.Create() is the ONLY way to make a root.
 *           * TZ_JsonArray must come from A[] / AddObject.
 *           * A[] / O[] auto-create on missing key; use Exists() to test.
 *           * S[] / I[] / B[] return defaults for missing keys.
 *           * Parae(TBytes) is the byte-safe parse entry.
 *           * ToBytes is the byte-safe emit entry.
 *
 *   - TZ_JsonString (Z.Json)
 *         Holds raw UTF-8 JSON payloads. On FPC this is a TUPascalString
 *         (UTF-16); on Delphi it is a TPascalString.
 *
 *   - TAtomVar<SystemString> (Z.Core)
 *         FLastException is written by the notify callback thread and
 *         read by arbitrary caller threads.
 *
 *   - DisposeObject / DisposeObjectAndNil (Z.Core)
 *         All object release. Never raises.
 *
 *   - DoStatus (Z.Status)
 *         Diagnostics only, and only from the caller thread.
 *
 * Notification threading
 * ----------------------
 * LF_RegisterNotifyEx registers a cdecl callback that fires on a
 * LingoFuse background notification thread. It is NOT marshalled to
 * the main thread automatically. Consumers that touch UI must marshal
 * themselves:
 *
 *     procedure TForm1.Do_LLM_Chunk(const SessionId, Text: string);
 *     begin
 *       TThread.Queue(nil,
 *         procedure
 *         begin
 *           OutputMemo.Lines.Add(Text);
 *         end);
 *     end;
 *
 * Structured Output
 * -----------------
 * The client offers entry points that produce a `generate` request
 * carrying options.response_format:
 *
 *   GenerateStructured(content, prompt, responseFormatBytes,
 *                      sessionId, error) -> boolean
 *       Complete response_format as raw JSON bytes.
 *
 *   GenerateWithJsonSchema(content, prompt, schemaName, schemaBytes,
 *                          strict, sessionId, error) -> boolean
 *       Convenience wrapper; assembles the outer
 *       {"type":"json_schema","json_schema":{...}} envelope.
 *
 *   GenerateWithImageFileAndSchema(content, prompt, filePath,
 *                                  schemaName, schemaBytes, strict,
 *                                  sessionId, error) -> boolean
 *       Single image file + JSON Schema in one request.
 *
 *   GenerateWithAttachmentsAndSchema(content, prompt, texts, images,
 *                                    schemaName, schemaBytes, strict,
 *                                    sessionId, error) -> boolean
 *       Full attachment arrays + JSON Schema in one request.
 *
 * Every method also has a `string` convenience overload. The string
 * overloads assume the input is a valid Unicode string and encode it
 * to UTF-8 internally via TEncoding.UTF8. Passing raw UTF-8 bytes
 * mis-cast to AnsiString is undefined behaviour.
 *
 * Structured Output works against llm_proxy and llm_proxy_tool. It
 * does NOT work against llm_service (local llama.cpp path).
 *
 * Author: LingoFuse-pasAgent project
 * =============================================================================
 *)

unit llm_client_v3;

{$DEFINE FPC_DELPHI_MODE}
{$I ..\zCore\src\Z.Define.inc}

interface

uses
  Classes, SysUtils, SyncObjs,
  lingofuse_import,
  Z.Core, Z.Json, Z.PascalStrings, Z.UPascalStrings, Z.Status, Z.UnicodeMixedLib;

var
  API_NAME_STREAM: string = 'llm_stream';
  API_NAME_GENERATE: string = 'generate';
  API_NAME_CREATE_SESSION: string = 'create_session';
  API_NAME_CLOSE_SESSION: string = 'close_session';
  API_NAME_CANCEL_SESSION: string = 'cancel_session';
  API_NAME_LIST_SESSIONS: string = 'list_sessions';
  API_NAME_SET_SYSTEM_MESSAGE: string = 'set_system_message';
  API_NAME_GET_CAPABILITIES: string = 'get_api_capabilities';
  API_NAME_HEALTH: string = 'health';

  (* Attachment limits, mirroring llm_common/attachments.py. *)
  ATTACHMENT_MAX_TEXT_BYTES_PER_FILE: integer = 256 * 1024;
  ATTACHMENT_MAX_TEXT_BYTES_TOTAL: integer = 512 * 1024;
  ATTACHMENT_MAX_IMAGE_B64_PER_FILE: integer = 8 * 1024 * 1024;
  ATTACHMENT_MAX_IMAGE_B64_TOTAL: integer = 16 * 1024 * 1024;
  ATTACHMENT_MAX_NAME_LEN: integer = 256;

  ATTACHMENT_DEFAULT_NAME: string = 'unnamed';
  ATTACHMENT_DEFAULT_TEXT_MIME: string = 'text/plain';
  ATTACHMENT_DEFAULT_IMAGE_MIME: string = 'image/png';

  ATTACHMENT_ALLOWED_IMAGE_MIMES: array[0..3] of string = ('image/png', 'image/jpeg', 'image/jpg', 'image/webp');

  LOG_PREFIX_LLM_CLIENT: string = '[llm_client] ';

type
  TLLMChunkEvent = procedure(const SessionId, Text: string) of object;
  TLLMThinkEvent = procedure(const SessionId, Text: string) of object;
  TLLMFinishEvent = procedure(const SessionId, Reason: string) of object;
  TLLMErrorEvent = procedure(const SessionId, ErrorMsg: string) of object;
  TLLMClosedEvent = procedure(const SessionId, Reason: string) of object;

  (* Attachment records.
     All fields use TZ_JsonString instead of plain `string`. This
     makes explicit release semantics reliable across compilers:
     the record's internal buffer is a managed dynamic array and is
     freed either automatically when the containing array goes out
     of scope or explicitly via ClearTextAttachments /
     ClearImageAttachments. *)
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

  TLLMClient = class
  private
    FApp: TAppHnd;
    FServerApp: string;
    FEndpoint: string;
    FTimeout: integer;

    FConnected: boolean;
    FPrepared: boolean;

    FCurrentSessionId: string;
    FClientName: string;

    FCapabilities: TZ_JsonObject;
    FCapabilitiesRawJson: TZ_JsonString;
    FServerKind: string;

    (* Written by the notify thread, read by anyone. *)
    FLastException: TAtomString;

    FOnChunk: TLLMChunkEvent;
    FOnThink: TLLMThinkEvent;
    FOnFinish: TLLMFinishEvent;
    FOnError: TLLMErrorEvent;
    FOnClosed: TLLMClosedEvent;

    procedure HandleLLMNotify(Input_: TDataHnd);
    function Get_LastException: string;

    procedure DoChunk(const SessionId, Text: string);
    procedure DoThink(const SessionId, Text: string);
    procedure DoFinish(const SessionId, Reason: string);
    procedure DoError(const SessionId, ErrorMsg: string);
    procedure DoClosed(const SessionId, Reason: string);

    procedure CleanupPartialConnect(AExitMainThread: boolean);
    procedure ResetCapabilityState;

    function CallAPI(const APIName: string; const RequestBytes: TBytes; out ResponseBytes: TBytes; out AError: string): boolean;

    class function SafeParseJson(const ARawBytes: TBytes; out AJson: TZ_JsonObject; out AError: string): boolean; static;

    class function CheckResponseCode(const AJson: TZ_JsonObject; const APIName: string; out AError: string): boolean; static;

    class procedure PopulateAttachmentArray(const ATexts: TLLMTextAttachmentArray; const AImages: TLLMImageAttachmentArray;
      const AArray: TZ_JsonArray; out AError: string); static;

    (* Reads an image file into a TLLMImageAttachment record.
       Detects the MIME type from the extension, reads the raw
       bytes, base64 encodes them, and fills Name / Mime / DataB64.
       Returns False and sets AError on any failure. *)
    class function BuildImageAttachmentFromFile(const AFilePath: string; out AImage: TLLMImageAttachment;
      out AError: string): boolean; static;

    (* Assembles the outer response_format envelope from a caller-
       supplied schema name / body / strict flag, and returns it as
       raw UTF-8 bytes ready to be embedded into
       options.response_format.

       Z.Json safety: the schema body is parsed on a STANDALONE root
       (joSchema), then its compact JSON string is concatenated with
       a separately built name/strict fragment. Parae is never
       called on a child of another TZ_JsonObject. *)
    class function BuildSchemaResponseFormatJson(const ASchemaName: string; const ASchemaJsonBytes: TBytes;
      const AStrict: boolean; out AResponseFormatJsonBytes: TBytes; out AError: string): boolean; static;

    (* Unified generate request sender. Supports all four
       combinations of {attachments present / absent} x
       {response_format present / absent}.

       An empty AResponseFormatJsonBytes means "no response_format".

       Z.Json safety: the response_format bytes are parsed on a
       STANDALONE root (joSchema). The resulting compact JSON string
       is appended to the serialized request via string
       concatenation, so we never call Parae on a child of joReq. *)
    function SendGenerateCombined(const AContent, APrompt: string; const ATexts: TLLMTextAttachmentArray;
      const AImages: TLLMImageAttachmentArray; const AResponseFormatJsonBytes: TBytes; var ASessionId: string;
      out AError: string): boolean;
  public
    constructor Create(const AServerApp, AEndpoint: string; ATimeout: integer = 10000);
    destructor Destroy; override;

    function Connect(out ErrorMsg: string): boolean;
    procedure Disconnect;

    (* Session management *)
    function CreateSession(out ASessionId, AError: string): boolean; overload;
    function CreateSession(const ASystemMessage: string; out ASessionId, AError: string): boolean; overload;
    function CloseSession(const ASessionId: string; ACancelRunning: boolean; out AError: string): boolean;
    function CancelSession(const ASessionId: string; out AError: string): boolean;
    function ListSessions(out ASessionsJson, AError: string): boolean;

    (* Generation *)
    function Generate(const AContent, APrompt: string; var ASessionId: string; out AError: string): boolean;

    function GenerateWithAttachments(const AContent, APrompt: string; const ATexts: TLLMTextAttachmentArray;
      const AImages: TLLMImageAttachmentArray; var ASessionId: string; out AError: string): boolean;

    function GenerateWithTextFile(const AContent, APrompt, AFilePath: string; var ASessionId: string; out AError: string): boolean;

    function GenerateWithImageFile(const AContent, APrompt, AFilePath: string; var ASessionId: string; out AError: string): boolean;

    function GenerateCurrent(const AContent, APrompt: string; out AError: string): boolean;

    (* Structured Output - byte-safe primary interface.
       GenerateStructured accepts a complete response_format
       object as raw JSON bytes and forwards it to the proxy.

       Z.Json safety: the response_format bytes are parsed on a
       STANDALONE root (joSchema), then appended to the serialized
       request via string concatenation. Parae is never called on a
       child of joReq. *)
    function GenerateStructured(const AContent, APrompt: string; const AResponseFormatJsonBytes: TBytes;
      var ASessionId: string; out AError: string): boolean; overload;

    (* Structured Output - string convenience overload.
       The input must be a valid Unicode string. It is encoded to
       UTF-8 internally. Do NOT pass raw UTF-8 bytes mis-cast to
       AnsiString - the result will be mangled. *)
    function GenerateStructured(const AContent, APrompt: string; const AResponseFormatJson: string;
      var ASessionId: string; out AError: string): boolean; overload;

    (* Convenience wrapper that assembles the outer response_format
       envelope around a caller-supplied JSON Schema body.
       Byte-safe primary interface. *)
    function GenerateWithJsonSchema(const AContent, APrompt: string; const ASchemaName: string;
      const ASchemaJsonBytes: TBytes; const AStrict: boolean; var ASessionId: string; out AError: string): boolean; overload;

    (* String convenience overload for GenerateWithJsonSchema. *)
    function GenerateWithJsonSchema(const AContent, APrompt: string; const ASchemaName: string;
      const ASchemaJson: string; const AStrict: boolean; var ASessionId: string; out AError: string): boolean; overload;

    (* Single image file + JSON Schema in one request.
       Recommended entry point for a detector-style call. *)
    function GenerateWithImageFileAndSchema(const AContent, APrompt, AFilePath: string; const ASchemaName: string;
      const ASchemaJsonBytes: TBytes; const AStrict: boolean; var ASessionId: string; out AError: string): boolean; overload;

    (* String convenience overload. *)
    function GenerateWithImageFileAndSchema(const AContent, APrompt, AFilePath: string; const ASchemaName: string;
      const ASchemaJson: string; const AStrict: boolean; var ASessionId: string; out AError: string): boolean; overload;

    (* Full attachment arrays + JSON Schema in one request. *)
    function GenerateWithAttachmentsAndSchema(const AContent, APrompt: string; const ATexts: TLLMTextAttachmentArray;
      const AImages: TLLMImageAttachmentArray; const ASchemaName: string; const ASchemaJsonBytes: TBytes;
      const AStrict: boolean; var ASessionId: string; out AError: string): boolean; overload;

    (* String convenience overload. *)
    function GenerateWithAttachmentsAndSchema(const AContent, APrompt: string; const ATexts: TLLMTextAttachmentArray;
      const AImages: TLLMImageAttachmentArray; const ASchemaName: string; const ASchemaJson: string;
      const AStrict: boolean; var ASessionId: string; out AError: string): boolean; overload;

    (* Server-wide settings *)
    function SetSystemMessage(const AMessage: string; out AError: string): boolean;
    function Health(out AHealthJson, AError: string): boolean;

    (* Capability discovery *)
    function GetAPICapabilities(var ACapabilitiesJson: TZ_JsonString; out AError: string): boolean;
    function HasCapabilityInfo: boolean;
    function LLMSupported(const AAPIName: string): boolean;
    function IsToolBridge: boolean;
    function HasVision: boolean;
    function HasAttachments: boolean;

    (* Explicit release helpers for the attachment arrays.
       Deterministic release regardless of compiler-specific
       dynamic-array finalization behaviour. *)
    class procedure ClearTextAttachments(var A: TLLMTextAttachmentArray); static;
    class procedure ClearImageAttachments(var A: TLLMImageAttachmentArray); static;

    property OnChunk: TLLMChunkEvent read FOnChunk write FOnChunk;
    property OnThink: TLLMThinkEvent read FOnThink write FOnThink;
    property OnFinish: TLLMFinishEvent read FOnFinish write FOnFinish;
    property OnError: TLLMErrorEvent read FOnError write FOnError;
    property OnClosed: TLLMClosedEvent read FOnClosed write FOnClosed;

    property CurrentSessionId: string read FCurrentSessionId write FCurrentSessionId;
    property ClientName: string read FClientName;
    property Connected: boolean read FConnected;
    property ServerKind: string read FServerKind;
    property CapabilitiesRawJson: TZ_JsonString read FCapabilitiesRawJson;
    property LastException: string read Get_LastException;
  end;

implementation

(* ----------------------------------------------------------------------------
  Module-local helpers
  ---------------------------------------------------------------------------- *)

procedure LogInfo(const Msg: string);
begin
  DoStatus(LOG_PREFIX_LLM_CLIENT + Msg);
end;

(* Convert a Unicode string to UTF-8 bytes.
   This is the ONLY place where a `string` is converted to bytes.
   Callers of the string-based overloads must pass a valid Unicode
   string, never raw UTF-8 bytes that were mis-cast to AnsiString. *)
function UnicodeStringToUtf8Bytes(const S: string): TBytes;
begin
  if S = '' then
    SetLength(Result, 0)
  else
    Result := TEncoding.UTF8.GetBytes(S);
end;

(* cdecl notify dispatcher. lingofuse_import's notify callbacks are
   free procedures, not of-object methods; we register Self as Trigger
   and bounce through this trampoline. *)
procedure _LLMNotifyDispatch(Trigger: Pointer; Input_: TDataHnd); cdecl;
begin
  if Trigger = nil then
    Exit;
  TLLMClient(Trigger).HandleLLMNotify(Input_);
end;

(* ----------------------------------------------------------------------------
  TLLMClient.SafeParseJson - byte-safe JSON parsing.

  This is the ONLY function in the unit that parses JSON, and it
  goes through TZ_JsonObject.Parae(TBytes). ParseText is never used.

  Contract:
    * Success: AJson <> nil, returns True.
    * Failure: AJson = nil, returns False, AError is non-empty.
  ---------------------------------------------------------------------------- *)

class function TLLMClient.SafeParseJson(const ARawBytes: TBytes; out AJson: TZ_JsonObject; out AError: string): boolean;
begin
  Result := False;
  AJson := nil;
  AError := '';

  if Length(ARawBytes) = 0 then
  begin
    AError := 'Empty JSON bytes';
    Exit;
  end;

  try
    AJson := TZ_JsonObject.Create;
  except
    on E: Exception do
    begin
      AJson := nil;
      AError := 'Failed to create JSON root: ' + E.Message;
      Exit;
    end;
  end;

  try
    if not AJson.Parae(ARawBytes) then
    begin
      DisposeObjectAndNil(AJson);
      AError := 'Invalid JSON bytes';
      Exit;
    end;
    Result := True;
  except
    on E: Exception do
    begin
      DisposeObjectAndNil(AJson);
      AError := 'JSON parse exception: ' + E.Message;
    end;
  end;
end;

(* ----------------------------------------------------------------------------
  TLLMClient.CheckResponseCode

  Every server response uses the envelope:
      { "code": 0, "error": "..." (on failure), <payload> }
  ---------------------------------------------------------------------------- *)

class function TLLMClient.CheckResponseCode(const AJson: TZ_JsonObject; const APIName: string; out AError: string): boolean;
begin
  Result := False;
  AError := '';

  if not AJson.Exists('code') then
  begin
    AError := APIName + ' response missing "code"';
    Exit;
  end;

  if AJson.i['code'] <> 0 then
  begin
    if AJson.Exists('error') then
      AError := AJson.S['error']
    else
      AError := APIName + ' failed (code ' + IntToStr(AJson.i['code']) + ')';
    Exit;
  end;

  Result := True;
end;

(* ----------------------------------------------------------------------------
  TLLMClient.ClearTextAttachments / ClearImageAttachments

  Explicit release helpers for the attachment arrays. Each element is
  cleared field-by-field first, then the array itself is reset to
  length 0.
  ---------------------------------------------------------------------------- *)

class procedure TLLMClient.ClearTextAttachments(var A: TLLMTextAttachmentArray);
var
  i: integer;
begin
  for i := 0 to Length(A) - 1 do
  begin
    A[i].Name.Text := '';
    A[i].Mime.Text := '';
    A[i].Text.Text := '';
  end;
  SetLength(A, 0);
end;

class procedure TLLMClient.ClearImageAttachments(var A: TLLMImageAttachmentArray);
var
  i: integer;
begin
  for i := 0 to Length(A) - 1 do
  begin
    A[i].Name.Text := '';
    A[i].Mime.Text := '';
    A[i].DataB64.Text := '';
  end;
  SetLength(A, 0);
end;

(* ----------------------------------------------------------------------------
  TLLMClient.PopulateAttachmentArray

  Validates every attachment against the size limits and emits the
  JSON array entries. Any violation aborts before any field is
  written.

  Z.Json safety: every entry is a freshly added child of AArray, and
  only field-level APIs (S[...]) are used. No Parae or Assign is ever
  called on a child object.
  ---------------------------------------------------------------------------- *)

class procedure TLLMClient.PopulateAttachmentArray(const ATexts: TLLMTextAttachmentArray; const AImages: TLLMImageAttachmentArray;
  const AArray: TZ_JsonArray; out AError: string);
var
  i: integer;
  totalText, totalB64: int64;
  displayName, Mime: string;
  n: int64;
  obj: TZ_JsonObject;
begin
  AError := '';

  if AArray = nil then
  begin
    AError := 'PopulateAttachmentArray: nil target array';
    Exit;
  end;

  (* Pre-flight: validate all sizes before touching the array. *)

  totalText := 0;
  for i := 0 to Length(ATexts) - 1 do
  begin
    if ATexts[i].Name.Len = 0 then
      displayName := ATTACHMENT_DEFAULT_NAME
    else
      displayName := ATexts[i].Name.Text;

    n := Length(TEncoding.UTF8.GetBytes(ATexts[i].Text.Text));
    if n > ATTACHMENT_MAX_TEXT_BYTES_PER_FILE then
    begin
      AError := Format('Text attachment "%s" is %d bytes, exceeds per-file limit %d', [displayName, n, ATTACHMENT_MAX_TEXT_BYTES_PER_FILE]);
      Exit;
    end;
    totalText := totalText + n;
  end;

  if totalText > ATTACHMENT_MAX_TEXT_BYTES_TOTAL then
  begin
    AError := Format('Cumulative text size %d exceeds total limit %d', [totalText, ATTACHMENT_MAX_TEXT_BYTES_TOTAL]);
    Exit;
  end;

  totalB64 := 0;
  for i := 0 to Length(AImages) - 1 do
  begin
    if AImages[i].Name.Len = 0 then
      displayName := ATTACHMENT_DEFAULT_NAME
    else
      displayName := AImages[i].Name.Text;

    n := AImages[i].DataB64.Len;
    if n = 0 then
    begin
      AError := Format('Image attachment "%s" has empty data_b64', [displayName]);
      Exit;
    end;
    if n > ATTACHMENT_MAX_IMAGE_B64_PER_FILE then
    begin
      AError := Format('Image attachment "%s" is %d base64 chars, exceeds per-file limit %d', [displayName,
        n, ATTACHMENT_MAX_IMAGE_B64_PER_FILE]);
      Exit;
    end;
    totalB64 := totalB64 + n;
  end;

  if totalB64 > ATTACHMENT_MAX_IMAGE_B64_TOTAL then
  begin
    AError := Format('Cumulative image size %d exceeds total limit %d', [totalB64, ATTACHMENT_MAX_IMAGE_B64_TOTAL]);
    Exit;
  end;

  (* Emit text entries. *)
  for i := 0 to Length(ATexts) - 1 do
  begin
    if ATexts[i].Name.Len = 0 then
      displayName := ATTACHMENT_DEFAULT_NAME
    else if ATexts[i].Name.Len > ATTACHMENT_MAX_NAME_LEN then
      displayName := Copy(ATexts[i].Name.Text, 1, ATTACHMENT_MAX_NAME_LEN)
    else
      displayName := ATexts[i].Name.Text;

    if ATexts[i].Mime.Len = 0 then
      Mime := ATTACHMENT_DEFAULT_TEXT_MIME
    else
      Mime := ATexts[i].Mime.Text;

    obj := AArray.AddObject;
    obj.S['kind'] := 'text';
    obj.S['name'] := displayName;
    obj.S['mime'] := Mime;
    obj.S['text'] := ATexts[i].Text.Text;
  end;

  (* Emit image entries. *)
  for i := 0 to Length(AImages) - 1 do
  begin
    if AImages[i].Name.Len = 0 then
      displayName := ATTACHMENT_DEFAULT_NAME
    else if AImages[i].Name.Len > ATTACHMENT_MAX_NAME_LEN then
      displayName := Copy(AImages[i].Name.Text, 1, ATTACHMENT_MAX_NAME_LEN)
    else
      displayName := AImages[i].Name.Text;

    if AImages[i].Mime.Len = 0 then
      Mime := ATTACHMENT_DEFAULT_IMAGE_MIME
    else
      Mime := AImages[i].Mime.Text;

    obj := AArray.AddObject;
    obj.S['kind'] := 'image';
    obj.S['name'] := displayName;
    obj.S['mime'] := Mime;
    obj.S['data_b64'] := AImages[i].DataB64.Text;
  end;
end;

(* ----------------------------------------------------------------------------
  TLLMClient.BuildImageAttachmentFromFile

  Reads an image file into a TLLMImageAttachment record:
      Name    <- ExtractFileName(AFilePath)
      Mime    <- extension-based guess (png/jpeg/webp, default png)
      DataB64 <- base64 of the raw bytes

  umlBase64EncodeBytes consumes its source TBytes as a zero-copy
  optimization; the local rawBytes is therefore NOT reused after the
  call.
  ---------------------------------------------------------------------------- *)

class function TLLMClient.BuildImageAttachmentFromFile(const AFilePath: string; out AImage: TLLMImageAttachment; out AError: string): boolean;
var
  fs: TFileStream;
  rawBytes: TBytes;
  ext: string;
  b64: TPascalString;
  b64Estimate: int64;
begin
  Result := False;
  AError := '';

  AImage.Name.Text := '';
  AImage.Mime.Text := '';
  AImage.DataB64.Text := '';

  if AFilePath = '' then
  begin
    AError := 'BuildImageAttachmentFromFile: empty file path';
    Exit;
  end;

  try
    fs := TFileStream.Create(AFilePath, fmOpenRead or fmShareDenyNone);
  except
    on E: Exception do
    begin
      AError := 'Cannot open image file "' + AFilePath + '": ' + E.Message;
      Exit;
    end;
  end;

  try
    try
      SetLength(rawBytes, fs.Size);
      if fs.Size > 0 then
        fs.ReadBuffer(rawBytes[0], fs.Size);
    except
      on E: Exception do
      begin
        SetLength(rawBytes, 0);
        AError := 'Cannot read image file "' + AFilePath + '": ' + E.Message;
        Exit;
      end;
    end;
  finally
    fs.Free;
  end;

  if Length(rawBytes) = 0 then
  begin
    AError := 'Image file "' + AFilePath + '" is empty (0 bytes).';
    Exit;
  end;

  b64Estimate := ((int64(Length(rawBytes)) + 2) div 3) * 4;
  if b64Estimate > ATTACHMENT_MAX_IMAGE_B64_PER_FILE then
  begin
    AError := Format('Image file "%s" would encode to about %d base64 chars, exceeding per-file limit %d',
      [AFilePath, b64Estimate, ATTACHMENT_MAX_IMAGE_B64_PER_FILE]);
    Exit;
  end;

  AImage.Name.Text := ExtractFileName(AFilePath);

  ext := LowerCase(ExtractFileExt(AFilePath));
  if ext = '.png' then
    AImage.Mime.Text := 'image/png'
  else if (ext = '.jpg') or (ext = '.jpeg') then
    AImage.Mime.Text := 'image/jpeg'
  else if ext = '.webp' then
    AImage.Mime.Text := 'image/webp'
  else
    AImage.Mime.Text := ATTACHMENT_DEFAULT_IMAGE_MIME;

  b64 := '';
  try
    umlBase64EncodeBytes(rawBytes, b64);
  except
    on E: Exception do
    begin
      AError := 'Base64 encoding failed: ' + E.Message;
      Exit;
    end;
  end;

  AImage.DataB64.Text := b64.Text;
  Result := True;
end;

(* ----------------------------------------------------------------------------
  TLLMClient.BuildSchemaResponseFormatJson - byte-safe version.

  Assembles the outer response_format envelope:

      {
        "type": "json_schema",
        "json_schema": {
          "name":   <ASchemaName>,
          "strict": <AStrict>,
          "schema": <parsed ASchemaJsonBytes>
        }
      }

  and returns it as raw UTF-8 bytes.

  Z.Json safety notes
  -------------------
  The original implementation tried to call Parae on a child object:

      joJsonSchema := joRoot.O['json_schema'];
      joJsonSchema.O['schema'].Parae(ASchemaJsonBytes);   // BROKEN

  A child's FInstance points into the parent's underlying JSON tree.
  Parae frees that node and creates a detached replacement, leaving a
  dangling pointer in the parent and causing ToBytes to crash or to
  silently drop the schema field.

  The fix parses the schema body on a STANDALONE root (joSchema),
  then concatenates its compact JSON string with a separately built
  name/strict fragment.

  Empty inputs and invalid schema bytes are rejected up front.
  ---------------------------------------------------------------------------- *)

class function TLLMClient.BuildSchemaResponseFormatJson(const ASchemaName: string; const ASchemaJsonBytes: TBytes;
  const AStrict: boolean; out AResponseFormatJsonBytes: TBytes; out AError: string): boolean;
var
  joSchema, joNameStrict: TZ_JsonObject;
  schemaJson, nameStrictJson, envelopeJson: string;
begin
  Result := False;
  SetLength(AResponseFormatJsonBytes, 0);
  AError := '';

  if ASchemaName = '' then
  begin
    AError := 'BuildSchemaResponseFormatJson: empty schema name';
    Exit;
  end;

  if Length(ASchemaJsonBytes) = 0 then
  begin
    AError := 'BuildSchemaResponseFormatJson: empty schema bytes';
    Exit;
  end;

  // ---- Step 1: parse the schema bytes on a standalone root object ----
  // Never call Parae on a child of another TZ_JsonObject: the child's
  // FInstance points into the parent's underlying JSON tree, and Parae
  // would free that node and create a detached replacement, leaving a
  // dangling pointer in the parent and causing ToBytes to crash or to
  // silently drop the schema field.
  joSchema := TZ_JsonObject.Create;
  try
    if not joSchema.Parae(ASchemaJsonBytes) then
    begin
      AError := 'BuildSchemaResponseFormatJson: schema bytes are not valid JSON';
      Exit;
    end;
    schemaJson := joSchema.ToJSONString(False).Text;
  finally
    DisposeObject(joSchema);
  end;

  // ---- Step 2: build the name/strict fragment with proper escaping ----
  joNameStrict := TZ_JsonObject.Create;
  try
    joNameStrict.S['name'] := ASchemaName;
    joNameStrict.B['strict'] := AStrict;
    nameStrictJson := joNameStrict.ToJSONString(False).Text;
  finally
    DisposeObject(joNameStrict);
  end;

  // nameStrictJson looks like {"name":"...","strict":true}.
  // Strip the trailing '}' so we can append ,"schema":<schemaJson>.
  if (nameStrictJson = '') or (nameStrictJson[Length(nameStrictJson)] <> '}') then
  begin
    AError := 'BuildSchemaResponseFormatJson: internal error building name/strict JSON';
    Exit;
  end;
  Delete(nameStrictJson, Length(nameStrictJson), 1);
  nameStrictJson := nameStrictJson + ',"schema":' + schemaJson + '}';

  // ---- Step 3: assemble the outer response_format envelope ----
  envelopeJson := '{"type":"json_schema","json_schema":' + nameStrictJson + '}';

  AResponseFormatJsonBytes := TEncoding.UTF8.GetBytes(envelopeJson);
  Result := True;
end;

(* ----------------------------------------------------------------------------
  TLLMClient.SendGenerateCombined

  Unified request sender for the combined Structured Output methods.
  Handles all four combinations of {attachments present / absent} x
  {response_format present / absent}.

  An empty AResponseFormatJsonBytes means "no response_format".
  Any non-empty value must already be a valid JSON byte stream.

  Z.Json safety notes
  -------------------
  The original implementation called Parae on a grandchild of joReq:

      joResponseFormat := joReq.O['options'].O['response_format'];
      joResponseFormat.Parae(AResponseFormatJsonBytes);   // BROKEN

  Same corruption as in BuildSchemaResponseFormatJson. The fix parses
  the response_format on a STANDALONE root (joSchema), then appends
  the "options":{"response_format":<schemaJson>} fragment to the
  serialized request via string concatenation.
  ---------------------------------------------------------------------------- *)

function TLLMClient.SendGenerateCombined(const AContent, APrompt: string; const ATexts: TLLMTextAttachmentArray;
  const AImages: TLLMImageAttachmentArray; const AResponseFormatJsonBytes: TBytes; var ASessionId: string; out AError: string): boolean;
var
  joReq, joResp, joSchema: TZ_JsonObject;
  attachmentsArr: TZ_JsonArray;
  reqBytes, respBytes: TBytes;
  newSessionId: string;
  hasAttach: boolean;
  hasSchema: boolean;
  schemaJsonStr, reqJsonStr: string;
begin
  Result := False;
  AError := '';

  if not FConnected then
  begin
    AError := 'Not connected to LingoFuse service';
    Exit;
  end;

  hasAttach := (Length(ATexts) > 0) or (Length(AImages) > 0);
  hasSchema := Length(AResponseFormatJsonBytes) > 0;

  // ---- Pre-parse response_format on a standalone root ----
  // Never call Parae on a child of another TZ_JsonObject; doing so
  // would free the child's underlying node and leave a dangling
  // pointer in the parent.
  schemaJsonStr := '';
  if hasSchema then
  begin
    joSchema := TZ_JsonObject.Create;
    try
      if not joSchema.Parae(AResponseFormatJsonBytes) then
      begin
        AError := 'SendGenerateCombined: response_format bytes are not valid JSON';
        Exit;
      end;
      schemaJsonStr := joSchema.ToJSONString(False).Text;
    finally
      DisposeObject(joSchema);
    end;
  end;

  // ---- Build the request ----
  joReq := TZ_JsonObject.Create;
  try
    joReq.S['content'] := AContent;
    joReq.S['prompt'] := APrompt;

    if ASessionId <> '' then
      joReq.S['session_id'] := ASessionId
    else if FCurrentSessionId <> '' then
      joReq.S['session_id'] := FCurrentSessionId
    else
      joReq.S['client_name'] := FClientName;

    if hasAttach then
    begin
      attachmentsArr := joReq.A['attachments'];
      PopulateAttachmentArray(ATexts, AImages, attachmentsArr, AError);
      if AError <> '' then
        Exit;
    end;

    if hasSchema then
    begin
      // Append "options":{"response_format":<schemaJsonStr>} via string
      // concatenation. This avoids calling Parae on a child of joReq.
      reqJsonStr := joReq.ToJSONString(False).Text;
      if reqJsonStr = '{}' then
        reqJsonStr := '{"options":{"response_format":' + schemaJsonStr + '}}'
      else
      begin
        // Strip the trailing '}' and append the options field.
        Delete(reqJsonStr, Length(reqJsonStr), 1);
        reqJsonStr := reqJsonStr + ',"options":{"response_format":' + schemaJsonStr + '}}';
      end;
      reqBytes := TEncoding.UTF8.GetBytes(reqJsonStr);
    end
    else
      reqBytes := joReq.ToBytes;
  finally
    DisposeObject(joReq);
  end;

  // ---- Send the request and handle the response ----
  if not CallAPI(API_NAME_GENERATE, reqBytes, respBytes, AError) then
    Exit;
  if not SafeParseJson(respBytes, joResp, AError) then
    Exit;

  try
    if not CheckResponseCode(joResp, 'generate', AError) then
      Exit;

    newSessionId := joResp.S['session_id'];
    if newSessionId = '' then
    begin
      AError := 'Server did not return session_id';
      Exit;
    end;

    ASessionId := newSessionId;
    FCurrentSessionId := newSessionId;

    LogInfo(Format('Generate queued (combined). session=%s, texts=%d, images=%d, schema=%s',
      [newSessionId, Length(ATexts), Length(AImages), BoolToStr(hasSchema, True)]));

    Result := True;
  finally
    DisposeObject(joResp);
  end;
end;

(* ----------------------------------------------------------------------------
  TLLMClient.CallAPI

  Wraps the "send request bytes, receive response bytes" pattern using
  the Ex helpers from lingofuse_import:
      LF_CreateDataEx      UTF-8 aware handle creation
      LF_WriteStringBytes  payload + trailing NUL
      LF_CallEx            UTF-8 aware remote call
      LF_ReadStringBytes   NUL-terminated, fault-tolerant read
      LF_FreeData          release
  ---------------------------------------------------------------------------- *)

function TLLMClient.CallAPI(const APIName: string; const RequestBytes: TBytes; out ResponseBytes: TBytes; out AError: string): boolean;
var
  hnd, res: TDataHnd;
begin
  Result := False;
  SetLength(ResponseBytes, 0);
  AError := '';

  if not FConnected then
  begin
    AError := 'Not connected to LingoFuse service';
    Exit;
  end;

  hnd := LF_CreateDataEx(APIName);
  if hnd = nil then
  begin
    AError := 'LF_CreateDataEx returned nil for API "' + APIName + '"';
    Exit;
  end;

  try
    if not LF_WriteStringBytes(hnd, RequestBytes) then
    begin
      AError := 'Failed to write request bytes for API "' + APIName + '"';
      Exit;
    end;

    res := LF_CallEx(FServerApp, hnd, uint64(FTimeout));
    if res = nil then
    begin
      AError := 'LF_CallEx returned nil for API "' + APIName + '" (timeout or network error)';
      Exit;
    end;

    try
      if LF_GetSize(res) <= 0 then
      begin
        AError := 'Empty response from API "' + APIName + '"';
        Exit;
      end;

      if not LF_ReadStringBytes(res, ResponseBytes) then
      begin
        AError := 'Failed to read response bytes for API "' + APIName + '"';
        Exit;
      end;

      if Length(ResponseBytes) = 0 then
      begin
        AError := 'Empty response from API "' + APIName + '"';
        Exit;
      end;

      Result := True;
    finally
      LF_FreeData(res);
    end;
  finally
    LF_FreeData(hnd);
  end;
end;

(* ----------------------------------------------------------------------------
  Lifecycle
  ---------------------------------------------------------------------------- *)

constructor TLLMClient.Create(const AServerApp, AEndpoint: string; ATimeout: integer);
begin
  inherited Create;
  FServerApp := AServerApp;
  FEndpoint := AEndpoint;
  FTimeout := ATimeout;
  FConnected := False;
  FPrepared := False;
  FApp := nil;
  FClientName := '';
  FCurrentSessionId := '';
  FCapabilities := nil;
  FCapabilitiesRawJson := '';
  FServerKind := '';
  FLastException := TAtomString.Create('');
end;

destructor TLLMClient.Destroy;
begin
  Disconnect;
  DisposeObjectAndNil(FCapabilities);
  DisposeObjectAndNil(FLastException);
  inherited;
end;

procedure TLLMClient.ResetCapabilityState;
begin
  DisposeObjectAndNil(FCapabilities);
  FCapabilitiesRawJson := '';
  FServerKind := '';
end;

procedure TLLMClient.CleanupPartialConnect(AExitMainThread: boolean);
begin
  if FApp <> nil then
  begin
    try
      LF_FreeApp(FApp);
    except
      (* Swallow so cleanup never masks the original error. *)
    end;
    FApp := nil;
  end;

  if AExitMainThread and FPrepared then
  begin
    try
      LF_ExitMainThread();
    except
    end;
  end;

  ResetCapabilityState;

  FPrepared := False;
  FConnected := False;
  FClientName := '';
  FCurrentSessionId := '';
end;

function TLLMClient.Connect(out ErrorMsg: string): boolean;
var
  regRet, bindRet: integer;
  capJson: TZ_JsonString;
  capErr: string;
begin
  Result := False;
  ErrorMsg := '';

  if FConnected then
  begin
    Result := True;
    Exit;
  end;

  if FPrepared or (FApp <> nil) then
    CleanupPartialConnect(False);

  try
    LF_ResetPrepare();
    LF_SetOptionEx('Wait_Connection_ReadyOk', 'True');
    LF_SetOptionEx('Overlap_Connection', 'True');

    if LF_PrepareClientEx(FEndpoint, nil) = -1 then
    begin
      ErrorMsg := 'LF_PrepareClientEx failed (address invalid or already in use)';
      CleanupPartialConnect(False);
      Exit;
    end;

    if LF_PrepareDone() <> 1 then
    begin
      ErrorMsg := 'LF_PrepareDone failed (network initialization error)';
      CleanupPartialConnect(False);
      Exit;
    end;

    (* Main thread is now running: any failure must exit it. *)
    FPrepared := True;

    FClientName := LF_Generate_AppNameEx();
    if FClientName = '' then
    begin
      ErrorMsg := 'LF_Generate_AppNameEx returned an empty string';
      CleanupPartialConnect(True);
      Exit;
    end;

    FApp := LF_CreateAppEx(FClientName, 'Dynamic LLM Client (v3.11)');
    if FApp = nil then
    begin
      ErrorMsg := 'LF_CreateAppEx returned nil';
      CleanupPartialConnect(True);
      Exit;
    end;

    regRet := LF_RegisterNotifyEx(FApp, API_NAME_STREAM, 'LLM stream callback', Pointer(Self), @_LLMNotifyDispatch);
    if regRet <> 1 then
    begin
      ErrorMsg := 'Failed to register notify callback for "' + API_NAME_STREAM + '"';
      CleanupPartialConnect(True);
      Exit;
    end;

    bindRet := LF_BindApp(FApp);
    if bindRet = 0 then
    begin
      ErrorMsg := 'LF_BindApp returned 0 - no free client available';
      CleanupPartialConnect(True);
      Exit;
    end;

    FConnected := True;

    (* Best-effort capability probe. Failure is logged, not fatal. *)
    if not GetAPICapabilities(capJson, capErr) then
      LogInfo('Capability probe failed: ' + capErr);

    LogInfo('Connected. ClientName=' + FClientName + ', ServerKind=' + FServerKind);

    Result := True;
  except
    on E: Exception do
    begin
      ErrorMsg := 'Unexpected exception in Connect: ' + E.Message;
      CleanupPartialConnect(FPrepared);
      Result := False;
    end;
  end;
end;

procedure TLLMClient.Disconnect;
begin
  if not (FPrepared or FConnected or (FApp <> nil)) then
    Exit;
  LogInfo('Disconnecting');
  CleanupPartialConnect(True);
end;

(* ----------------------------------------------------------------------------
  Notify handler (runs on the LingoFuse notification thread)
  ---------------------------------------------------------------------------- *)

procedure TLLMClient.HandleLLMNotify(Input_: TDataHnd);
var
  jsBytes: TBytes;
  jo: TZ_JsonObject;
  msgType, SessionId, Text, Reason, Msg: string;
begin
  FLastException.V := '';

  try
    if Input_ = nil then
      Exit;
    if LF_GetSize(Input_) <= 0 then
      Exit;

    (* Byte-safe read: LF_ReadStringBytes is NUL-terminated if present,
       otherwise reads the entire remaining buffer. *)
    jsBytes := LF_ReadStringBytes(Input_);
    if Length(jsBytes) = 0 then
      Exit;

    jo := TZ_JsonObject.Create;
    try
      (* Byte-safe parse: Parae, never ParseText. jo is a root. *)
      if not jo.Parae(jsBytes) then
        Exit;
      if not jo.Exists('type') then
        Exit;

      msgType := jo.S['type'];
      SessionId := jo.S['session_id'];

      if msgType = 'closed' then
      begin
        Reason := jo.S['reason'];
        DoClosed(SessionId, Reason);
        Exit;
      end;

      if msgType = 'chunk' then
      begin
        Text := jo.S['text'];
        if Text <> '' then
          DoChunk(SessionId, Text);
      end
      else if msgType = 'think' then
      begin
        Text := jo.S['text'];
        if Text <> '' then
          DoThink(SessionId, Text);
      end
      else if msgType = 'finish' then
      begin
        Reason := jo.S['reason'];
        DoFinish(SessionId, Reason);
      end
      else if msgType = 'error' then
      begin
        Msg := jo.S['message'];
        DoError(SessionId, Msg);
      end;
    finally
      DisposeObject(jo);
    end;
  except
    on E: Exception do
      FLastException.V := E.ClassName + ': ' + E.Message;
  end;
end;

function TLLMClient.Get_LastException: string;
begin
  Result := FLastException.V;
end;

procedure TLLMClient.DoChunk(const SessionId, Text: string);
begin
  if Assigned(FOnChunk) then FOnChunk(SessionId, Text);
end;

procedure TLLMClient.DoThink(const SessionId, Text: string);
begin
  if Assigned(FOnThink) then FOnThink(SessionId, Text);
end;

procedure TLLMClient.DoFinish(const SessionId, Reason: string);
begin
  if Assigned(FOnFinish) then FOnFinish(SessionId, Reason);
end;

procedure TLLMClient.DoError(const SessionId, ErrorMsg: string);
begin
  if Assigned(FOnError) then FOnError(SessionId, ErrorMsg);
end;

procedure TLLMClient.DoClosed(const SessionId, Reason: string);
begin
  if Assigned(FOnClosed) then FOnClosed(SessionId, Reason);
end;

(* ----------------------------------------------------------------------------
  Session management
  ---------------------------------------------------------------------------- *)

function TLLMClient.CreateSession(out ASessionId, AError: string): boolean;
begin
  Result := CreateSession('', ASessionId, AError);
end;

function TLLMClient.CreateSession(const ASystemMessage: string; out ASessionId, AError: string): boolean;
var
  joReq, joResp: TZ_JsonObject;
  reqBytes, respBytes: TBytes;
begin
  Result := False;
  ASessionId := '';
  AError := '';

  joReq := TZ_JsonObject.Create;
  try
    joReq.S['client_name'] := FClientName;
    if ASystemMessage <> '' then
      joReq.S['system_message'] := ASystemMessage;
    reqBytes := joReq.ToBytes;
  finally
    DisposeObject(joReq);
  end;

  if not CallAPI(API_NAME_CREATE_SESSION, reqBytes, respBytes, AError) then
    Exit;
  if not SafeParseJson(respBytes, joResp, AError) then
    Exit;

  try
    if not CheckResponseCode(joResp, 'create_session', AError) then
      Exit;

    ASessionId := joResp.S['session_id'];
    if ASessionId = '' then
    begin
      AError := 'Server did not return session_id';
      Exit;
    end;

    FCurrentSessionId := ASessionId;
    LogInfo('Session created: ' + ASessionId + ' (system_message=' + IntToStr(Length(ASystemMessage)) + ' chars)');
    Result := True;
  finally
    DisposeObject(joResp);
  end;
end;

function TLLMClient.CloseSession(const ASessionId: string; ACancelRunning: boolean; out AError: string): boolean;
var
  joReq, joResp: TZ_JsonObject;
  reqBytes, respBytes: TBytes;
begin
  Result := False;
  AError := '';

  if ASessionId = '' then
  begin
    AError := 'CloseSession: empty session_id';
    Exit;
  end;

  joReq := TZ_JsonObject.Create;
  try
    joReq.S['session_id'] := ASessionId;
    joReq.B['cancel_running'] := ACancelRunning;
    reqBytes := joReq.ToBytes;
  finally
    DisposeObject(joReq);
  end;

  if not CallAPI(API_NAME_CLOSE_SESSION, reqBytes, respBytes, AError) then
    Exit;
  if not SafeParseJson(respBytes, joResp, AError) then
    Exit;

  try
    if not CheckResponseCode(joResp, 'close_session', AError) then
      Exit;

    if FCurrentSessionId = ASessionId then
      FCurrentSessionId := '';
    LogInfo('Session closed: ' + ASessionId);
    Result := True;
  finally
    DisposeObject(joResp);
  end;
end;

function TLLMClient.CancelSession(const ASessionId: string; out AError: string): boolean;
var
  joReq, joResp: TZ_JsonObject;
  reqBytes, respBytes: TBytes;
begin
  Result := False;
  AError := '';

  if ASessionId = '' then
  begin
    AError := 'CancelSession: empty session_id';
    Exit;
  end;

  joReq := TZ_JsonObject.Create;
  try
    joReq.S['session_id'] := ASessionId;
    reqBytes := joReq.ToBytes;
  finally
    DisposeObject(joReq);
  end;

  if not CallAPI(API_NAME_CANCEL_SESSION, reqBytes, respBytes, AError) then
    Exit;
  if not SafeParseJson(respBytes, joResp, AError) then
    Exit;

  try
    if not CheckResponseCode(joResp, 'cancel_session', AError) then
      Exit;
    LogInfo('Cancel requested for session: ' + ASessionId);
    Result := True;
  finally
    DisposeObject(joResp);
  end;
end;

function TLLMClient.ListSessions(out ASessionsJson, AError: string): boolean;
var
  joReq, joResp: TZ_JsonObject;
  reqBytes, respBytes: TBytes;
begin
  Result := False;
  ASessionsJson := '';
  AError := '';

  joReq := TZ_JsonObject.Create;
  try
    joReq.S['client_name'] := FClientName;
    reqBytes := joReq.ToBytes;
  finally
    DisposeObject(joReq);
  end;

  if not CallAPI(API_NAME_LIST_SESSIONS, reqBytes, respBytes, AError) then
    Exit;
  if not SafeParseJson(respBytes, joResp, AError) then
    Exit;

  try
    if not CheckResponseCode(joResp, 'list_sessions', AError) then
      Exit;
    ASessionsJson := TEncoding.UTF8.GetString(respBytes);
    Result := True;
  finally
    DisposeObject(joResp);
  end;
end;

(* ----------------------------------------------------------------------------
  Generate (no attachments)
  ---------------------------------------------------------------------------- *)

function TLLMClient.Generate(const AContent, APrompt: string; var ASessionId: string; out AError: string): boolean;
var
  joReq, joResp: TZ_JsonObject;
  reqBytes, respBytes: TBytes;
  newSessionId: string;
begin
  Result := False;
  AError := '';

  if not FConnected then
  begin
    AError := 'Not connected to LingoFuse service';
    Exit;
  end;

  joReq := TZ_JsonObject.Create;
  try
    joReq.S['content'] := AContent;
    joReq.S['prompt'] := APrompt;

    if ASessionId <> '' then
      joReq.S['session_id'] := ASessionId
    else if FCurrentSessionId <> '' then
      joReq.S['session_id'] := FCurrentSessionId
    else
      joReq.S['client_name'] := FClientName;

    reqBytes := joReq.ToBytes;
  finally
    DisposeObject(joReq);
  end;

  if not CallAPI(API_NAME_GENERATE, reqBytes, respBytes, AError) then
    Exit;
  if not SafeParseJson(respBytes, joResp, AError) then
    Exit;

  try
    if not CheckResponseCode(joResp, 'generate', AError) then
      Exit;

    newSessionId := joResp.S['session_id'];
    if newSessionId = '' then
    begin
      AError := 'Server did not return session_id';
      Exit;
    end;

    ASessionId := newSessionId;
    FCurrentSessionId := newSessionId;

    if joResp.Exists('task_id') then
      LogInfo('Generate queued. session=' + newSessionId + ', task=' + joResp.S['task_id'])
    else
      LogInfo('Generate queued. session=' + newSessionId);

    Result := True;
  finally
    DisposeObject(joResp);
  end;
end;

(* ----------------------------------------------------------------------------
  Generate (with attachments)
  ---------------------------------------------------------------------------- *)

function TLLMClient.GenerateWithAttachments(const AContent, APrompt: string; const ATexts: TLLMTextAttachmentArray;
  const AImages: TLLMImageAttachmentArray; var ASessionId: string; out AError: string): boolean;
var
  joReq, joResp: TZ_JsonObject;
  attachmentsArr: TZ_JsonArray;
  reqBytes, respBytes: TBytes;
  newSessionId: string;
begin
  Result := False;
  AError := '';

  if not FConnected then
  begin
    AError := 'Not connected to LingoFuse service';
    Exit;
  end;

  if (Length(ATexts) = 0) and (Length(AImages) = 0) then
  begin
    Result := Generate(AContent, APrompt, ASessionId, AError);
    Exit;
  end;

  joReq := TZ_JsonObject.Create;
  try
    joReq.S['content'] := AContent;
    joReq.S['prompt'] := APrompt;

    if ASessionId <> '' then
      joReq.S['session_id'] := ASessionId
    else if FCurrentSessionId <> '' then
      joReq.S['session_id'] := FCurrentSessionId
    else
      joReq.S['client_name'] := FClientName;

    attachmentsArr := joReq.A['attachments'];
    PopulateAttachmentArray(ATexts, AImages, attachmentsArr, AError);
    if AError <> '' then
      Exit;

    reqBytes := joReq.ToBytes;
  finally
    DisposeObject(joReq);
  end;

  if not CallAPI(API_NAME_GENERATE, reqBytes, respBytes, AError) then
    Exit;
  if not SafeParseJson(respBytes, joResp, AError) then
    Exit;

  try
    if not CheckResponseCode(joResp, 'generate', AError) then
      Exit;

    newSessionId := joResp.S['session_id'];
    if newSessionId = '' then
    begin
      AError := 'Server did not return session_id';
      Exit;
    end;

    ASessionId := newSessionId;
    FCurrentSessionId := newSessionId;

    LogInfo('Generate queued with attachments. session=' + newSessionId + ', texts=' + IntToStr(Length(ATexts)) +
      ', images=' + IntToStr(Length(AImages)));

    Result := True;
  finally
    DisposeObject(joResp);
  end;
end;

function TLLMClient.GenerateWithTextFile(const AContent, APrompt, AFilePath: string; var ASessionId: string; out AError: string): boolean;
var
  texts: TLLMTextAttachmentArray;
  images: TLLMImageAttachmentArray;
  fs: TFileStream;
  rawBytes: TBytes;
  Decoded: string;
begin
  Result := False;
  AError := '';

  SetLength(texts, 1);
  SetLength(images, 0);
  try
    try
      fs := TFileStream.Create(AFilePath, fmOpenRead or fmShareDenyNone);
    except
      on E: Exception do
      begin
        AError := 'Cannot open text file "' + AFilePath + '": ' + E.Message;
        Exit;
      end;
    end;

    try
      SetLength(rawBytes, fs.Size);
      if fs.Size > 0 then
        fs.ReadBuffer(rawBytes[0], fs.Size);
    finally
      fs.Free;
    end;

    texts[0].Name.Text := ExtractFileName(AFilePath);
    texts[0].Mime.Text := '';

    if Length(rawBytes) = 0 then
      texts[0].Text.Text := ''
    else
    begin
      try
        Decoded := TEncoding.UTF8.GetString(rawBytes);
      except
        (* Fall back to GBK (code page 936); if that also fails,
           treat the bytes as Latin-1 by copy. *)
        try
          Decoded := TEncoding.GetEncoding(936).GetString(rawBytes);
        except
          SetLength(Decoded, Length(rawBytes));
          if Length(rawBytes) > 0 then
            Move(rawBytes[0], Decoded[1], Length(rawBytes));
        end;
      end;
      texts[0].Text.Text := Decoded;
    end;

    Result := GenerateWithAttachments(AContent, APrompt, texts, images, ASessionId, AError);
  finally
    ClearTextAttachments(texts);
    ClearImageAttachments(images);
  end;
end;

function TLLMClient.GenerateWithImageFile(const AContent, APrompt, AFilePath: string; var ASessionId: string; out AError: string): boolean;
var
  texts: TLLMTextAttachmentArray;
  images: TLLMImageAttachmentArray;
  fs: TFileStream;
  rawBytes: TBytes;
  ext: string;
  b64: TPascalString;
begin
  Result := False;
  AError := '';

  SetLength(texts, 0);
  SetLength(images, 1);
  try
    try
      fs := TFileStream.Create(AFilePath, fmOpenRead or fmShareDenyNone);
    except
      on E: Exception do
      begin
        AError := 'Cannot open image file "' + AFilePath + '": ' + E.Message;
        Exit;
      end;
    end;

    try
      SetLength(rawBytes, fs.Size);
      if fs.Size > 0 then
        fs.ReadBuffer(rawBytes[0], fs.Size);
    finally
      fs.Free;
    end;

    if Length(rawBytes) = 0 then
    begin
      AError := 'Image file "' + AFilePath + '" is empty (0 bytes).';
      Exit;
    end;

    images[0].Name.Text := ExtractFileName(AFilePath);

    ext := LowerCase(ExtractFileExt(AFilePath));
    if ext = '.png' then
      images[0].Mime.Text := 'image/png'
    else if (ext = '.jpg') or (ext = '.jpeg') then
      images[0].Mime.Text := 'image/jpeg'
    else if ext = '.webp' then
      images[0].Mime.Text := 'image/webp'
    else
      images[0].Mime.Text := ATTACHMENT_DEFAULT_IMAGE_MIME;

    b64 := '';
    umlBase64EncodeBytes(rawBytes, b64);
    images[0].DataB64.Text := b64.Text;

    Result := GenerateWithAttachments(AContent, APrompt, texts, images, ASessionId, AError);
  finally
    ClearTextAttachments(texts);
    ClearImageAttachments(images);
  end;
end;

function TLLMClient.GenerateCurrent(const AContent, APrompt: string; out AError: string): boolean;
var
  sid: string;
begin
  sid := FCurrentSessionId;
  Result := Generate(AContent, APrompt, sid, AError);
end;

(* ----------------------------------------------------------------------------
  Structured Output - byte-safe primary interface

  Z.Json safety notes
  -------------------
  The original implementation called Parae on a grandchild of joReq:

      joResponseFormat := joReq.O['options'].O['response_format'];
      joResponseFormat.Parae(AResponseFormatJsonBytes);   // BROKEN

  The fix parses the response_format on a STANDALONE root (joSchema),
  then appends the "options":{"response_format":<schemaJson>} fragment
  to the serialized request via string concatenation. This never
  touches any child node pointer.
  ---------------------------------------------------------------------------- *)

function TLLMClient.GenerateStructured(const AContent, APrompt: string; const AResponseFormatJsonBytes: TBytes;
  var ASessionId: string; out AError: string): boolean;
var
  joReq, joResp, joSchema: TZ_JsonObject;
  reqBytes, respBytes: TBytes;
  newSessionId: string;
  schemaJsonStr, reqJsonStr: string;
begin
  Result := False;
  AError := '';

  if not FConnected then
  begin
    AError := 'Not connected to LingoFuse service';
    Exit;
  end;

  if Length(AResponseFormatJsonBytes) = 0 then
  begin
    AError := 'GenerateStructured: empty response_format bytes';
    Exit;
  end;

  // ---- Pre-parse response_format on a standalone root ----
  // Never call Parae on a child of another TZ_JsonObject; doing so
  // would free the child's underlying node and leave a dangling
  // pointer in the parent.
  joSchema := TZ_JsonObject.Create;
  try
    if not joSchema.Parae(AResponseFormatJsonBytes) then
    begin
      AError := 'GenerateStructured: response_format bytes are not valid JSON';
      Exit;
    end;
    schemaJsonStr := joSchema.ToJSONString(False).Text;
  finally
    DisposeObject(joSchema);
  end;

  // ---- Build the request ----
  joReq := TZ_JsonObject.Create;
  try
    joReq.S['content'] := AContent;
    joReq.S['prompt'] := APrompt;

    if ASessionId <> '' then
      joReq.S['session_id'] := ASessionId
    else if FCurrentSessionId <> '' then
      joReq.S['session_id'] := FCurrentSessionId
    else
      joReq.S['client_name'] := FClientName;

    // Append "options":{"response_format":<schemaJsonStr>} via string
    // concatenation. This avoids calling Parae on a child of joReq.
    reqJsonStr := joReq.ToJSONString(False).Text;
    if reqJsonStr = '{}' then
      reqJsonStr := '{"options":{"response_format":' + schemaJsonStr + '}}'
    else
    begin
      // Strip the trailing '}' and append the options field.
      Delete(reqJsonStr, Length(reqJsonStr), 1);
      reqJsonStr := reqJsonStr + ',"options":{"response_format":' + schemaJsonStr + '}}';
    end;
    reqBytes := TEncoding.UTF8.GetBytes(reqJsonStr);
  finally
    DisposeObject(joReq);
  end;

  // ---- Send the request and handle the response ----
  if not CallAPI(API_NAME_GENERATE, reqBytes, respBytes, AError) then
    Exit;
  if not SafeParseJson(respBytes, joResp, AError) then
    Exit;

  try
    if not CheckResponseCode(joResp, 'generate', AError) then
      Exit;

    newSessionId := joResp.S['session_id'];
    if newSessionId = '' then
    begin
      AError := 'Server did not return session_id';
      Exit;
    end;

    ASessionId := newSessionId;
    FCurrentSessionId := newSessionId;

    LogInfo('Generate queued with response_format. session=' + newSessionId);
    Result := True;
  finally
    DisposeObject(joResp);
  end;
end;

(* Structured Output - string convenience overload.
   The input must be a valid Unicode string. It is encoded to UTF-8
   internally. Do NOT pass raw UTF-8 bytes mis-cast to AnsiString -
   the result will be mangled. *)
function TLLMClient.GenerateStructured(const AContent, APrompt: string; const AResponseFormatJson: string;
  var ASessionId: string; out AError: string): boolean;
var
  bytes: TBytes;
begin
  bytes := UnicodeStringToUtf8Bytes(AResponseFormatJson);
  Result := GenerateStructured(AContent, APrompt, bytes, ASessionId, AError);
end;

(* ----------------------------------------------------------------------------
  Structured Output - schema-name + body + strict, byte-safe version
  ---------------------------------------------------------------------------- *)

function TLLMClient.GenerateWithJsonSchema(const AContent, APrompt: string; const ASchemaName: string;
  const ASchemaJsonBytes: TBytes; const AStrict: boolean; var ASessionId: string; out AError: string): boolean;
var
  responseFormatBytes: TBytes;
begin
  Result := False;
  AError := '';

  if not BuildSchemaResponseFormatJson(ASchemaName, ASchemaJsonBytes, AStrict, responseFormatBytes, AError) then
    Exit;

  Result := GenerateStructured(AContent, APrompt, responseFormatBytes, ASessionId, AError);
end;

(* String convenience overload. *)
function TLLMClient.GenerateWithJsonSchema(const AContent, APrompt: string; const ASchemaName: string;
  const ASchemaJson: string; const AStrict: boolean; var ASessionId: string; out AError: string): boolean;
var
  bytes: TBytes;
begin
  bytes := UnicodeStringToUtf8Bytes(ASchemaJson);
  Result := GenerateWithJsonSchema(AContent, APrompt, ASchemaName, bytes, AStrict, ASessionId, AError);
end;

(* ----------------------------------------------------------------------------
  Structured Output + single image file, byte-safe version
  ---------------------------------------------------------------------------- *)

function TLLMClient.GenerateWithImageFileAndSchema(const AContent, APrompt, AFilePath: string; const ASchemaName: string;
  const ASchemaJsonBytes: TBytes; const AStrict: boolean; var ASessionId: string; out AError: string): boolean;
var
  texts: TLLMTextAttachmentArray;
  images: TLLMImageAttachmentArray;
  responseFormatBytes: TBytes;
begin
  Result := False;
  AError := '';

  if not FConnected then
  begin
    AError := 'Not connected to LingoFuse service';
    Exit;
  end;

  if not BuildSchemaResponseFormatJson(ASchemaName, ASchemaJsonBytes, AStrict, responseFormatBytes, AError) then
    Exit;

  SetLength(texts, 0);
  SetLength(images, 1);
  try
    if not BuildImageAttachmentFromFile(AFilePath, images[0], AError) then
      Exit;

    Result := SendGenerateCombined(AContent, APrompt, texts, images, responseFormatBytes, ASessionId, AError);
  finally
    ClearTextAttachments(texts);
    ClearImageAttachments(images);
  end;
end;

(* String convenience overload. *)
function TLLMClient.GenerateWithImageFileAndSchema(const AContent, APrompt, AFilePath: string; const ASchemaName: string;
  const ASchemaJson: string; const AStrict: boolean; var ASessionId: string; out AError: string): boolean;
var
  bytes: TBytes;
begin
  bytes := UnicodeStringToUtf8Bytes(ASchemaJson);
  Result := GenerateWithImageFileAndSchema(AContent, APrompt, AFilePath, ASchemaName, bytes, AStrict, ASessionId, AError);
end;

(* ----------------------------------------------------------------------------
  Structured Output + full attachment arrays, byte-safe version
  ---------------------------------------------------------------------------- *)

function TLLMClient.GenerateWithAttachmentsAndSchema(const AContent, APrompt: string; const ATexts: TLLMTextAttachmentArray;
  const AImages: TLLMImageAttachmentArray; const ASchemaName: string; const ASchemaJsonBytes: TBytes; const AStrict: boolean;
  var ASessionId: string; out AError: string): boolean;
var
  responseFormatBytes: TBytes;
begin
  Result := False;
  AError := '';

  if not FConnected then
  begin
    AError := 'Not connected to LingoFuse service';
    Exit;
  end;

  if not BuildSchemaResponseFormatJson(ASchemaName, ASchemaJsonBytes, AStrict, responseFormatBytes, AError) then
    Exit;

  Result := SendGenerateCombined(AContent, APrompt, ATexts, AImages, responseFormatBytes, ASessionId, AError);
end;

(* String convenience overload. *)
function TLLMClient.GenerateWithAttachmentsAndSchema(const AContent, APrompt: string; const ATexts: TLLMTextAttachmentArray;
  const AImages: TLLMImageAttachmentArray; const ASchemaName: string; const ASchemaJson: string; const AStrict: boolean;
  var ASessionId: string; out AError: string): boolean;
var
  bytes: TBytes;
begin
  bytes := UnicodeStringToUtf8Bytes(ASchemaJson);
  Result := GenerateWithAttachmentsAndSchema(AContent, APrompt, ATexts, AImages, ASchemaName, bytes, AStrict, ASessionId, AError);
end;

(* ----------------------------------------------------------------------------
  SetSystemMessage
  ---------------------------------------------------------------------------- *)

function TLLMClient.SetSystemMessage(const AMessage: string; out AError: string): boolean;
var
  joReq, joResp: TZ_JsonObject;
  reqBytes, respBytes: TBytes;
  kind: string;
begin
  Result := False;
  AError := '';

  if HasCapabilityInfo and (not LLMSupported(API_NAME_SET_SYSTEM_MESSAGE)) then
  begin
    kind := FServerKind;
    if kind = '' then
      kind := 'unknown';

    AError := 'set_system_message is not supported by this server (kind=' + kind +
      '). The system message is fixed at session creation time. ' + 'Pass it to CreateSession, or close the current session and ' +
      'create a new one with the desired system message.';
    Exit;
  end;

  joReq := TZ_JsonObject.Create;
  try
    joReq.S['content'] := AMessage;
    reqBytes := joReq.ToBytes;
  finally
    DisposeObject(joReq);
  end;

  if not CallAPI(API_NAME_SET_SYSTEM_MESSAGE, reqBytes, respBytes, AError) then
    Exit;
  if not SafeParseJson(respBytes, joResp, AError) then
    Exit;

  try
    if not CheckResponseCode(joResp, 'set_system_message', AError) then
      Exit;
    LogInfo('Global system message updated (applies to new sessions only)');
    Result := True;
  finally
    DisposeObject(joResp);
  end;
end;

(* ----------------------------------------------------------------------------
  Health
  ---------------------------------------------------------------------------- *)

function TLLMClient.Health(out AHealthJson, AError: string): boolean;
var
  joReq, joResp: TZ_JsonObject;
  reqBytes, respBytes: TBytes;
begin
  Result := False;
  AHealthJson := '';
  AError := '';

  joReq := TZ_JsonObject.Create;
  try
    reqBytes := joReq.ToBytes;
  finally
    DisposeObject(joReq);
  end;

  if not CallAPI(API_NAME_HEALTH, reqBytes, respBytes, AError) then
    Exit;
  if not SafeParseJson(respBytes, joResp, AError) then
    Exit;

  try
    if not CheckResponseCode(joResp, 'health', AError) then
      Exit;
    AHealthJson := TEncoding.UTF8.GetString(respBytes);
    Result := True;
  finally
    DisposeObject(joResp);
  end;
end;

(* ----------------------------------------------------------------------------
  Capability discovery

  Z.Json safety note: FCapabilities is created as a fresh root
  (TZ_JsonObject.Create), and Assign targets it as the destination.
  Assign only calls SaveToStream on the source and LoadFromStream on
  the destination root, so the source child node of joResp is never
  mutated.
  ---------------------------------------------------------------------------- *)

function TLLMClient.GetAPICapabilities(var ACapabilitiesJson: TZ_JsonString; out AError: string): boolean;
var
  joReq, joResp: TZ_JsonObject;
  reqBytes, respBytes: TBytes;
  capsSrc: TZ_JsonObject;
begin
  Result := False;
  ACapabilitiesJson := '';
  AError := '';

  ResetCapabilityState;

  joReq := TZ_JsonObject.Create;
  try
    reqBytes := joReq.ToBytes;
  finally
    DisposeObject(joReq);
  end;

  if not CallAPI(API_NAME_GET_CAPABILITIES, reqBytes, respBytes, AError) then
    Exit;
  if not SafeParseJson(respBytes, joResp, AError) then
    Exit;

  try
    if not CheckResponseCode(joResp, 'get_api_capabilities', AError) then
      Exit;

    if not joResp.Exists('capabilities') then
    begin
      AError := 'get_api_capabilities response missing "capabilities"';
      Exit;
    end;

    capsSrc := joResp.O['capabilities'];
    FCapabilities := TZ_JsonObject.Create;
    try
      FCapabilities.Assign(capsSrc);
    except
      on E: Exception do
      begin
        DisposeObjectAndNil(FCapabilities);
        AError := 'Failed to clone capabilities: ' + E.Message;
        Exit;
      end;
    end;

    if joResp.Exists('server_kind') then
      FServerKind := joResp.S['server_kind']
    else
      FServerKind := '';

    FCapabilitiesRawJson.bytes := respBytes;
    ACapabilitiesJson := FCapabilitiesRawJson;
    Result := True;
  finally
    DisposeObject(joResp);
  end;
end;

function TLLMClient.HasCapabilityInfo: boolean;
begin
  Result := FCapabilities <> nil;
end;

function TLLMClient.LLMSupported(const AAPIName: string): boolean;
begin
  if FCapabilities = nil then
    Result := False
  else if FCapabilities.Exists(AAPIName) then
    Result := FCapabilities.i[AAPIName] = 1
  else
    Result := False;
end;

function TLLMClient.IsToolBridge: boolean;
begin
  Result := LLMSupported('tools') and LLMSupported('tool_calls');
end;

function TLLMClient.HasVision: boolean;
begin
  Result := LLMSupported('vision');
end;

function TLLMClient.HasAttachments: boolean;
begin
  Result := LLMSupported('attachments');
end;

end.
