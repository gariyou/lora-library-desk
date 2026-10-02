param(
  [string]$LaunchUri = ""
)

$defaultBaseUrl = "http://127.0.0.1:8787"
$startBatchPath = Join-Path $PSScriptRoot "start_lora.bat"
$startScriptPath = Join-Path $PSScriptRoot "start.ps1"

function Get-QueryValue {
  param(
    [string]$Query,
    [string]$Name
  )

  foreach ($pair in ([string]$Query).TrimStart("?").Split("&", [System.StringSplitOptions]::RemoveEmptyEntries)) {
    $parts = $pair.Split("=", 2)
    $key = [Uri]::UnescapeDataString([string]$parts[0]).Trim()
    if ($key -ne $Name) {
      continue
    }

    if ($parts.Count -lt 2) {
      return ""
    }
    return [Uri]::UnescapeDataString([string]$parts[1]).Trim()
  }

  return ""
}

function Get-BaseUrl {
  param(
    [string]$ProtocolUri
  )

  if (-not [string]::IsNullOrWhiteSpace($ProtocolUri)) {
    try {
      $uri = [Uri]$ProtocolUri
      $baseUrl = Get-QueryValue -Query $uri.Query -Name "base_url"
      if (-not $baseUrl) {
        $baseUrl = Get-QueryValue -Query $uri.Query -Name "baseUrl"
      }
      if ($baseUrl) {
        return $baseUrl
      }
    } catch {
    }
  }

  return $defaultBaseUrl
}

function Get-PortFromBaseUrl {
  param(
    [string]$BaseUrl
  )

  try {
    $uri = [Uri]$BaseUrl
    if ($uri.Port -gt 0) {
      return [int]$uri.Port
    }
  } catch {
  }

  return 8787
}

function Test-LoRAManagerHealth {
  param(
    [string]$BaseUrl
  )

  try {
    $response = Invoke-WebRequest -Uri "$BaseUrl/health" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
    return ($response.Content -as [string]).Trim().ToLowerInvariant() -eq "ok"
  } catch {
    return $false
  }
}

function Get-WindowsPowerShellPath {
  $fallback = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
  if (Test-Path $fallback) {
    return $fallback
  }

  $fromPath = Get-Command powershell -ErrorAction SilentlyContinue
  if ($fromPath -and $fromPath.Source) {
    return $fromPath.Source
  }

  return "powershell.exe"
}

$baseUrl = Get-BaseUrl -ProtocolUri $LaunchUri
$port = Get-PortFromBaseUrl -BaseUrl $baseUrl

if (Test-LoRAManagerHealth -BaseUrl $baseUrl) {
  exit 0
}

if (Test-Path -LiteralPath $startBatchPath) {
  $arguments = @("--no-browser", "--open-path", "/lora")
  if ($port -ne 8787) {
    $arguments += @("--port", [string]$port)
  }

  Start-Process -FilePath $startBatchPath -ArgumentList $arguments -WorkingDirectory $PSScriptRoot | Out-Null
  exit 0
}

$shellPath = Get-WindowsPowerShellPath
$arguments = @(
  "-NoProfile",
  "-ExecutionPolicy",
  "Bypass",
  "-File",
  $startScriptPath,
  "--no-browser",
  "--open-path",
  "/lora"
)

if ($port -ne 8787) {
  $arguments += @("--port", [string]$port)
}

Start-Process -FilePath $shellPath -ArgumentList $arguments -WorkingDirectory $PSScriptRoot | Out-Null
