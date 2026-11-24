$ErrorActionPreference = 'Stop'
$uiDirectory = $PSScriptRoot
$projectDirectory = (Resolve-Path (Join-Path $uiDirectory '..\..')).Path
Push-Location $projectDirectory
try {
    & '.\.venv\Scripts\python.exe' (Join-Path $uiDirectory 'configure.py')
    if ($LASTEXITCODE -ne 0) { throw 'Local configuration failed.' }
    & docker compose -f docker-compose.yml -f (Join-Path $uiDirectory 'compose.local.yml') up -d --no-build
    if ($LASTEXITCODE -ne 0) { throw 'Docker startup failed. Build the image with docker compose build migrate if needed.' }
    $existing = Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue
    if (-not $existing) {
        $nodeExecutable = (Get-Command node.exe).Source
        $process = Start-Process -FilePath $nodeExecutable -ArgumentList 'server.mjs' -WorkingDirectory $uiDirectory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $uiDirectory 'server.log') -RedirectStandardError (Join-Path $uiDirectory 'server-error.log')
        $process.Id | Set-Content (Join-Path $uiDirectory 'server.pid')
    }
    Write-Host 'Local UI: http://127.0.0.1:5173'
    Write-Host 'API docs: http://localhost:8000/docs'
} finally { Pop-Location }
