$ErrorActionPreference = "Stop"
$AgentRoot = "D:\competition\aoi-agent"
$Python = Join-Path $AgentRoot ".venv\Scripts\python.exe"

$health = Invoke-RestMethod -Uri "http://127.0.0.1:8100/health" -TimeoutSec 10
if ($health.status -ne "ok") { throw "AOI API health check failed." }
Write-Host "AOI API: PASS (version $($health.version))" -ForegroundColor Green

& $Python -X utf8 (Join-Path $AgentRoot "scripts\check_mcp.py")
if ($LASTEXITCODE -ne 0) { throw "AOI MCP check failed." }
Write-Host "AOI MCP: PASS" -ForegroundColor Green

$required = @(
    "nexent-config", "nexent-runtime", "nexent-northbound", "nexent-mcp",
    "nexent-data-process", "nexent-web", "nexent-minio", "nexent-postgresql",
    "nexent-redis", "nexent-elasticsearch", "supabase-auth-mini",
    "supabase-kong-mini", "supabase-db-mini"
)
$running = @(docker ps --format "{{.Names}}")
$missing = @($required | Where-Object { $_ -notin $running })
if ($missing.Count) { throw "Missing Nexent containers: $($missing -join ', ')" }
Write-Host "Nexent containers: PASS" -ForegroundColor Green

$response = Invoke-WebRequest -Uri "http://127.0.0.1:3000" -UseBasicParsing -TimeoutSec 15
if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 500) { throw "Nexent Web check failed." }
Write-Host "Nexent Web: PASS" -ForegroundColor Green
Write-Host "All startup checks passed." -ForegroundColor Green
