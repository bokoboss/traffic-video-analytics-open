@echo off
setlocal
set "ROOT=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\windows\restore_app.ps1" %*
exit /b %ERRORLEVEL%
