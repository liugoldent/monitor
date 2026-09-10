@echo off
setlocal
powershell.exe -NoLogo -ExecutionPolicy Bypass -File "%~dp0initialize-telegram-relay-session.ps1"
set "RUN_STATUS=%ERRORLEVEL%"
if not "%RUN_STATUS%"=="0" pause
exit /b %RUN_STATUS%
