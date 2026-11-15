@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" scripts\run_local.py --provider gemini --port 8001
pause
