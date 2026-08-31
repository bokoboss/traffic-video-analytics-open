$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$RuntimeDir = Join-Path $Root ".local-data\runtime"
$LogsDir = Join-Path $Root ".local-data\logs"
$SetupMarker = Join-Path $RuntimeDir "setup-complete.json"
$ReleaseFile = Join-Path $Root "release.json"
$PythonExe = Join-Path $Root ".venv\Scripts\python.exe"
$BackendHealthUrl = "http://127.0.0.1:8000/api/v1/health"
$BackendReadinessUrl = "http://127.0.0.1:8000/api/v1/readiness"
$FrontendUrl = "http://127.0.0.1:5174"
$RunId = "startup_" + [guid]::NewGuid().ToString("N").Substring(0, 12)
$OwnerToken = [guid]::NewGuid().ToString("N")
$WorkerId = "local-worker"
$WorkerInstanceToken = [guid]::NewGuid().ToString("N")
$DiagnosticsEnabled = ($env:TRAFFIC_APP_DIAGNOSTICS -match "^(1|true|yes|on)$")
$NoBrowser = ($env:TVA_NO_BROWSER -match "^(1|true|yes|on)$")
. (Join-Path $PSScriptRoot "runtime_tools.ps1")

function New-PhaseTimer {
    [pscustomobject]@{ Started = Get-Date; Last = Get-Date }
}

function Write-Phase($Timer, $Name, $Status = "ok", $Detail = "") {
    $now = Get-Date
    $elapsedMs = [math]::Round(($now - $Timer.Last).TotalMilliseconds, 3)
    $totalMs = [math]::Round(($now - $Timer.Started).TotalMilliseconds, 3)
    $Timer.Last = $now
    if ($DiagnosticsEnabled) {
        $record = @{
            event = "launcher_startup_phase"
            run_id = $RunId
            phase = $Name
            status = $Status
            elapsed_ms = $elapsedMs
            total_ms = $totalMs
            timestamp = $now.ToUniversalTime().ToString("o")
        }
        if (![string]::IsNullOrWhiteSpace($Detail)) {
            $record.detail = $Detail
        }
        Add-Content -LiteralPath (Join-Path $LogsDir "startup-$RunId.jsonl") -Encoding UTF8 -Value ($record | ConvertTo-Json -Compress)
    }
}

function Test-Port($Port) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $connection = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $connection.AsyncWaitHandle.WaitOne(150)) {
            return $false
        }
        $client.EndConnect($connection)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-ListenerPid($Port) {
    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $connection) {
        return $null
    }
    return [int]$connection.OwningProcess
}

function Wait-Http($Url, $Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2 | Out-Null
            return $true
        } catch {
            Start-Sleep -Milliseconds 300
        }
    }
    return $false
}

function Wait-Json($Url, $Seconds, [scriptblock]$Predicate) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $payload = Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 2
            if (& $Predicate $payload) { return $payload }
        } catch {
            # Poll the real endpoint state until it becomes ready.
        }
        Start-Sleep -Milliseconds 300
    }
    return $null
}

function Rotate-Log($Path, $MaxBytes = 5242880, $Keep = 3) {
    if (!(Test-Path $Path) -or (Get-Item -LiteralPath $Path).Length -lt $MaxBytes) { return }
    for ($index = $Keep - 1; $index -ge 1; $index--) {
        $older = "$Path.$index"
        $newer = "$Path." + ($index + 1)
        if (Test-Path $older) { Move-Item -LiteralPath $older -Destination $newer -Force -ErrorAction SilentlyContinue }
    }
    Move-Item -LiteralPath $Path -Destination "$Path.1" -Force -ErrorAction SilentlyContinue
}

function Test-RecordedProcess($Name) {
    $recordPath = Join-Path $RuntimeDir "$Name.json"
    if (!(Test-Path $recordPath)) { return $false }
    try {
        $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
        $process = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
        return $null -ne $process -and $record.root -eq $Root -and $record.owner_token
    } catch {
        return $false
    }
}

function Stop-OwnedProcesses {
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\windows\stop_app.ps1") | Out-Host
}

function Open-Frontend {
    if ($NoBrowser) {
        Write-Host "Browser launch skipped by TVA_NO_BROWSER=1."
        return
    }
    try {
        Start-Process $FrontendUrl
    } catch {
        Write-Host "Browser launch failed. Open this URL manually: $FrontendUrl"
    }
}

function Write-RuntimeRecord($Name, $ProcessId, $LauncherPid, $Command, $Port) {
    $processStart = $null
    try { $processStart = (Get-Process -Id $ProcessId -ErrorAction Stop).StartTime.ToUniversalTime().ToString("o") } catch { }
    Set-Content -LiteralPath (Join-Path $RuntimeDir "$Name.json") -Encoding UTF8 -Value (@{
        pid = $ProcessId
        launcher_pid = $LauncherPid
        command = $Command
        root = $Root
        port = $Port
        started_at = (Get-Date).ToUniversalTime().ToString("o")
        process_start_time = $processStart
        owner_token = $OwnerToken
        run_id = $RunId
    } | ConvertTo-Json)
}

