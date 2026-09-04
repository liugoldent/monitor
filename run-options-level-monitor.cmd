@echo off
powershell.exe -NoLogo -NoExit -ExecutionPolicy Bypass -File "%~dp0run-options-level-monitor.ps1" %*
