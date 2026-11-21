@echo off
:: Lets your phone reach StreamHub on your home Wi-Fi (asks for admin once).
net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
netsh advfirewall firewall delete rule name="StreamHub" >nul 2>&1
netsh advfirewall firewall add rule name="StreamHub" dir=in action=allow protocol=TCP localport=8337 profile=private
echo.
echo Done. Your phone can now connect on your home Wi-Fi.
echo Make sure your Wi-Fi is set to "Private" in Windows:
echo   Settings ^> Network ^& internet ^> Wi-Fi ^> your network ^> Private
echo.
pause
