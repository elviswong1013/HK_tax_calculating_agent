# 本地启动脚本：加载 .env.local（若存在）并启动回环服务。
# 用法：powershell -ExecutionPolicy Bypass -File scripts\run_local.ps1
# 说明：.env.local 内含模型密钥，仅本机保存且不提交（.gitignore）。

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$envFile = Join-Path $repo ".env.local"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $name, $value = $line.Split("=", 2)
            [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), "Process")
        }
    }
    Write-Host "已加载 .env.local（模型配置）"
} else {
    Write-Host "未找到 .env.local——以纯本地模式启动（AI 未配置）"
}

# 启动顺序与参数按 Annex C C1/SDD §8：
# --workers 1 --no-proxy-headers；仅回环 127.0.0.1。
& (Join-Path $repo ".venv\Scripts\python.exe") -m uvicorn app.main:app `
    --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
