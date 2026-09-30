<#
.SYNOPSIS
  Update an EXISTING HFMG Help Desk server install from an extracted release folder.

.DESCRIPTION
  For servers where the app folder is a plain copy (no git). Run from an elevated
  PowerShell, from the extracted release folder (the one containing backend\ and deploy\).

  It: stops the service, backs up the database, copies the new code over the app
  folder (keeping backend\.env and backend\.venv), removes the deleted Twilio files,
  installs requirements, runs the migrations, starts the service and health-checks it.

.EXAMPLE
  .\deploy\update-server.ps1 -ServiceName HFMG-Backend
  .\deploy\update-server.ps1 -ServiceName HFMG-Backend -ServeDashboard
#>
param(
    [Parameter(Mandatory)] [string] $ServiceName,
    [string] $AppDir = 'C:\HorizonApps\HFMG AI Helpdesk Backend',
    [int]    $Port = 8001,
    [string] $PgBin = 'C:\Program Files\PostgreSQL\16\bin',
    [switch] $ServeDashboard   # have the backend serve the bundled dashboard (sets FRONTEND_DIST_DIR)
)

$ErrorActionPreference = 'Stop'
$release = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $AppDir 'backend'
$envFile = Join-Path $backend '.env'
$venvPy  = Join-Path $backend '.venv\Scripts\python.exe'

function Step($t) { Write-Host "`n==> $t" -ForegroundColor Cyan }
function Fail($t) { Write-Host "ERROR: $t" -ForegroundColor Red; exit 1 }

Step 'Checking'
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole('Administrator')) { Fail 'Run from an elevated PowerShell.' }
if (-not (Test-Path (Join-Path $release 'backend\app\main.py'))) { Fail "Run this from the extracted release folder (no backend\app\main.py under $release)." }
if ($release -eq $AppDir) { Fail 'The release folder and the app folder are the same. Extract the release somewhere else.' }
if (-not (Test-Path $envFile)) { Fail "Missing $envFile. Is -AppDir correct?" }
if (-not (Test-Path $venvPy)) { Fail "Missing virtual environment at $backend\.venv." }
if (-not (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)) {
    Write-Host 'Services that look related:'; Get-Service | Where-Object { $_.Name -match 'HFMG|Help|Horizon|uvicorn|Sip' } | Format-Table Name, Status
    Fail "Service '$ServiceName' not found. Re-run with the right -ServiceName."
}

Step "Stopping $ServiceName"
Stop-Service $ServiceName -Force
Start-Sleep -Seconds 3

Step 'Backing up the database'
$dbUrl = (Select-String -Path $envFile -Pattern '^DATABASE_URL=(.+)$').Matches[0].Groups[1].Value.Trim() -replace '\+asyncpg', ''
$backupDir = Join-Path $AppDir 'backups'
New-Item -ItemType Directory -Force $backupDir | Out-Null
$dump = Join-Path $backupDir ("before_sip_{0}.dump" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
$pgDump = Join-Path $PgBin 'pg_dump.exe'
if (-not (Test-Path $pgDump)) { Start-Service $ServiceName; Fail "pg_dump.exe not found in $PgBin (use -PgBin). Service restarted; nothing changed." }
& $pgDump -Fc -f $dump -d $dbUrl
if ($LASTEXITCODE) { Start-Service $ServiceName; Fail 'Database backup failed. Service restarted; nothing changed.' }
Write-Host "Backup: $dump"

Step 'Recording current migration state'
Push-Location $backend
$before = & $venvPy -m alembic current 2>&1 | Select-Object -Last 1
Write-Host "Before: $before   (needed only if you have to roll back)"

Step 'Copying new code (keeping .env and .venv)'
robocopy $release $AppDir /E /XD .venv .git node_modules __pycache__ backups logs /XF .env *.pyc /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { Pop-Location; Fail "robocopy failed ($LASTEXITCODE)" }

Step 'Removing files deleted in this release'
$stale = @(
    'backend\app\voice\twiml.py', 'backend\app\voice\security.py', 'backend\app\voice\routes.py',
    'backend\tests\test_voice_webhooks.py', 'TWILIO_SETUP.md', 'TWILIO_ARCHITECTURE.md'
)
foreach ($f in $stale) { Remove-Item (Join-Path $AppDir $f) -Force -ErrorAction SilentlyContinue }

if ($ServeDashboard -and -not (Select-String -Path $envFile -Pattern '^FRONTEND_DIST_DIR=.+' -Quiet)) {
    Add-Content $envFile "`nFRONTEND_DIST_DIR=$(Join-Path $AppDir 'deploy\frontend-dist')"
    Write-Host 'Set FRONTEND_DIST_DIR in .env'
}
if (-not (Select-String -Path $envFile -Pattern '^VOICE_SIP_GATEWAY_TOKEN=.+' -Quiet)) {
    Write-Host 'WARNING: VOICE_SIP_GATEWAY_TOKEN is empty in .env; the SIP endpoints will refuse the gateway.' -ForegroundColor Yellow
}

Step 'Installing requirements and migrating'
& $venvPy -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE) { Pop-Location; Fail 'pip install failed. Service is still stopped; database untouched.' }
& $venvPy -m alembic upgrade head
if ($LASTEXITCODE) { Pop-Location; Fail "alembic upgrade failed. Service is still stopped. Restore with pg_restore from $dump if needed, and send the error above." }
$after = & $venvPy -m alembic current 2>&1 | Select-Object -Last 1
Write-Host "After: $after   (expected a1f4c2d9e6b7 (head))"
Pop-Location

Step "Starting $ServiceName"
Start-Service $ServiceName
$ok = $false
for ($i = 0; $i -lt 20 -and -not $ok; $i++) {
    Start-Sleep -Seconds 2
    try { $ok = (Invoke-WebRequest "http://localhost:$Port/api/v1/health" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 } catch { }
}
if (-not $ok) { Fail "Backend did not become healthy on port $Port. Check the service log in the logs folder." }

try {
    $r = Invoke-WebRequest "http://localhost:$Port/api/v1/tickets?page=1&page_size=1" -UseBasicParsing
    Write-Host "Tickets API: $($r.StatusCode)"
} catch { Write-Host "Tickets API failed: $($_.Exception.Message)" -ForegroundColor Yellow }

Write-Host "`nUpdate complete. Backup kept at $dump" -ForegroundColor Green
Write-Host 'Next: confirm the gateway can reach the backend, then place a test call.'
