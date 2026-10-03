@echo off
rem Phat hanh ban App moi cho user (chi tiet: release.md).
rem
rem   PowerShell:  .\release.bat 1.0.2 "Sua loi abc, them tinh nang xyz"
rem   cmd:         release.bat 1.0.2 "..."
rem
rem 1) Bien dich app\ bang Nuitka + build giao dien -> dist\releases\app-<ban>.zip
rem 2) Upload len GitHub Releases + ghi vao bang app_releases tren Supabase
rem    -> may user thay "Co ban moi" trong <= 30 phut, bam Cap nhat la xong.
rem
rem Chi doi Runtime (them/doi thu vien trong requirements.txt) thi KHONG dung
rem file nay - xem muc "Runtime" trong release.md.

setlocal
chcp 65001 >nul
cd /d "%~dp0"

if "%~1"=="" (
    echo Cach dung: .\release.bat PHIEN_BAN "GHI CHU THAY DOI" - PowerShell can them .\ o dau
    echo Vi du:     release.bat 1.0.2 "Sua loi dich, them nut xuat file"
    exit /b 1
)
set "VERSION=%~1"
set "NOTES=%~2"

echo.
echo === [1/2] Build ban %VERSION% ===
".venv\Scripts\python.exe" tools\build_release.py %VERSION% --notes "%NOTES%"
if errorlevel 1 (
    echo.
    echo [LOI] Build that bai - xem thong bao phia tren. Chua upload gi ca.
    exit /b 1
)

echo.
echo === [2/2] Upload len GitHub + ghi vao Supabase ===
".venv\Scripts\python.exe" tools\publish_github.py app %VERSION%
if errorlevel 1 (
    echo.
    echo [LOI] Upload that bai. File build van o dist\releases\app-%VERSION%.zip
    echo       Chay lai rieng buoc upload: .venv\Scripts\python.exe tools\publish_github.py app %VERSION%
    exit /b 1
)

echo.
echo Xong. Ban %VERSION% da phat hanh: https://github.com/Buiducthang123/oddlylab-reup-releases/releases/tag/app-%VERSION%
echo Bat buoc moi nguoi cap nhat: trang Quan ly user - Cau hinh chung - Ban app toi thieu = %VERSION%
endlocal
