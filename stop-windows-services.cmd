@echo off
setlocal
cd /d "%~dp0"
docker compose stop six-strategy monitor-mxf webhook-server cloudflared
set "RUN_STATUS=%ERRORLEVEL%"
if not "%RUN_STATUS%"=="0" pause
exit /b %RUN_STATUS%
