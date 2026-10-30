$ErrorActionPreference = "Stop"
$AgentRoot = "D:\competition\aoi-agent"
$Python = Join-Path $AgentRoot ".venv\Scripts\python.exe"
$Config = Join-Path $AgentRoot "config.json"
$LogRoot = Join-Path $AgentRoot "data\service_logs"
$PidFile = Join-Path $AgentRoot "data\service_pids.json"
$InfraContainers = @(
    "nexent-postgresql", "nexent-redis", "nexent-elasticsearch", "nexent-minio",
    "supabase-db-mini", "supabase-auth-mini", "supabase-kong-mini"
)
$AppContainers = @(
    "nexent-config", "nexent-mcp", "nexent-data-process", "nexent-runtime",
    "nexent-northbound", "nexent-web"
)

function Test-DockerReady {
    & cmd.exe /d /c "docker info >nul 2>&1"
    return $LASTEXITCODE -eq 0
}

if (-not (Test-Path -LiteralPath $Python)) { throw "AOI virtual environment not found: $Python" }
if (-not (Test-Path -LiteralPath $Config)) { throw "AOI config not found: $Config" }

if (-not (Test-DockerReady)) {
    Write-Host "Starting Docker Desktop..."
    docker desktop start
    $deadline = (Get-Date).AddMinutes(3)
    do {
        Start-Sleep -Seconds 3
        $ready = Test-DockerReady
    } until ($ready -or (Get-Date) -gt $deadline)
    if (-not $ready) { throw "Docker Desktop did not become ready within 3 minutes." }
}

New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null

function Test-Port([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

$apiProcess = $null
$mcpProcess = $null
if (-not (Test-Port 8100)) {
    $apiProcess = Start-Process -FilePath $Python -WorkingDirectory $AgentRoot -ArgumentList @(
        "-m", "uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8100", "--workers", "1"
    ) -WindowStyle Hidden -RedirectStandardOutput (Join-Path $LogRoot "api.stdout.log") -RedirectStandardError (Join-Path $LogRoot "api.stderr.log") -PassThru
}
if (-not (Test-Port 8101)) {
    $mcpProcess = Start-Process -FilePath $Python -WorkingDirectory $AgentRoot -ArgumentList @(
        "-m", "mcp_server.server"
    ) -WindowStyle Hidden -RedirectStandardOutput (Join-Path $LogRoot "mcp.stdout.log") -RedirectStandardError (Join-Path $LogRoot "mcp.stderr.log") -PassThru
}

$existing = @{}
if (Test-Path -LiteralPath $PidFile) {
    try {
        $oldState = Get-Content -Raw -LiteralPath $PidFile -Encoding UTF8 | ConvertFrom-Json
        foreach ($property in $oldState.PSObject.Properties) {
            $existing[$property.Name] = $property.Value
        }
    } catch { $existing = @{} }
}
if ($apiProcess) { $existing.api_pid = $apiProcess.Id }
if ($mcpProcess) { $existing.mcp_pid = $mcpProcess.Id }
$existing.api_port = 8100
$existing.mcp_port = 8101
$existing.project_root = $AgentRoot
$existing.started_at = (Get-Date).ToUniversalTime().ToString("o")
$existing | ConvertTo-Json | Set-Content -LiteralPath $PidFile -Encoding UTF8

$apiDeadline = (Get-Date).AddMinutes(2)
do {
    Start-Sleep -Seconds 2
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8100/health" -TimeoutSec 5
        $apiReady = $health.status -eq "ok"
    } catch { $apiReady = $false }
} until ($apiReady -or (Get-Date) -gt $apiDeadline)
if (-not $apiReady) { throw "AOI API failed to start. Check $LogRoot\api.stderr.log" }

$mcpReady = $false
for ($attempt = 1; $attempt -le 3; $attempt++) {
    & $Python -X utf8 (Join-Path $AgentRoot "scripts\check_mcp.py")
    if ($LASTEXITCODE -eq 0) { $mcpReady = $true; break }
    Start-Sleep -Seconds 3
}
if (-not $mcpReady) { throw "AOI MCP failed to start. Check $LogRoot\mcp.stderr.log" }

$existingContainers = @(docker ps -a --format "{{.Names}}")
$requiredContainers = @($InfraContainers + $AppContainers)
$missingContainers = @($requiredContainers | Where-Object { $_ -notin $existingContainers })
if ($missingContainers.Count) {
    throw "Nexent containers are missing: $($missingContainers -join ', '). Run the official Nexent deployment once."
}

docker start $InfraContainers | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Failed to start Nexent infrastructure containers." }

$infraDeadline = (Get-Date).AddMinutes(4)
do {
    Start-Sleep -Seconds 3
    $infraReady = $true
    foreach ($name in $InfraContainers) {
        $container = docker inspect $name | ConvertFrom-Json
        if (-not $container.State.Running) { $infraReady = $false; break }
        if ($container.State.Health -and $container.State.Health.Status -ne "healthy") { $infraReady = $false; break }
    }
} until ($infraReady -or (Get-Date) -gt $infraDeadline)
if (-not $infraReady) { throw "Nexent infrastructure did not become healthy within 4 minutes." }

docker start $AppContainers | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Failed to start Nexent application containers." }

$sandboxExists = docker ps -a --filter "name=^/nexent-runtime-sandbox$" --format "{{.Names}}"
$sandboxRunning = docker ps --filter "name=^/nexent-runtime-sandbox$" --format "{{.Names}}"
if ($sandboxExists -eq "nexent-runtime-sandbox" -and $sandboxRunning -ne "nexent-runtime-sandbox") {
    docker start nexent-runtime-sandbox | Out-Null
}

$webDeadline = (Get-Date).AddMinutes(5)
do {
    Start-Sleep -Seconds 3
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:3000" -UseBasicParsing -TimeoutSec 10
        $webReady = $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    } catch { $webReady = $false }
} until ($webReady -or (Get-Date) -gt $webDeadline)
if (-not $webReady) { throw "Nexent Web failed to become ready within 5 minutes." }

Write-Host "AOI API: http://localhost:8100" -ForegroundColor Cyan
Write-Host "AOI MCP: http://localhost:8101/mcp" -ForegroundColor Cyan
Write-Host "Nexent: http://localhost:3000" -ForegroundColor Cyan
Write-Host "Nexent container MCP URL remains http://host.docker.internal:8101/mcp" -ForegroundColor Green
Write-Host "Nexent and AOI startup checks passed." -ForegroundColor Green
Start-Process "http://localhost:3000"
