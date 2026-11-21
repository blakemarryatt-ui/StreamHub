@echo off
cd /d "%~dp0"
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\StreamHub.lnk'); $s.TargetPath='%~dp0start.bat'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0icon.ico,0'; $s.Description='Start StreamHub'; $s.Save()"
if errorlevel 1 (
  echo Something went wrong making the shortcut.
) else (
  echo Done! A StreamHub icon is now on your desktop.
)
pause
