@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0..\website\Setup.cmd"
if errorlevel 1 (
  echo Board setup failed. Review the environment installation message above.
  pause
  exit /b 1
)
cd /d "%~dp0"
echo Group 2 Scrum board: http://127.0.0.1:5001
echo Proposed owners do not establish personal contributions. Press Ctrl+C to stop.
"%~dp0..\website\.venv\Scripts\python.exe" "%~dp0board.py"
pause
