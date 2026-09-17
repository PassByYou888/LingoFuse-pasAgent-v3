(*
 * =============================================================================
 * llm_client_v3 — Pascal client for LingoFuse LLM Service (v3.8)
 * =============================================================================
 *
 * High-level, event-driven Pascal client for a LingoFuse LLM service.
 * Speaks the structured streaming protocol defined by llm_service.py /
 * llm_proxy.py / llm_proxy_tool.py (v3.0 protocol and later).
 *
 * Three backends, one protocol
 * ----------------------------
 *   llm_service.py     (server_kind = "service")
 *       Local inference server (llama.cpp / transformers). Owns an
 *       in-process model, session registry, and global default
 *       system message.
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
 * Dependency policy
 * -----------------
 * This unit imports ONLY the low-level C ABI unit (lingofuse_import).
 * It does NOT import lingofuse_helper. Every convenience operation is
 * provided by the *Ex variants of lingofuse_import itself:
 *
 *     LF_CreateDataEx          LF_SetOptionEx
 *     LF_CreateAppEx           LF_PrepareClientEx
 *     LF_WriteStringBytes      LF_ReadStringBytes
 *     LF_CallEx                LF_Generate_AppNameEx
 *     LF_RegisterNotifyEx
 *
 * Z framework API usage
 * ---------------------
 *   - TZ_JsonObject / TZ_JsonArray (Z.Json)
 *         All request construction and response parsing.
 *         Key contracts:
 *           * TZ_JsonObject.Create() is the ONLY way to make a root.
 *           * TZ_JsonArray must come from A[] / AddObject, never created
 *             standalone.
 *           * A[] / O[] auto-create on missing key; use Exists() to test.
 *           * S[] / I[] return defaults for missing keys, never raise.
 *           * Parae() is a misspelled Parse() with an empty-buffer risk;
 *             this unit guards with a length check before Parae().
 *
 *   - TZ_JsonString (Z.Json)
 *         Holds raw UTF-8 JSON payloads. On FPC this is a TUPascalString
 *         (UTF-16); on Delphi it is a TPascalString. Assigning a TBytes
 *         buffer to .Bytes triggers UTF-8 decode into the internal UTF-16
 *         representation; reading .Text returns the decoded SystemString.
 *
 *         IMPORTANT: all string fields inside the dynamic arrays
 *         TLLMTextAttachmentArray and TLLMImageAttachmentArray use
 *         TZ_JsonString instead of plain `string`. This makes explicit
 *         release semantics reliable across compilers: the record's
 *         internal buffer is a managed dynamic array and is freed either
 *         automatically when the containing array goes out of scope or
 *         explicitly via ClearTextAttachments / ClearImageAttachments.
 *
 *   - TAtomVar<SystemString> (Z.Core)
 *         FLastException is written by the notify callback thread and
 *         read by arbitrary caller threads. Wrapped in TAtomString for
 *         lock-free atomic read/write.
 *
 *   - DisposeObject / DisposeObjectAndNil (Z.Core)
 *         All object release. Never raise.
 *
 *   - DoStatus (Z.Status)
 *         Diagnostics only, and only from the caller thread. Never from
 *         the notify callback (background thread), to avoid re-entrancy
 *         on the Z.Status queue.
 *
 *   - umlBase64EncodeBytes (Z.UnicodeMixedLib)
 *         Image attachment encoding. Consumes the source TBytes as a
 *         zero-copy optimization.
 *
 * Notification threading
 * ----------------------
 * LF_RegisterNotifyEx registers a cdecl callback that fires on a
 * LingoFuse background notification thread. It is NOT marshalled to the
 * main thread automatically. User event handlers (OnChunk, OnThink,
 * OnFinish, OnError, OnClosed) run on the notification thread; consumers
 * that touch UI must marshal themselves:
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
 * API capability discovery
 * ------------------------
 * get_api_capabilities returns:
 *
 *     {
 *       "code": 0,
 *       "server_kind": "service" | "proxy",
 *       "capabilities": { "<api_name>": 0|1, ...,
 *                         "attachments": 0|1, "vision": 0|1,
 *                         "tools": 0|1, "tool_calls": 0|1,
 *                         "tool_results": 0|1 }
 *     }
 *
 * Helpers: HasCapabilityInfo, LLMSupported(name), ServerKind,
 *          IsToolBridge, HasVision, HasAttachments, CapabilitiesRawJson.
 *
 * set_system_message is NOT supported by the two proxies. The client
 * short-circuits locally when the capability matrix has been fetched.
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

  (* Attachment limits, mirroring llm_common/attachments.py. Local
     pre-check produces a precise error naming the file and its size. *)
  ATTACHMENT_MAX_TEXT_BYTES_PER_FILE: integer = 256 * 1024;
  ATTACHMENT_MAX_TEXT_BYTES_TOTAL: integer = 512 * 1024;
  ATTACHMENT_MAX_IMAGE_B64_PER_FILE: integer = 8 * 1024 * 1024;
  ATTACHMENT_MAX_IMAGE_B64_TOTAL: integer = 16 * 1024 * 1024;
  ATTACHMENT_MAX_NAME_LEN: integer = 256;

  ATTACHMENT_DEFAULT_NAME: string = 'unnamed';
  ATTACHMENT_DEFAULT_TEXT_MIME: string = 'text/plain';
  ATTACHMENT_DEFAULT_IMAGE_MIME: string = 'image/png';

  ATTACHMENT_ALLOWED_IMAGE_MIMES: array[0..3] of string = (
    'image/png',
    'image/jpeg',
    'image/jpg',
    'image/webp'
    );

  LOG_PREFIX_LLM_CLIENT: string = '[llm_client] ';

type
  TLLMChunkEvent = procedure(const SessionId, Text: string) of object;
  TLLMThinkEvent = procedure(const SessionId, Text: string) of object;
  TLLMFinishEvent = procedure(const SessionId, Reason: string) of object;
  TLLMErrorEvent = procedure(const SessionId, ErrorMsg: string) of object;
  TLLMClosedEvent = procedure(const SessionId, Reason: string) of object;

  (* Attachment records.
     IMPORTANT: all fields use TZ_JsonString instead of plain `string`.
     Dynamic arrays of records are notoriously fragile when the record
     contains plain `string` fields: some compiler configurations do not
     reliably finalize them on SetLength(arr, 0) or scope exit. Using a
     Z managed string (TZ_JsonString is TPascalString / TUPascalString)
     gives us a well-defined release path that is compiler-agnostic, and
     allows the explicit Clear* helpers below to release deterministically. *)
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
       These exist so callers can deterministically free the internal
       TZ_JsonString buffers inside the records, independent of any
       compiler-specific dynamic-array finalization behavior. *)
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

