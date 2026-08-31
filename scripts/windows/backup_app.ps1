$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$RuntimeDir = Join-Path $Root ".local-data\runtime"
$PythonExe = Join-Path $Root ".venv\Scripts\python.exe"
$Database = Join-Path $Root ".local-data\tva.sqlite3"

$runtimeRecords = @("backend", "frontend", "worker") | ForEach-Object { Test-Path (Join-Path $RuntimeDir "$_.json") }
if ($runtimeRecords -contains $true) {
    Write-Error "Stop the app with stop_app.bat before creating a backup. No data was changed."
}
if (!(Test-Path $PythonExe)) { Write-Error "Python environment is missing. Run setup_app.bat first." }
& $PythonExe (Join-Path $Root "scripts\database_backup.py") backup --database $Database $args
exit $LASTEXITCODE
