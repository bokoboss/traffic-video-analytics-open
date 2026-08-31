$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Venv = Join-Path $Root ".venv"
$RuntimeDir = Join-Path $Root ".local-data\runtime"
$LogsDir = Join-Path $Root ".local-data\logs"
$SetupLog = Join-Path $LogsDir "setup.log"
. (Join-Path $PSScriptRoot "runtime_tools.ps1")

trap {
    if ($SetupLog) { Add-Content -LiteralPath $SetupLog -Encoding UTF8 -Value "[$((Get-Date).ToUniversalTime().ToString('o'))] setup_failed $($_.Exception.Message)" }
    throw
}

function Write-Step($Message) {
    Write-Host ""
    Write-Host "== $Message =="
    Add-Content -LiteralPath $SetupLog -Encoding UTF8 -Value "[$((Get-Date).ToUniversalTime().ToString('o'))] $Message"
}

function Require-Command($Name, $InstallHint, [switch]$Optional) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $cmd) {
        if ($Optional) {
            Write-Host "${Name}: missing (optional). $InstallHint"
            return $null
        }
        Write-Error "$Name is required. $InstallHint"
    }
    $version = & $Name --version 2>$null | Select-Object -First 1
    Write-Host "${Name}: $version"
    return $cmd.Source
}

Set-Location $Root
New-Item -ItemType Directory -Force -Path $RuntimeDir, $LogsDir, (Join-Path $Root ".local-data\media"), (Join-Path $Root ".local-data\previews"), (Join-Path $Root ".local-data\exports"), (Join-Path $Root ".local-data\diagnostics"), (Join-Path $Root ".local-data\backups") | Out-Null
if (Test-Path $SetupLog) {
    $previousSetupLog = Join-Path $LogsDir "setup.1.log"
    Move-Item -LiteralPath $SetupLog -Destination $previousSetupLog -Force -ErrorAction SilentlyContinue
}
Add-Content -LiteralPath $SetupLog -Encoding UTF8 -Value "[$((Get-Date).ToUniversalTime().ToString('o'))] setup_started"

Write-Step "Supported Pilot host"
$os = Get-CimInstance Win32_OperatingSystem
$architecture = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
if ($os.Caption -notmatch "Windows 11") { Write-Error "Pilot support requires Windows 11 x64. Detected: $($os.Caption)" }
if ($architecture -notmatch "AMD64|x64|ARM64") { Write-Error "Pilot support requires a 64-bit Windows host. Detected: $architecture" }
Write-Host "OS: $($os.Caption) ($architecture)"

Write-Step "Repository"
Write-Host "Root: $Root"

Write-Step "Required tools"
$null = Require-Command "python" "Install Python 3.11 or newer and ensure it is on PATH."
try {
    $NodeRuntime = Resolve-TvaNodeRuntime $Root
} catch {
    Write-TvaPortableNodeInstructions
    Write-Error $_
}
Write-Host "node: $($NodeRuntime.Version) ($($NodeRuntime.Executable))"
Write-Host "node source: $($NodeRuntime.Source)"
try {
    $PnpmCommand = Resolve-TvaPnpmCommand $Root $NodeRuntime
} catch {
    Write-Host ""
    Write-Host "pnpm is required for frontend dependency setup. The launcher will not silently download or upgrade package managers."
    Write-Host "Required pnpm version: $RequiredPnpmVersion"
    Write-Host "Prepare .local-tools\pnpm\pnpm.cmd, configure TVA_PNPM_CMD, or ask IT to install pnpm."
    Write-Error $_
}
Write-Host "pnpm: $($PnpmCommand.Version) ($($PnpmCommand.Executable))"
Write-Host "pnpm source: $($PnpmCommand.Source)"
$env:PATH = Get-TvaChildPath $NodeRuntime $PnpmCommand
$npm = Get-Command "npm" -ErrorAction SilentlyContinue
if ($null -eq $npm) {
    Write-Host "npm: missing (not required by repository scripts; install Node.js system package if npm is required from a fresh shell)"
} else {
    Write-Host "npm: $(& $npm.Source --version) ($($npm.Source))"
}

$pythonVersion = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
$pythonOk = python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Python $pythonVersion is unsupported. Python 3.11 or newer is required."
}

Write-Step "Optional media and GPU tools"
$null = Require-Command "ffmpeg" "Install FFmpeg and add its bin directory to PATH for media inspection." -Optional
$null = Require-Command "ffprobe" "Install FFmpeg and add its bin directory to PATH for media inspection." -Optional
nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "nvidia-smi: missing or no NVIDIA GPU detected (optional)."
}

Write-Step "Python environment"
if (!(Test-Path $Venv)) {
    python -m venv $Venv
}
$PythonExe = Join-Path $Venv "Scripts\python.exe"
& $PythonExe -m pip install --disable-pip-version-check -r (Join-Path $Root "requirements-dev.txt")
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Step "Frontend dependencies"
& $PnpmCommand.Executable install --frozen-lockfile
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Step "Post-setup validation"
& $PythonExe scripts\check_pilot_runtime.py --strict-windows
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonExe scripts\check_media_runtime.py --readiness
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
& $PythonExe scripts\check_ai_runtime.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PythonExe -m compileall -q apps tests scripts tools
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $PnpmCommand.Executable run build:frontend
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$release = Get-Content -LiteralPath (Join-Path $Root "release.json") -Raw | ConvertFrom-Json
$gitSha = (& git -C $Root rev-parse HEAD 2>$null | Select-Object -First 1).Trim()
Set-Content -LiteralPath (Join-Path $RuntimeDir "setup-complete.json") -Encoding UTF8 -Value (@{
    completed_at = (Get-Date).ToUniversalTime().ToString("o")
    release_version = $release.release_version
    git_commit_sha = if ($gitSha) { $gitSha } else { "unknown" }
    python = $pythonVersion
    node_source = $NodeRuntime.Source
    node_version = $NodeRuntime.Version
    pnpm_version = $PnpmCommand.Version
} | ConvertTo-Json)
Add-Content -LiteralPath $SetupLog -Encoding UTF8 -Value "[$((Get-Date).ToUniversalTime().ToString('o'))] setup_complete release=$($release.release_version) sha=$gitSha"

Write-Host ""
Write-Host "Setup complete."
