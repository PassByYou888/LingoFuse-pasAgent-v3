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
    FServerApp: string;
    FEndpoint: string;
    FTimeout: integer;

    FConnected: boolean;
    FPrepared: boolean;

    FCurrentSessionId: string;

    FCapabilities: TZ_JsonObject;
    FCapabilitiesRawJson: TZ_JsonString;
    FServerKind: string;

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

    class function BuildImageAttachmentFromFile(const AFilePath: string; out AImage: TLLMImageAttachment;
      out AError: string): boolean; static;

    class function BuildSchemaResponseFormatJson(const ASchemaName: string; const ASchemaJsonBytes: TBytes;
      const AStrict: boolean; out AResponseFormatJsonBytes: TBytes; out AError: string): boolean; static;

    function SendGenerateCombined(const AContent, APrompt: string; const ATexts: TLLMTextAttachmentArray;
      const AImages: TLLMImageAttachmentArray; const AResponseFormatJsonBytes: TBytes; var ASessionId: string;
      out AError: string): boolean;
  public
    constructor Create(const AServerApp, AEndpoint: string; ATimeout: integer = 10000);
    destructor Destroy; override;

    function Connect(out ErrorMsg: string): boolean;
    procedure Disconnect;

    function CreateSession(out ASessionId, AError: string): boolean; overload;
    function CreateSession(const ASystemMessage: string; out ASessionId, AError: string): boolean; overload;
    function CloseSession(const ASessionId: string; ACancelRunning: boolean; out AError: string): boolean;
    function CancelSession(const ASessionId: string; out AError: string): boolean;
    function ListSessions(out ASessionsJson, AError: string): boolean;

    function Generate(const AContent, APrompt: string; var ASessionId: string; out AError: string): boolean;

    function GenerateWithAttachments(const AContent, APrompt: string; const ATexts: TLLMTextAttachmentArray;
      const AImages: TLLMImageAttachmentArray; var ASessionId: string; out AError: string): boolean;

    function GenerateWithTextFile(const AContent, APrompt, AFilePath: string; var ASessionId: string; out AError: string): boolean;

    function GenerateWithImageFile(const AContent, APrompt, AFilePath: string; var ASessionId: string; out AError: string): boolean;

    function GenerateCurrent(const AContent, APrompt: string; out AError: string): boolean;

    function GenerateStructured(const AContent, APrompt: string; const AResponseFormatJsonBytes: TBytes;
      var ASessionId: string; out AError: string): boolean; overload;

    function GenerateStructured(const AContent, APrompt: string; const AResponseFormatJson: string;
      var ASessionId: string; out AError: string): boolean; overload;

    function GenerateWithJsonSchema(const AContent, APrompt: string; const ASchemaName: string;
      const ASchemaJsonBytes: TBytes; const AStrict: boolean; var ASessionId: string; out AError: string): boolean; overload;

    function GenerateWithJsonSchema(const AContent, APrompt: string; const ASchemaName: string;
      const ASchemaJson: string; const AStrict: boolean; var ASessionId: string; out AError: string): boolean; overload;

    function GenerateWithImageFileAndSchema(const AContent, APrompt, AFilePath: string; const ASchemaName: string;
      const ASchemaJsonBytes: TBytes; const AStrict: boolean; var ASessionId: string; out AError: string): boolean; overload;

    function GenerateWithImageFileAndSchema(const AContent, APrompt, AFilePath: string; const ASchemaName: string;
      const ASchemaJson: string; const AStrict: boolean; var ASessionId: string; out AError: string): boolean; overload;

    function GenerateWithAttachmentsAndSchema(const AContent, APrompt: string; const ATexts: TLLMTextAttachmentArray;
      const AImages: TLLMImageAttachmentArray; const ASchemaName: string; const ASchemaJsonBytes: TBytes;
      const AStrict: boolean; var ASessionId: string; out AError: string): boolean; overload;

    function GenerateWithAttachmentsAndSchema(const AContent, APrompt: string; const ATexts: TLLMTextAttachmentArray;
      const AImages: TLLMImageAttachmentArray; const ASchemaName: string; const ASchemaJson: string;
      const AStrict: boolean; var ASessionId: string; out AError: string): boolean; overload;

    function SetSystemMessage(const AMessage: string; out AError: string): boolean;
    function Health(out AHealthJson, AError: string): boolean;

    function GetAPICapabilities(var ACapabilitiesJson: TZ_JsonString; out AError: string): boolean;
    function HasCapabilityInfo: boolean;
    function LLMSupported(const AAPIName: string): boolean;
    function IsToolBridge: boolean;
    function HasVision: boolean;
    function HasAttachments: boolean;

    class procedure ClearTextAttachments(var A: TLLMTextAttachmentArray); static;
    class procedure ClearImageAttachments(var A: TLLMImageAttachmentArray); static;

    property OnChunk: TLLMChunkEvent read FOnChunk write FOnChunk;
    property OnThink: TLLMThinkEvent read FOnThink write FOnThink;
    property OnFinish: TLLMFinishEvent read FOnFinish write FOnFinish;
    property OnError: TLLMErrorEvent read FOnError write FOnError;
    property OnClosed: TLLMClosedEvent read FOnClosed write FOnClosed;

    property CurrentSessionId: string read FCurrentSessionId write FCurrentSessionId;
    property Connected: boolean read FConnected;
    property ServerKind: string read FServerKind;
    property CapabilitiesRawJson: TZ_JsonString read FCapabilitiesRawJson;
    property LastException: string read Get_LastException;
  end;

