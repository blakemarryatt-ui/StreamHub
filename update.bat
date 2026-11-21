@echo off
cd /d "%~dp0"
title StreamHub update
if not exist ".venv\Scripts\python.exe" (
  echo Run start.bat once first.
  pause
  exit /b 1
)
echo Close the StreamHub window first if it's open.
echo.
".venv\Scripts\python.exe" server\updater.py
echo.
pause
