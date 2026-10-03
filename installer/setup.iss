; Bộ cài lần đầu (user-management-plan.md mục 12 "Bộ cài lần đầu").
;
; Setup.exe nhỏ: chỉ chứa launcher.pyw + lớp App. Runtime (Python + thư viện +
; Chromium + ffmpeg, vài GB) tải lúc cài từ GitHub Releases — chia nhiều phần
; < 2 GB (giới hạn GitHub), mỗi phần kiểm SHA-256, ghép lại rồi giải nén vào
; {app}\runtime. Model AI không đóng vào — app tự tải khi cần.
;
; Build:  "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" /DAppVersion=1.0.1 installer\setup.iss
; Cần có sẵn: tools\launcher\launcher.py, build\release\stage\<AppVersion>\ (tools\build_release.py)
; và installer\runtime_parts.iss (tools\publish_github.py runtime <bản> sinh ra).

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6E0B5A3C-2F7D-4C1E-9B8A-7A1D3E5F9C21}
AppName=OddlyLab Reup
AppVersion={#AppVersion}
AppPublisher=OddlyLab
DefaultDirName={localappdata}\Programs\OddlyLabReup
DefaultGroupName=OddlyLab Reup
; Cài theo user (không cần quyền admin) — app ghi workspace/cập nhật vào chính thư mục cài.
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=OddlyLabReup-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\runtime\pythonw.exe

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Files]
; Launcher là script chạy bằng pythonw.exe của Runtime (có chữ ký PSF) — KHÔNG dùng
; launcher.exe kiểu Nuitka onefile: Windows Defender chặn nhầm là virus (lỗi CreateProcess 225).
Source: "..\tools\launcher\launcher.py"; DestDir: "{app}"; DestName: "launcher.pyw"; Flags: ignoreversion
Source: "..\build\release\stage\{#AppVersion}\*"; DestDir: "{app}\app\{#AppVersion}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Cài đè lên bản cũ: bỏ launcher.exe (bị Defender chặn nhầm) và shortcut cũ trỏ vào nó.
Type: files; Name: "{app}\launcher.exe"

[Dirs]
Name: "{app}\workspace"; Flags: uninsneveruninstall

[Tasks]
Name: "desktopicon"; Description: "Tạo biểu tượng ngoài Desktop"

