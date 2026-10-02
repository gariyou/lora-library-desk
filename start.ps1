param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
$serverScriptPath = Join-Path $PSScriptRoot 'server.py'
$effectiveArgs = @($Args)
$effectivePort = 8787
for ($index = 0; $index -lt $effectiveArgs.Count; $index += 1) {
  if ($effectiveArgs[$index] -eq '--port' -and $index + 1 -lt $effectiveArgs.Count) { $effectivePort = [int]$effectiveArgs[$index + 1] }
  elseif ($effectiveArgs[$index] -match '^--port=(\d+)$') { $effectivePort = [int]$Matches[1] }
}
if ($effectivePort -lt 1 -or $effectivePort -gt 65535) { Write-Host 'Invalid port.'; exit 1 }
if (Get-NetTCPConnection -LocalPort $effectivePort -State Listen -ErrorAction SilentlyContinue) {
  Write-Host "Port $effectivePort is already in use. Stop the existing app yourself or use --port 8877."
  exit 1
}
if ($pyCommand = Get-Command py -ErrorAction SilentlyContinue) {
  & $pyCommand.Source -3 $serverScriptPath @effectiveArgs
  exit $LASTEXITCODE
}
if ($pythonCommand = Get-Command python -ErrorAction SilentlyContinue) {
  & $pythonCommand.Source $serverScriptPath @effectiveArgs
  exit $LASTEXITCODE
}
Write-Host 'Python was not found. Install Python 3.10 or newer and try again.'
exit 1