Set-Location $Root
New-Item -ItemType Directory -Force -Path $RuntimeDir, $LogsDir | Out-Null
Get-ChildItem -LiteralPath $LogsDir -Filter "*.out.log" -ErrorAction SilentlyContinue | ForEach-Object { Rotate-Log $_.FullName }
Get-ChildItem -LiteralPath $LogsDir -Filter "*.err.log" -ErrorAction SilentlyContinue | ForEach-Object { Rotate-Log $_.FullName }
Get-ChildItem -LiteralPath $LogsDir -Filter "startup-*.jsonl" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 20 |
    Remove-Item -Force -ErrorAction SilentlyContinue

$timer = New-PhaseTimer
Write-Host "Starting Traffic Video Analytics local services..."
Write-Host "Frontend URL: $FrontendUrl"
Write-Phase $timer "launcher_feedback"

if (!(Test-Path $SetupMarker) -or !(Test-Path $PythonExe)) {
    Write-Phase $timer "prerequisite_validation" "failed" "setup_missing"
    Write-Error "Setup has not completed. Run setup_app.bat first."
}
Write-Phase $timer "python_runtime_resolution"

try {
    $NodeRuntime = Resolve-TvaNodeRuntime $Root
    $PnpmCommand = Resolve-TvaPnpmCommand $Root $NodeRuntime
} catch {
    Write-TvaPortableNodeInstructions
    Write-Phase $timer "node_runtime_resolution" "failed" "node_or_pnpm_missing"
    Write-Error "Runtime is not ready for launch. Run setup_app.bat after preparing the approved Node.js and pnpm runtime. Details: $_"
}
$env:PATH = Get-TvaChildPath $NodeRuntime $PnpmCommand
Write-Phase $timer "node_pnpm_resolution"

if ((Test-Path (Join-Path $RuntimeDir "backend.json")) -or (Test-Path (Join-Path $RuntimeDir "frontend.json")) -or (Test-Path (Join-Path $RuntimeDir "worker.json"))) {
    if ((Test-RecordedProcess "backend") -and (Test-RecordedProcess "frontend") -and (Test-RecordedProcess "worker") -and (Wait-Json $BackendHealthUrl 2 { param($payload) $payload.status -eq "ok" }) -and (Wait-Http $FrontendUrl 2) -and (Wait-Json $BackendReadinessUrl 2 { param($payload) $payload.application_ready -eq $true })) {
        Write-Host "Application is already running."
        Write-Host "Backend alive:   $BackendHealthUrl"
        Write-Host "Frontend ready: $FrontendUrl"
        Write-Phase $timer "duplicate_start_detection"
        Open-Frontend
        Write-Phase $timer "browser_launch"
        exit 0
    }
    Write-Host "Stale or incomplete project runtime records found. Cleaning project-owned records before launch."
    Stop-OwnedProcesses
    Write-Phase $timer "stale_process_cleanup"
}

if (Test-Port 8000) {
    Write-Phase $timer "port_selection" "failed" "backend_port_conflict"
    Write-Error "Port 8000 is already in use by a non-launcher process. Stop that process or change repository configuration."
}
if (Test-Port 5174) {
    Write-Phase $timer "port_selection" "failed" "frontend_port_conflict"
    Write-Error "Port 5174 is already in use by a non-launcher process. Stop that process or change repository configuration."
}
Write-Phase $timer "port_selection"

$env:TVA_DB_PATH = Join-Path $Root ".local-data\tva.sqlite3"
$env:TVA_LOCAL_DATA_DIR = Join-Path $Root ".local-data"
$env:TVA_WORKER_ID = $WorkerId
$env:TVA_WORKER_INSTANCE_TOKEN = $WorkerInstanceToken
$env:TVA_WORKER_HEARTBEAT_TTL_SECONDS = "5"
$env:TVA_FRONTEND_URL = $FrontendUrl
$env:VITE_API_BASE_URL = "http://127.0.0.1:8000"
$release = Get-Content -LiteralPath $ReleaseFile -Raw | ConvertFrom-Json
$gitSha = (& git -C $Root rev-parse HEAD 2>$null | Select-Object -First 1).Trim()
$env:TVA_RELEASE_VERSION = [string]$release.release_version
$env:TVA_GIT_COMMIT_SHA = if ($gitSha) { $gitSha } else { "unknown" }
Write-Phase $timer "environment_preparation"

$backendOutLog = Join-Path $LogsDir "backend.out.log"
$backendErrLog = Join-Path $LogsDir "backend.err.log"
$frontendOutLog = Join-Path $LogsDir "frontend.out.log"
$frontendErrLog = Join-Path $LogsDir "frontend.err.log"
$workerOutLog = Join-Path $LogsDir "worker.out.log"
$workerErrLog = Join-Path $LogsDir "worker.err.log"

