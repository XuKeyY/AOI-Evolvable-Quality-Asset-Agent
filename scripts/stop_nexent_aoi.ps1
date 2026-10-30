param(
    [switch]$KeepDockerDesktop
)

$ErrorActionPreference = "Stop"
$AgentRoot = "D:\competition\aoi-agent"
$NexentContainers = @(
    "nexent-runtime-sandbox", "nexent-web", "nexent-data-process", "nexent-mcp",
    "nexent-northbound", "nexent-runtime", "nexent-config", "supabase-kong-mini",
    "supabase-auth-mini", "supabase-db-mini", "nexent-minio", "nexent-elasticsearch",
    "nexent-redis", "nexent-postgresql"
)

function Stop-AoiProcessOnPort([int]$Port) {
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    foreach ($listener in $listeners) {
        $processId = $listener.OwningProcess
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
        $isAoi = $processInfo -and (
            $processInfo.CommandLine -like "*$AgentRoot*" -or
            $processInfo.ExecutablePath -like "$AgentRoot\.venv\Scripts\python.exe" -or
            ($Port -eq 8100 -and $processInfo.CommandLine -like "*uvicorn*api.app:app*") -or
            ($Port -eq 8101 -and $processInfo.CommandLine -like "*mcp_server.server*")
        )
        if ($isAoi) {
            Stop-Process -Id $processId -Force -ErrorAction Stop
            Write-Host "Stopped AOI process $processId on port $Port."
        } else {
            Write-Warning "Port $Port belongs to another program; it was not stopped. PID=$processId"
        }
    }
}

Stop-AoiProcessOnPort 8100
Stop-AoiProcessOnPort 8101

$PidFile = Join-Path $AgentRoot "data\service_pids.json"
if (Test-Path -LiteralPath $PidFile) {
    Remove-Item -LiteralPath $PidFile -Force
}

if (docker info 2>$null) {
    $running = @(docker ps --format "{{.Names}}")
    $toStop = @($NexentContainers | Where-Object { $_ -in $running })
    if ($toStop.Count) {
        docker stop $toStop | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "docker stop failed" }
    }
    Write-Host "Stopped existing Nexent containers without recreating or deleting them."
}

if (-not $KeepDockerDesktop) {
    docker desktop stop
    if ($LASTEXITCODE -ne 0) { throw "docker desktop stop failed" }
    Write-Host "Docker Desktop stopped. Docker CPU and WSL memory can now be released." -ForegroundColor Green
} else {
    Write-Host "Docker Desktop is still running; all Nexent containers are stopped." -ForegroundColor Yellow
}

Write-Host "State preserved: Nexent volumes, AOI data, models, Skills, and configuration were not deleted." -ForegroundColor Green
