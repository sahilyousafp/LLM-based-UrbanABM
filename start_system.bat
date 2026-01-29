@echo off
REM Quick Start - Urban ABM System
echo.
echo ========================================
echo   Urban ABM - Quick Start
echo ========================================
echo.
echo This will:
echo 1. Start the Backend API server (Port 8000)
echo 2. Open the Frontend in your default browser
echo.
echo Press any key to continue...
pause >nul

REM Start backend in new window
echo Starting Backend Server...
start "Urban ABM Backend" cmd /k "%~dp0start_backend.bat"

REM Wait a moment for server to start
timeout /t 3 /nobreak >nul

REM Open frontend
echo Opening Frontend...
start "" "%~dp0Frontend\index.html"

echo.
echo ========================================
echo System Started!
echo.
echo Backend: http://127.0.0.1:8000
echo Frontend: Opening in browser...
echo.
echo Close the Backend window to stop the server
echo ========================================
