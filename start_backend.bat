@echo off
REM Start Urban ABM Backend Server
echo.
echo ========================================
echo   Urban ABM - Backend Server
echo ========================================
echo.

REM Load .env file into environment variables
if exist "%~dp0.env" (
    echo Loading .env configuration...
    for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0.env") do (
        REM Skip comments and empty lines
        echo %%A | findstr /r "^#" >nul 2>&1 || (
            if not "%%A"=="" if not "%%B"=="" set "%%A=%%B"
        )
    )
    echo .env loaded.
) else (
    echo WARNING: No .env file found. Using defaults.
    echo Create .env from the template in the project root.
)
echo.

echo Starting FastAPI server on http://127.0.0.1:8000
echo.
echo API Endpoints will be available at:
echo   - http://127.0.0.1:8000/api/buildings
echo   - http://127.0.0.1:8000/api/agents
echo   - http://127.0.0.1:8000/api/streetview_grid
echo   - http://127.0.0.1:8000/api/config/frontend
echo   - http://127.0.0.1:8000/api/stats
echo   ... and more
echo.
echo Press Ctrl+C to stop the server
echo.
cd /d "%~dp0Backend\Agent"
python map_server.py
