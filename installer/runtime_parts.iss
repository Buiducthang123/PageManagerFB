; SINH TỰ ĐỘNG bởi tools/publish_github.py — không sửa tay.
#define RuntimeVersion "1.0.0"

[Code]
procedure AddRuntimeParts(DownloadPage: TDownloadWizardPage);
begin
  DownloadPage.Add('https://github.com/Buiducthang123/oddlylab-reup-releases/releases/download/runtime-1.0.0/runtime-1.0.0.zip.part1', 'rt.part1', 'c330500fb448d904c9873f357cd80fb329ae7150bed940f77e4fd1d3607fc34a');
end;

function RuntimeConcatArgs(): String;
begin
  Result := '/c copy /b /y rt.part1 runtime.zip';
end;
