param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$Args
)

& "$PSScriptRoot\start.ps1" --open-path /lora @Args
exit $LASTEXITCODE
