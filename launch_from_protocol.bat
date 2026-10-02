@echo off
setlocal

set "RUNNER=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%RUNNER%" set "RUNNER=powershell.exe"

pushd "%~dp0" >nul 2>nul
if errorlevel 1 exit /b 1

"%RUNNER%" -NoProfile -ExecutionPolicy Bypass -File ".\launch_from_protocol.ps1" "%~1"
set "ERR=%ERRORLEVEL%"

popd >nul
exit /b %ERR%