[Icons]
Name: "{group}\OddlyLab Reup"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"
Name: "{userdesktop}\OddlyLab Reup"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\launcher.pyw"""; WorkingDir: "{app}"; Description: "Mở OddlyLab Reup"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Xoá runtime/app khi gỡ; workspace (dữ liệu user) GIỮ LẠI.
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\logs"
Type: files; Name: "{app}\launcher.json"
; Bản cài cũ có launcher.exe (đã bỏ).
Type: files; Name: "{app}\launcher.exe"

#include "runtime_parts.iss"

[Code]
var
  DownloadPage: TDownloadWizardPage;

function RuntimeInstalled(): Boolean;
var
  Text: AnsiString;
begin
  { Chỉ đọc `runtime.json` là CHƯA ĐỦ — nếu lần cài trước bị gián đoạn giữa
    chừng (hết đĩa, antivirus chặn, người dùng tắt máy...) nhưng file đánh dấu
    này lỡ ghi được trước khi hỏng, lần cài SAU sẽ tưởng nhầm "đã có Runtime"
    rồi bỏ qua hẳn bước tải/giải nén — để lại thư mục runtime thiếu hẳn
    python.exe, launcher chạy lên báo "Thiếu runtime" mà bộ cài không hề biết
    gì để tự sửa (đã xác nhận thật gặp đúng lỗi này). Kiểm tra thêm file thật
    sự cần có để chạy app. }
  Result := FileExists(ExpandConstant('{app}\runtime\pythonw.exe'))
    and LoadStringFromFile(ExpandConstant('{app}\runtime\runtime.json'), Text)
    and (Pos('"version": "{#RuntimeVersion}"', Text) > 0);
end;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage('Tải thành phần chạy', 'Đang tải Python + thư viện AI (vài GB, chỉ tải 1 lần)…', nil);
end;

function CheckTempDiskSpace(RequiredMB: Int64): Boolean;
var
  Free, Total: Int64;
begin
  { Thư mục tạm của Inno Setup LUÔN nằm ở ổ chứa thư mục Temp hệ thống (thường
    là ổ C), BẤT KỂ người dùng chọn cài app ở ổ nào - cần đủ chỗ cho CẢ file
    tải về LẪN file runtime.zip ghép ra (không xoá file tải ngay) trước khi
    giải nén. Thiếu chỗ ở đây làm bước copy /b ghép file thất bại với mã lỗi
    chung chung (đã gặp thật: Ghép Runtime lỗi, mã 1), không rõ nguyên nhân
    cho người dùng tự đoán - kiểm tra trước, báo rõ ràng ngay từ đầu. 4000 MB
    là ước lượng dư ra so với kích thước Runtime thật (~1.7 GB tải + ~1.7 GB ghép). }
  Result := GetSpaceOnDisk64(ExpandConstant('{tmp}'), Free, Total) and (Free div (1024*1024) >= RequiredMB);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpReady) and (not RuntimeInstalled()) then begin
    if not CheckTempDiskSpace(4000) then begin
      SuppressibleMsgBox(
        'Ổ đĩa chứa thư mục tạm của Windows (thường là ổ C, KHÔNG phải ổ bạn chọn cài app) ' +
        'không đủ khoảng 4 GB trống để tải và ghép Runtime. Dọn bớt dung lượng ổ C rồi chạy lại bộ cài.',
        mbCriticalError, MB_OK, IDOK);
      Result := False;
      exit;
    end;
    DownloadPage.Clear;
    DownloadPage.Show;
    try
      try
        AddRuntimeParts(DownloadPage);  { mỗi phần kèm SHA-256 — sai là báo lỗi }
        DownloadPage.Download;
      except
        if DownloadPage.AbortedByUser then
          Log('Người dùng huỷ tải runtime')
        else
          SuppressibleMsgBox(AddPeriod(GetExceptionMessage), mbCriticalError, MB_OK, IDOK);
        Result := False;
      end;
    finally
      DownloadPage.Hide;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  Zip, Dest, LogFile, BatFile: String;
begin
  if (CurStep = ssPostInstall) and (not RuntimeInstalled()) then begin
    Zip := ExpandConstant('{tmp}\runtime.zip');
    Dest := ExpandConstant('{app}\runtime');
    WizardForm.StatusLabel.Caption := 'Đang ghép các phần Runtime…';
    if not Exec(ExpandConstant('{cmd}'), RuntimeConcatArgs(), ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, ResultCode)
       or (ResultCode <> 0) then begin
      MsgBox('Ghép Runtime lỗi (mã ' + IntToStr(ResultCode) + '). Chạy lại bộ cài.', mbError, MB_OK);
      exit;
    end;
    WizardForm.StatusLabel.Caption := 'Đang giải nén Runtime (có thể mất 10-15 phút)…';
    DelTree(Dest, True, True, True);
    ForceDirectories(Dest);
    ForceDirectories(ExpandConstant('{app}\logs'));
    { ĐÃ ĐỔI từ tar.exe sang Expand-Archive (PowerShell) — xác nhận thật bằng
      cách tự giải nén file runtime.zip thật trên máy: tar.exe (bsdtar có sẵn
      Windows 10+) đọc SAI zip do Python `zipfile` tạo ra — 1 entry đọc lệch
      kéo theo HÀNG LOẠT entry sau bị lỗi "empty or unreadable filename",
      thoát với mã 1. Expand-Archive (.NET System.IO.Compression) đọc đúng
      (xác nhận: pythonw.exe có mặt, đủ 39704 file) — CHẬM hơn tar nhiều
      (~9-10 phút cho 4.3 GB thay vì vài phút) nhưng đúng mới dùng được, nên
      chấp nhận đánh đổi tốc độ lấy đúng.
      Ghi lệnh (kèm redirect log) ra 1 file .bat rồi Exec đúng file đó — thay
      vì nhồi thẳng "/c "..." ... > "..." 2>&1" vào tham số dòng lệnh của
      cmd.exe: cmd có quy tắc xử lý dấu nháy kép đặc biệt cho "/c" (chỉ an
      toàn khi CẢ CHUỖI có ĐÚNG 1 cặp nháy kép bao quanh), nhiều cặp nháy kép
      lồng nhau bị cmd parse sai, Exec coi như thất bại NGAY TỪ ĐẦU — không
      hề chạy lệnh thật, không hề tạo ra file log (đã xác nhận thật gặp đúng
      lỗi này với tar.exe). Dùng file .bat thì Exec chỉ cần đúng 1 cặp nháy
      kép bao quanh đường dẫn file .bat, an toàn. }
    LogFile := ExpandConstant('{app}\logs\runtime_extract.log');
    BatFile := ExpandConstant('{tmp}\extract_runtime.bat');
    SaveStringToFile(
      BatFile,
      'powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Path ''' +
        Zip + ''' -DestinationPath ''' + Dest + ''' -Force" > "' + LogFile + '" 2>&1' + #13#10,
      False);
    if not Exec(ExpandConstant('{cmd}'), '/c "' + BatFile + '"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
       or (ResultCode <> 0) then
      MsgBox(
        'Giải nén Runtime lỗi (mã ' + IntToStr(ResultCode) + '). Xem chi tiết tại ' +
        LogFile + ' rồi chạy lại bộ cài.',
        mbError, MB_OK);
  end;
end;
