# Local launcher: load .env.local (if present) and start the loopback server.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\run_local.ps1 [-Port 8000]
# Note: .env.local holds the model key; it is machine-local and git-ignored.
# ASCII-only on purpose: Windows PowerShell 5.1 mis-parses non-BOM UTF-8 scripts.

param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$envFile = Join-Path $repo ".env.local"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $pair = $line.Split("=", 2)
            $name = $pair[0].Trim()
            # F-12: only app-scoped keys may be injected (never PATH, SSL_CERT_FILE,
            # PYTHONPATH, NODE_OPTIONS, ... which would let a local file alter the
            # interpreter/toolchain behaviour).
            if ($name -notlike "HKTAX_*") {
                Write-Warning "Ignoring non-HKTAX_ key in .env.local: $name"
                return
            }
            [Environment]::SetEnvironmentVariable($name, $pair[1].Trim(), "Process")
        }
    }
    Write-Host "Loaded .env.local (model config)"
} else {
    Write-Host "No .env.local found - starting in local-only mode (AI unconfigured)"
}

$py = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    throw "Python venv not found at $py - create it first (.venv)."
}

# Startup via the controlled entrypoint (app.main.run): loopback-only bind,
# single worker, no proxy headers are enforced there. --host is refused unless
# it is 127.0.0.1 / localhost.
$mainArgs = @(
    "-m", "app.main",
    "--port", "$Port"
)
& $py @mainArgs
