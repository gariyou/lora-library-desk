@echo off
setlocal

set "RUNNER=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%RUNNER%" set "RUNNER=powershell.exe"

pushd "%~dp0" >nul 2>nul
if errorlevel 1 (
  echo LoRA管理フォルダを開けません。
  exit /b 1
)

if not exist ".\register_lora_manager_protocol.ps1" (
  echo register_lora_manager_protocol.ps1 が見つかりません。
  popd
  exit /b 1
)

if not exist "%RUNNER%" (
  echo PowerShell が見つかりません。
  popd
  exit /b 1
)

"%RUNNER%" -NoProfile -ExecutionPolicy Bypass -File ".\register_lora_manager_protocol.ps1"
set "ERR=%ERRORLEVEL%"
popd
exit /b %ERR%
