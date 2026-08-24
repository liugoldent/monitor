@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0initialize-h3-ef-012-session.ps1" %*
set "RUN_STATUS=%ERRORLEVEL%"
if not "%RUN_STATUS%"=="0" pause
exit /b %RUN_STATUS%
