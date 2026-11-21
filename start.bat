@echo off
setlocal
cd /d "%~dp0"
title StreamHub

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto :nopython

if exist ".venv\Scripts\python.exe" (
  fc /b requirements.txt ".venv\req.stamp" >nul 2>nul || goto :install
  goto :run
)

echo.
echo  First run: setting things up (about a minute)...
%PY% -m venv .venv || goto :nopython

:install
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :fail
copy /y requirements.txt ".venv\req.stamp" >nul

:run
".venv\Scripts\python.exe" server\main.py
if %errorlevel%==42 (
  fc /b requirements.txt ".venv\req.stamp" >nul 2>nul || goto :install
  goto :run
)
echo.
pause
exit /b

:nopython
echo.
echo  Python 3.10 or newer is needed.
echo  Get it from https://www.python.org/downloads/  (tick "Add python.exe to PATH")
echo  then double-click start.bat again.
pause
exit /b 1

:fail
echo.
echo  Installing packages failed. Check your internet and run start.bat again.
pause
exit /b 1
