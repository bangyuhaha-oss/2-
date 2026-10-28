@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0Setup.cmd"
if errorlevel 1 goto failed
echo.
echo Group 2 Flask Employee Management
echo Open http://127.0.0.1:5000 in your browser.
echo Keep this window open. Press Ctrl+C to stop.
echo.
".venv\Scripts\python.exe" app.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo Startup failed. Read README.md for Python 3.12 x64 and offline installation.
pause
exit /b 1