implementation

procedure LogInfo(const Msg: string);
begin
  DoStatus(LOG_PREFIX_LLM_CLIENT + Msg);
end;

function UnicodeStringToUtf8Bytes(const S: string): TBytes;
begin
  if S = '' then
    SetLength(Result, 0)
  else
    Result := TEncoding.UTF8.GetBytes(S);
end;

procedure _LLMNotifyDispatch(Trigger: Pointer; Input_: TDataHnd); cdecl;
begin
  if Trigger = nil then
    Exit;
  TLLMClient(Trigger).HandleLLMNotify(Input_);
end;

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

  joNameStrict := TZ_JsonObject.Create;
  try
    joNameStrict.S['name'] := ASchemaName;
    joNameStrict.B['strict'] := AStrict;
    nameStrictJson := joNameStrict.ToJSONString(False).Text;
  finally
    DisposeObject(joNameStrict);
  end;

  if (nameStrictJson = '') or (nameStrictJson[Length(nameStrictJson)] <> '}') then
  begin
    AError := 'BuildSchemaResponseFormatJson: internal error building name/strict JSON';
    Exit;
  end;
  Delete(nameStrictJson, Length(nameStrictJson), 1);
  nameStrictJson := nameStrictJson + ',"schema":' + schemaJson + '}';

  envelopeJson := '{"type":"json_schema","json_schema":' + nameStrictJson + '}';

  AResponseFormatJsonBytes := TEncoding.UTF8.GetBytes(envelopeJson);
  Result := True;
end;

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

  joReq := TZ_JsonObject.Create;
  try
    joReq.S['content'] := AContent;
    joReq.S['prompt'] := APrompt;

    if ASessionId <> '' then
      joReq.S['session_id'] := ASessionId
    else if FCurrentSessionId <> '' then
      joReq.S['session_id'] := FCurrentSessionId;

    if hasAttach then
    begin
      attachmentsArr := joReq.A['attachments'];
      PopulateAttachmentArray(ATexts, AImages, attachmentsArr, AError);
      if AError <> '' then
        Exit;
    end;

    if hasSchema then
    begin
      reqJsonStr := joReq.ToJSONString(False).Text;
      if reqJsonStr = '{}' then
        reqJsonStr := '{"options":{"response_format":' + schemaJsonStr + '}}'
      else
      begin
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

constructor TLLMClient.Create(const AServerApp, AEndpoint: string; ATimeout: integer);
begin
  inherited Create;
  FServerApp := AServerApp;
  FEndpoint := AEndpoint;
  FTimeout := ATimeout;
  FConnected := False;
  FPrepared := False;
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
  try
    LF_Unregister_P2PEx(API_NAME_STREAM);
  except
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
  FCurrentSessionId := '';
end;

