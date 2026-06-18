@echo off
REM Quick Start - Urban ABM System
echo.
echo ========================================
echo   Urban ABM - Quick Start
echo ========================================
echo.
echo This will:
echo 1. Start the Map API server (Port 8000)
echo 2. Start the Agent Lab server (Port 8100)
echo 3. Start the Frontend dev server (Port 8091)
echo 4. Open the Frontend in your default browser
echo.
echo Press any key to continue...
pause >nul

REM Start map server in new window
echo Starting Map Server (port 8000)...
start "UABM Map" cmd /k "%~dp0start_backend.bat"

REM Start agent lab server in new window
echo Starting Agent Lab Server (port 8100)...
start "UABM Lab" cmd /k "cd /d "%~dp0test" && python agent_lab_server.py"

REM Start Vite dev server (TypeScript build + HMR + /api proxy to :8000)
echo Starting Frontend Dev Server (port 8091)...
start "UABM Frontend" cmd /k "cd /d "%~dp0Frontend" && npm run dev"

REM Wait for servers to start
timeout /t 5 /nobreak >nul

REM Open frontend (Vite serves from localhost:8091, proxies /api to :8000)
echo Opening Frontend...
start "" "http://localhost:8091/"

echo.
echo ========================================
echo System Started!
echo.
echo Map Server:  http://127.0.0.1:8000
echo Agent Lab:   http://127.0.0.1:8100
echo Frontend:    http://localhost:8091/
echo.
echo Close server windows to stop the system.
echo ========================================