{ ----------------------------------------------------------------------------
  Module-local helpers
  ---------------------------------------------------------------------------- }

procedure LogInfo(const Msg: string);
begin
  DoStatus(LOG_PREFIX_LLM_CLIENT + Msg);
end;

(* cdecl notify dispatcher. lingofuse_import's notify callbacks are free
   procedures, not of-object methods; we register Self as Trigger and
   bounce through this trampoline. *)
procedure _LLMNotifyDispatch(Trigger: Pointer; Input_: TDataHnd); cdecl;
begin
  if Trigger = nil then
    Exit;
  TLLMClient(Trigger).HandleLLMNotify(Input_);
end;

{ ----------------------------------------------------------------------------
  TLLMClient.SafeParseJson
  ---------------------------------------------------------------------------- }

class function TLLMClient.SafeParseJson(const ARawBytes: TBytes; out AJson: TZ_JsonObject; out AError: string): boolean;
begin
  Result := False;
  AJson := nil;
  AError := '';

  (* Guard against the out-of-bounds risk of Parae() on an empty buffer. *)
  if Length(ARawBytes) = 0 then
  begin
    AError := 'Empty response bytes';
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
      AError := 'Invalid JSON response';
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

{ ----------------------------------------------------------------------------
  TLLMClient.CheckResponseCode

  Every server response uses the envelope:
      { "code": 0, "error": "..." (on failure), <payload> }
  Collapses the per-API code check into one place.
  ---------------------------------------------------------------------------- }

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

{ ----------------------------------------------------------------------------
  TLLMClient.ClearTextAttachments / ClearImageAttachments

  Explicit release helpers for the attachment arrays. Each element is
  cleared field-by-field first (which releases the underlying
  TZ_JsonString buffer), then the array itself is reset to length 0.
  This guarantees deterministic release regardless of compiler-specific
  finalization behavior on dynamic arrays of records.
  ---------------------------------------------------------------------------- }

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