function TLLMClient.Connect(out ErrorMsg: string): boolean;
var
  regRet: integer;
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

  if FPrepared then
    CleanupPartialConnect(False);

  try
    LF_ResetPrepare();
    LF_SetOptionEx('Wait_Connection_ReadyOk', 'True');
    LF_SetOptionEx('Overlap_Connection', 'True');

    regRet := LF_Register_P2P_NotifyEx(
      API_NAME_STREAM,
      'LLM stream callback (v4.0 UID-addressed client)',
      Pointer(Self),
      @_LLMNotifyDispatch
    );
    if regRet <> 1 then
    begin
      ErrorMsg := 'Failed to register P2P notify callback for "' + API_NAME_STREAM + '"';
      CleanupPartialConnect(False);
      Exit;
    end;

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

    FPrepared := True;
    FConnected := True;

    if not GetAPICapabilities(capJson, capErr) then
      LogInfo('Capability probe failed: ' + capErr);

    LogInfo('Connected (P2P UID mode). ServerKind=' + FServerKind);

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
  if not (FPrepared or FConnected) then
    Exit;
  LogInfo('Disconnecting');
  CleanupPartialConnect(True);
end;

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

    jsBytes := LF_ReadStringBytes(Input_);
    if Length(jsBytes) = 0 then
      Exit;

    jo := TZ_JsonObject.Create;
    try
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

    if joResp.Exists('client_uid_hex') then
      LogInfo('Session created: ' + ASessionId + ' (uid=' + joResp.S['client_uid_hex'] + ', system_message=' + IntToStr(Length(ASystemMessage)) + ' chars)')
    else
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
      joReq.S['session_id'] := FCurrentSessionId;

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
      joReq.S['session_id'] := FCurrentSessionId;

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

  joReq := TZ_JsonObject.Create;
  try
    joReq.S['content'] := AContent;
    joReq.S['prompt'] := APrompt;

    if ASessionId <> '' then
      joReq.S['session_id'] := ASessionId
    else if FCurrentSessionId <> '' then
      joReq.S['session_id'] := FCurrentSessionId;

    reqJsonStr := joReq.ToJSONString(False).Text;
    if reqJsonStr = '{}' then
      reqJsonStr := '{"options":{"response_format":' + schemaJsonStr + '}}'
    else
    begin
      Delete(reqJsonStr, Length(reqJsonStr), 1);
      reqJsonStr := reqJsonStr + ',"options":{"response_format":' + schemaJsonStr + '}}';
    end;
    reqBytes := TEncoding.UTF8.GetBytes(reqJsonStr);
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

    LogInfo('Generate queued with response_format. session=' + newSessionId);
    Result := True;
  finally
    DisposeObject(joResp);
  end;
end;

function TLLMClient.GenerateStructured(const AContent, APrompt: string; const AResponseFormatJson: string;
  var ASessionId: string; out AError: string): boolean;
var
  bytes: TBytes;
begin
  bytes := UnicodeStringToUtf8Bytes(AResponseFormatJson);
  Result := GenerateStructured(AContent, APrompt, bytes, ASessionId, AError);
end;

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

function TLLMClient.GenerateWithJsonSchema(const AContent, APrompt: string; const ASchemaName: string;
  const ASchemaJson: string; const AStrict: boolean; var ASessionId: string; out AError: string): boolean;
var
  bytes: TBytes;
begin
  bytes := UnicodeStringToUtf8Bytes(ASchemaJson);
  Result := GenerateWithJsonSchema(AContent, APrompt, ASchemaName, bytes, AStrict, ASessionId, AError);
end;

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

function TLLMClient.GenerateWithImageFileAndSchema(const AContent, APrompt, AFilePath: string; const ASchemaName: string;
  const ASchemaJson: string; const AStrict: boolean; var ASessionId: string; out AError: string): boolean;
var
  bytes: TBytes;
begin
  bytes := UnicodeStringToUtf8Bytes(ASchemaJson);
  Result := GenerateWithImageFileAndSchema(AContent, APrompt, AFilePath, ASchemaName, bytes, AStrict, ASessionId, AError);
end;

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

function TLLMClient.GenerateWithAttachmentsAndSchema(const AContent, APrompt: string; const ATexts: TLLMTextAttachmentArray;
  const AImages: TLLMImageAttachmentArray; const ASchemaName: string; const ASchemaJson: string; const AStrict: boolean;
  var ASessionId: string; out AError: string): boolean;
var
  bytes: TBytes;
begin
  bytes := UnicodeStringToUtf8Bytes(ASchemaJson);
  Result := GenerateWithAttachmentsAndSchema(AContent, APrompt, ATexts, AImages, ASchemaName, bytes, AStrict, ASessionId, AError);
end;

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
