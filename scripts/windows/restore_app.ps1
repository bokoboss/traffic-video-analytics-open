$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$RuntimeDir = Join-Path $Root ".local-data\runtime"
$PythonExe = Join-Path $Root ".venv\Scripts\python.exe"
$Database = Join-Path $Root ".local-data\tva.sqlite3"

$runtimeRecords = @("backend", "frontend", "worker") | ForEach-Object { Test-Path (Join-Path $RuntimeDir "$_.json") }
if ($runtimeRecords -contains $true) {
    Write-Error "Stop the app with stop_app.bat before restoring a backup. No data was changed."
}
if (!(Test-Path $PythonExe)) { Write-Error "Python environment is missing. Run setup_app.bat first." }
if ($args.Count -eq 0) { Write-Error "Provide a backup path: restore_app.bat <backup.sqlite3>" }
& $PythonExe (Join-Path $Root "scripts\database_backup.py") restore --database $Database --backup $args[0]
exit $LASTEXITCODE
