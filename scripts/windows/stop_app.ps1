$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$RuntimeDir = Join-Path $Root ".local-data\runtime"

function Get-RootAliases($Path) {
    $aliases = @($Path)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -ne $item -and $null -ne $item.Target) {
        foreach ($target in $item.Target) {
            if (![string]::IsNullOrWhiteSpace($target)) {
                $resolvedTarget = Resolve-Path -LiteralPath $target -ErrorAction SilentlyContinue
                if ($null -ne $resolvedTarget) {
                    $aliases += $resolvedTarget.Path
                } else {
                    $aliases += $target
                }
            }
        }
    }
    return $aliases | Select-Object -Unique
}

$RootAliases = Get-RootAliases $Root

function Test-CommandLineContainsProjectRoot($CommandLine) {
    if ([string]::IsNullOrWhiteSpace($CommandLine)) {
        return $false
    }
    foreach ($alias in $RootAliases) {
        if ($CommandLine.Contains($alias)) {
            return $true
        }
    }
    return $false
}

function Stop-Recorded($Name) {
    $recordPath = Join-Path $RuntimeDir "$Name.json"
    if (!(Test-Path $recordPath)) {
        Write-Host "${Name}: no runtime record"
        return
    }
    try {
        $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
        $process = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
        if ($null -eq $process) {
            Write-Host "${Name}: process already stopped"
            Remove-Item -LiteralPath $recordPath -Force -ErrorAction SilentlyContinue
        } else {
            $commandLine = (Get-CimInstance Win32_Process -Filter "ProcessId=$($record.pid)").CommandLine
            $isOwned = $false
            $recordRootMatches = ($record.root -eq $Root)
            $startTimeMatches = $true
            if ($record.process_start_time) {
                try {
                    $recordStart = [datetime]::Parse($record.process_start_time).ToUniversalTime()
                    $actualStart = $process.StartTime.ToUniversalTime()
                    $startTimeMatches = [math]::Abs(($actualStart - $recordStart).TotalSeconds) -lt 5
                } catch {
                    $startTimeMatches = $false
                }
            }
            if ($Name -eq "backend") {
                $isOwned = $recordRootMatches -and $startTimeMatches -and $commandLine -and $commandLine.Contains("apps.backend.app.main:app") -and $commandLine.Contains("--port 8000")
            }
            if ($Name -eq "frontend") {
                $isOwned = $recordRootMatches -and $startTimeMatches -and $commandLine -and (Test-CommandLineContainsProjectRoot $commandLine) -and $commandLine.Contains("vite")
            }
            if ($Name -eq "worker") {
                $isOwned = $recordRootMatches -and $startTimeMatches -and $commandLine -and (Test-CommandLineContainsProjectRoot $commandLine) -and $commandLine.Contains("apps.worker.processing_worker")
            }
            if ($isOwned) {
                Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
                try {
                    Wait-Process -Id $process.Id -Timeout 5 -ErrorAction Stop
                    Write-Host "${Name}: stopped PID $($process.Id)"
                } catch {
                    $remaining = Get-Process -Id $process.Id -ErrorAction SilentlyContinue
                    if ($null -ne $remaining) {
                        Stop-Process -Id $process.Id -Force
                        Write-Host "${Name}: force-stopped PID $($process.Id)"
                    } else {
                        Write-Host "${Name}: stopped PID $($process.Id)"
                    }
                }
                Remove-Item -LiteralPath $recordPath -Force -ErrorAction SilentlyContinue
            } else {
                Write-Host "${Name}: PID $($record.pid) did not match project root; left untouched"
            }
        }
    } catch {
        Write-Host "${Name}: runtime record could not be validated; left untouched"
    }
}

New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
Stop-Recorded "frontend"
Stop-Recorded "worker"
Stop-Recorded "backend"
