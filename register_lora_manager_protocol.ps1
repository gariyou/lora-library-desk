param()

$scheme = "lora-manager"
$protocolRoot = "HKCU:\Software\Classes\$scheme"
$launchBatchPath = (Join-Path $PSScriptRoot "launch_from_protocol.bat")
$cmdPath = "$env:SystemRoot\System32\cmd.exe"

if (-not (Test-Path -LiteralPath $launchBatchPath)) {
  throw "launch_from_protocol.bat was not found."
}

if (-not (Test-Path -LiteralPath $cmdPath)) {
  throw "cmd.exe was not found."
}

$commandValue = ('"{0}" /c ""{1}" "%1"""' -f $cmdPath, $launchBatchPath)

New-Item -Path $protocolRoot -Force | Out-Null
Set-ItemProperty -Path $protocolRoot -Name "(default)" -Value "URL:LoRA Manager Protocol"
Set-ItemProperty -Path $protocolRoot -Name "URL Protocol" -Value ""

New-Item -Path "$protocolRoot\DefaultIcon" -Force | Out-Null
Set-ItemProperty -Path "$protocolRoot\DefaultIcon" -Name "(default)" -Value "$env:SystemRoot\System32\shell32.dll,13"

New-Item -Path "$protocolRoot\shell\open\command" -Force | Out-Null
Set-ItemProperty -Path "$protocolRoot\shell\open\command" -Name "(default)" -Value $commandValue

Write-Host "LoRA Manager protocol was registered."
Write-Host "You can now launch LoRA Manager from the Chrome extension."
