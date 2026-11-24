$ErrorActionPreference = 'Stop'
$uiDirectory = $PSScriptRoot
$pidFile = Join-Path $uiDirectory 'server.pid'
if (Test-Path $pidFile) {
    $uiProcessId = [int](Get-Content $pidFile)
    $uiProcess = Get-Process -Id $uiProcessId -ErrorAction SilentlyContinue
    if ($uiProcess -and $uiProcess.ProcessName -eq 'node') { Stop-Process -Id $uiProcessId }
    Remove-Item -LiteralPath $pidFile
}
Write-Host 'Local UI stopped. Docker backend is still running.'
