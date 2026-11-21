@echo off
cd /d "%~dp0"
if not exist config.json copy config.example.json config.json >nul
echo Close the StreamHub window before saving changes, then start it again.
start "" notepad config.json
