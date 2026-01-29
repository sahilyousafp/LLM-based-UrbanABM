@echo off
REM Start Urban ABM Backend Server
echo.
echo ========================================
echo   Urban ABM - Backend Server
echo ========================================
echo.
echo Starting FastAPI server on http://127.0.0.1:8000
echo.
echo API Endpoints will be available at:
echo   - http://127.0.0.1:8000/api/buildings
echo   - http://127.0.0.1:8000/api/agents
echo   - http://127.0.0.1:8000/api/stats
echo   ... and more
echo.
echo Press Ctrl+C to stop the server
echo.
cd /d "%~dp0Backend\Agent"
python map_server.py