{ ----------------------------------------------------------------------------
  TLLMClient.PopulateAttachmentArray

  Validates every attachment against the size limits and emits the
  JSON array entries. Any violation aborts before any field is written.
  ---------------------------------------------------------------------------- }

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

  (* ---- Pre-flight: validate all sizes before touching the array ---- *)

  totalText := 0;
  for i := 0 to Length(ATexts) - 1 do
  begin
    if ATexts[i].Name.Len = 0 then
      displayName := ATTACHMENT_DEFAULT_NAME
    else
      displayName := ATexts[i].Name.Text;

    n := Length(utf8string(ATexts[i].Text.Text));
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

  (* ---- Emit text entries ---- *)

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

  (* ---- Emit image entries ---- *)

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

{ ----------------------------------------------------------------------------
  TLLMClient.CallAPI

  Wraps the "send request bytes, receive response bytes" pattern using
  the Ex helpers from lingofuse_import:
      LF_CreateDataEx      UTF-8 aware handle creation
      LF_WriteStringBytes  payload + trailing NUL
      LF_CallEx            UTF-8 aware remote call
      LF_ReadStringBytes   NUL-terminated, fault-tolerant read
      LF_FreeData          release
  ---------------------------------------------------------------------------- }

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

{ ----------------------------------------------------------------------------
  Lifecycle
  ---------------------------------------------------------------------------- }

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

    (* LF_Generate_AppNameEx copies the internal buffer immediately,
       so no dependency on the 5-second lifetime window. *)
    FClientName := LF_Generate_AppNameEx();
    if FClientName = '' then
    begin
      ErrorMsg := 'LF_Generate_AppNameEx returned an empty string';
      CleanupPartialConnect(True);
      Exit;
    end;

    FApp := LF_CreateAppEx(FClientName, 'Dynamic LLM Client (v3.8)');
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

{ ----------------------------------------------------------------------------
  Notify handler (runs on the LingoFuse notification thread)
  ---------------------------------------------------------------------------- }

procedure TLLMClient.HandleLLMNotify(Input_: TDataHnd);
var
  js: TZ_JsonString;
  jo: TZ_JsonObject;
  msgType, SessionId, Text, Reason, Msg: string;
begin
  FLastException.V := '';

  try
    if Input_ = nil then
      Exit;
    if LF_GetSize(Input_) <= 0 then
      Exit;

    (* LF_ReadStringBytes is fault-tolerant: NUL-terminated if present,
       otherwise reads the entire remaining buffer. *)
    js.Bytes := LF_ReadStringBytes(Input_);
    if js.Len <= 0 then
      Exit;

    jo := TZ_JsonObject.Create;
    try
      if not jo.ParseText(js) then
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

{ ----------------------------------------------------------------------------
  Session management
  ---------------------------------------------------------------------------- }

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
    (* Re-emit the raw response so callers can parse the full payload. *)
    ASessionsJson := TEncoding.UTF8.GetString(respBytes);
    Result := True;
  finally
    DisposeObject(joResp);
  end;
end;

{ ----------------------------------------------------------------------------
  Generate (no attachments)
  ---------------------------------------------------------------------------- }

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

{ ----------------------------------------------------------------------------
  Generate (with attachments)
  ---------------------------------------------------------------------------- }

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
        (* Fall back to GBK (code page 936); if that also fails, treat
           the bytes as Latin-1 by copy. *)
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
    (* Deterministic release of the internal TZ_JsonString buffers. *)
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

    (* Reject an empty image file up front; the server-side validator
       would reject an empty data_b64 with a less precise message. *)
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

    (* umlBase64EncodeBytes consumes its source buffer as a zero-copy
       optimization; rawBytes is empty afterwards. Do not reuse it. *)
    b64 := '';
    umlBase64EncodeBytes(rawBytes, b64);
    images[0].DataB64.Text := b64.Text;

    Result := GenerateWithAttachments(AContent, APrompt, texts, images, ASessionId, AError);
  finally
    (* Deterministic release of the internal TZ_JsonString buffers. *)
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

{ ----------------------------------------------------------------------------
  SetSystemMessage
  ---------------------------------------------------------------------------- }

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

{ ----------------------------------------------------------------------------
  Health
  ---------------------------------------------------------------------------- }

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

{ ----------------------------------------------------------------------------
  Capability discovery
  ---------------------------------------------------------------------------- }

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

    FCapabilitiesRawJson.Bytes := respBytes;
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