$backend = Start-Process -FilePath $PythonExe -ArgumentList @("-m", "uvicorn", "apps.backend.app.main:app", "--host", "127.0.0.1", "--port", "8000") -WorkingDirectory $Root -RedirectStandardOutput $backendOutLog -RedirectStandardError $backendErrLog -WindowStyle Hidden -PassThru
Write-RuntimeRecord "backend" $backend.Id $backend.Id "uvicorn apps.backend.app.main:app" 8000
Write-Phase $timer "backend_spawn"

$health = Wait-Json $BackendHealthUrl 30 { param($payload) $payload.status -eq "ok" -and $payload.database -eq "sqlite" }
if ($null -eq $health) {
    Write-Phase $timer "backend_health_available" "failed"
    Write-Host "Backend did not become healthy. See $backendOutLog and $backendErrLog"
    Stop-OwnedProcesses
    exit 2
}
$backendPid = Get-ListenerPid 8000
if ($null -ne $backendPid) {
    Write-RuntimeRecord "backend" $backendPid $backend.Id "uvicorn apps.backend.app.main:app" 8000
}
Write-Host "Backend alive:   $BackendHealthUrl"
Write-Phase $timer "backend_health_available"

$worker = Start-Process -FilePath $PythonExe -ArgumentList @("-m", "apps.worker.processing_worker", "--db", $env:TVA_DB_PATH, "--worker-id", $WorkerId) -WorkingDirectory $Root -RedirectStandardOutput $workerOutLog -RedirectStandardError $workerErrLog -WindowStyle Hidden -PassThru
Write-RuntimeRecord "worker" $worker.Id $worker.Id "apps.worker.processing_worker" $null
Write-Phase $timer "worker_spawn"

# Process non-exit is only an early sanity signal. The backend-observable
# heartbeat below is the authoritative worker readiness contract.
Start-Sleep -Milliseconds 500
if ($worker.HasExited) {
    Write-Phase $timer "worker_process_sanity" "failed"
    Write-Host "Processing worker exited during startup. See $workerOutLog and $workerErrLog"
    Stop-OwnedProcesses
    exit 4
}
Write-Phase $timer "worker_process_sanity"

$workerReadiness = Wait-Json $BackendReadinessUrl 15 { param($payload) $payload.components.worker.ready -eq $true -and @("READY", "BUSY") -contains $payload.components.worker.state }
if ($null -eq $workerReadiness) {
    Write-Phase $timer "worker_heartbeat_readiness" "failed"
    Write-Host "Processing worker did not publish a fresh heartbeat. See $workerOutLog and $workerErrLog"
    Stop-OwnedProcesses
    exit 4
}
Write-Host "Processing worker ready: $($workerReadiness.components.worker.state)"
Write-Phase $timer "worker_heartbeat_readiness"

$frontend = Start-Process -FilePath $PnpmCommand.Executable -ArgumentList @("--filter", "@traffic-video-analytics/frontend", "exec", "vite", "--host", "127.0.0.1", "--port", "5174", "--strictPort") -WorkingDirectory $Root -RedirectStandardOutput $frontendOutLog -RedirectStandardError $frontendErrLog -WindowStyle Hidden -PassThru
Write-RuntimeRecord "frontend" $frontend.Id $frontend.Id "vite frontend dev 5174" 5174
Write-Phase $timer "frontend_spawn"

if (!(Wait-Http $FrontendUrl 30)) {
    Write-Phase $timer "frontend_http_available" "failed"
    Write-Host "Frontend did not become ready. See $frontendOutLog and $frontendErrLog"
    Stop-OwnedProcesses
    exit 3
}
$frontendPid = Get-ListenerPid 5174
if ($null -ne $frontendPid) { Write-RuntimeRecord "frontend" $frontendPid $frontend.Id "vite frontend dev 5174" 5174 }
Write-Host "Frontend ready: $FrontendUrl"
Write-Phase $timer "frontend_http_available"

$readiness = Wait-Json $BackendReadinessUrl 15 { param($payload) $payload.application_ready -eq $true -and @("READY", "READY_WITH_WARNINGS") -contains $payload.application_status }
if ($null -ne $readiness) {
    Write-Host "Application readiness: $($readiness.application_status); processing: $($readiness.processing_status) ($BackendReadinessUrl)"
    Write-Phase $timer "application_readiness_poll"
} else {
    Write-Phase $timer "application_readiness_poll" "failed"
    Write-Host "Application readiness did not become available. See $backendOutLog, $backendErrLog, and the worker logs"
    Stop-OwnedProcesses
    exit 5
}

Open-Frontend
Write-Phase $timer "browser_launch"

Write-Host "Use stop_app.bat to stop project-owned services."
