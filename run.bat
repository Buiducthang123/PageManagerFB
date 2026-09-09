@echo off
cd /d "%~dp0"

set BACKEND_PORT=8001
set FRONTEND_PORT=5175

netstat -ano | findstr ":%BACKEND_PORT% " | findstr "LISTENING" >nul
if %errorlevel%==0 (
    echo [skip] Backend da chay san o port %BACKEND_PORT%.
) else (
    echo [start] Backend uvicorn o port %BACKEND_PORT%...
    start "Backend (uvicorn)" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --reload --port %BACKEND_PORT%"
)

netstat -ano | findstr ":%FRONTEND_PORT% " | findstr "LISTENING" >nul
if %errorlevel%==0 (
    echo [skip] Frontend da chay san o port %FRONTEND_PORT%.
) else (
    echo [start] Frontend vite o port %FRONTEND_PORT%...
    start "Frontend (vite)" cmd /k "cd frontend && npm run dev"
)

echo.
echo Mo trinh duyet: http://localhost:%FRONTEND_PORT%
echo (Dong 2 cua so console rieng "Backend"/"Frontend" moi khi muon dung han.)
pause
