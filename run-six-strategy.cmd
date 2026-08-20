@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-six-strategy.ps1" %*
exit /b %ERRORLEVEL%
