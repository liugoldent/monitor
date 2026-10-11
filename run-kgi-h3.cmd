@echo off
powershell.exe -NoLogo -NoExit -ExecutionPolicy Bypass -File "%~dp0scripts\windows\kgi-h3-service.ps1" -Action Paper
