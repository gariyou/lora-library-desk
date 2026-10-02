@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_lora.ps1" %*
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" pause
exit /b %ERR%
