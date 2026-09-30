<#
.SYNOPSIS
  One-shot server install for the HFMG AI Help Desk (Windows Server).

.DESCRIPTION
  Run from an elevated PowerShell in the repository root, after `git clone` and
  `git checkout feature/sip-only`. It:
    1. checks prerequisites (Python 3.12, PostgreSQL client tools, NSSM)
    2. creates the Python venv and installs requirements
    3. creates the PostgreSQL user and database
    4. writes backend\.env (never overwrites an existing one) and generates the
       SIP gateway token
    5. runs the migrations and seeds the categories
    6. installs and starts the backend as a Windows service (serves API + dashboard)
    7. opens the firewall port and runs a health check

  Safe to re-run: existing .env, database and service are left in place.

.EXAMPLE
  .\deploy\install-server.ps1 -PgAdminPassword 'postgres-pw' -DbPassword 'new-db-pw' `
      -OpenAiApiKey 'sk-...' -ServerAddress '172.22.6.98'
#>
param(
    [Parameter(Mandatory)] [string] $PgAdminPassword,   # password of the 'postgres' superuser
    [Parameter(Mandatory)] [string] $DbPassword,        # password to give the new 'hfmg' DB user
    [Parameter(Mandatory)] [string] $OpenAiApiKey,
    [string] $ServerAddress = '172.22.6.98',            # how staff and the gateway reach this server
    [int]    $Port = 8001,
    [string] $PgBin = 'C:\Program Files\PostgreSQL\16\bin',
    [string] $ServiceName = 'HFMG-Backend',
    [string] $AllowedRemoteAddresses = 'LocalSubnet'    # firewall scope for the backend port
)

$ErrorActionPreference = 'Stop'
$repo    = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repo 'backend'
$dist    = Join-Path $repo 'deploy\frontend-dist'
$logs    = Join-Path $repo 'logs'

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "ERROR: $text" -ForegroundColor Red; exit 1 }

# --- 1. Prerequisites --------------------------------------------------------
Step 'Checking prerequisites'
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole('Administrator')) {
    Fail 'Run this script from an elevated (Administrator) PowerShell.'
}
$py = $null
foreach ($candidate in @('py -3.12', 'python')) {
    try {
        $v = & cmd /c "$candidate --version 2>&1"
        if ($v -match 'Python 3\.(11|12)\.') { $py = $candidate; break }
    } catch { }
}
if (-not $py) { Fail 'Python 3.11 or 3.12 is required (not 3.13+). Install it from python.org and re-run.' }
$psql = Join-Path $PgBin 'psql.exe'
if (-not (Test-Path $psql)) { Fail "psql.exe not found in '$PgBin'. Install PostgreSQL 16 or pass -PgBin." }
$nssm = (Get-Command nssm -ErrorAction SilentlyContinue).Source
if (-not $nssm) { Fail 'nssm.exe not found on PATH. Download it from nssm.cc and add it to PATH.' }
if (-not (Test-Path (Join-Path $dist 'index.html'))) { Fail "Built dashboard missing at $dist. Did you check out the right branch?" }

# --- 2. Python environment ---------------------------------------------------
Step 'Creating virtual environment and installing requirements'
$venvPy = Join-Path $backend '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) { & cmd /c "$py -m venv `"$backend\.venv`""; if ($LASTEXITCODE) { Fail 'venv creation failed' } }
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet -r (Join-Path $backend 'requirements.txt')
if ($LASTEXITCODE) { Fail 'pip install failed' }

# --- 3. Database -------------------------------------------------------------
Step 'Creating PostgreSQL user and database'
$env:PGPASSWORD = $PgAdminPassword
function Psql($db, $sql) { & $psql -h localhost -U postgres -d $db -v ON_ERROR_STOP=1 -tAc $sql }
$userExists = Psql 'postgres' "SELECT 1 FROM pg_roles WHERE rolname='hfmg'"
if (-not $userExists) { Psql 'postgres' "CREATE USER hfmg WITH PASSWORD '$($DbPassword -replace "'", "''")'" | Out-Null }
$dbExists = Psql 'postgres' "SELECT 1 FROM pg_database WHERE datname='hfmg_helpdesk'"
if (-not $dbExists) { Psql 'postgres' 'CREATE DATABASE hfmg_helpdesk OWNER hfmg' | Out-Null }
Psql 'hfmg_helpdesk' 'CREATE EXTENSION IF NOT EXISTS citext' | Out-Null
Remove-Item Env:PGPASSWORD

# --- 4. Configuration --------------------------------------------------------
Step 'Writing backend\.env'
$envFile = Join-Path $backend '.env'
$token = $null
if (Test-Path $envFile) {
    Write-Host '.env already exists; leaving it untouched.'
    $match = Select-String -Path $envFile -Pattern '^VOICE_SIP_GATEWAY_TOKEN=(.+)$'
    if ($match) { $token = $match.Matches[0].Groups[1].Value }
} else {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    $values = @{
        DATABASE_URL           = "postgresql+asyncpg://hfmg:$DbPassword@localhost:5432/hfmg_helpdesk"
        OPENAI_API_KEY         = $OpenAiApiKey
        CORS_ORIGINS           = "http://${ServerAddress}:$Port"
        ENVIRONMENT            = 'production'
        ENABLE_VOICE_SIMULATOR = 'false'
        FRONTEND_DIST_DIR      = $dist
        VOICE_SIP_GATEWAY_TOKEN = $token
    }
    $text = Get-Content (Join-Path $backend '.env.example') -Raw
    foreach ($key in $values.Keys) {
        $text = [regex]::Replace($text, "(?m)^$key=.*$", { param($m) "$key=$($values[$key])" }.GetNewClosure())
    }
    Set-Content -Path $envFile -Value $text -Encoding ascii
    Write-Host "Created $envFile. Review the EMAIL_* settings in it before go-live."
}

# --- 5. Migrations and seed --------------------------------------------------
Step 'Running migrations and seeding categories'
Push-Location $backend
try {
    & $venvPy -m alembic upgrade head
    if ($LASTEXITCODE) { Fail 'alembic upgrade failed' }
    & $venvPy seed.py
    if ($LASTEXITCODE) { Fail 'seed.py failed' }
} finally { Pop-Location }

# --- 6. Windows service ------------------------------------------------------
Step "Installing service $ServiceName"
New-Item -ItemType Directory -Force $logs | Out-Null
$uvicorn = Join-Path $backend '.venv\Scripts\uvicorn.exe'
if (& $nssm status $ServiceName 2>$null) { & $nssm stop $ServiceName | Out-Null; & $nssm remove $ServiceName confirm | Out-Null }
& $nssm install $ServiceName $uvicorn "app.main:app --host 0.0.0.0 --port $Port" | Out-Null
& $nssm set $ServiceName AppDirectory $backend | Out-Null       # required: the app reads .env from its working directory
& $nssm set $ServiceName AppStdout (Join-Path $logs 'backend.log') | Out-Null
& $nssm set $ServiceName AppStderr (Join-Path $logs 'backend.err.log') | Out-Null
& $nssm set $ServiceName AppRotateFiles 1 | Out-Null
& $nssm set $ServiceName AppRotateBytes 10485760 | Out-Null
& $nssm set $ServiceName Start SERVICE_AUTO_START | Out-Null
& $nssm start $ServiceName | Out-Null

# --- 7. Firewall and health check -------------------------------------------
Step "Opening TCP $Port ($AllowedRemoteAddresses)"
Get-NetFirewallRule -DisplayName 'HFMG Help Desk backend' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'HFMG Help Desk backend' -Direction Inbound -Protocol TCP -LocalPort $Port `
    -RemoteAddress $AllowedRemoteAddresses -Action Allow | Out-Null

Step 'Health check'
$ok = $false
for ($i = 0; $i -lt 20 -and -not $ok; $i++) {
    Start-Sleep -Seconds 2
    try { $ok = (Invoke-WebRequest "http://localhost:$Port/api/v1/health" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 } catch { }
}
if (-not $ok) { Fail "Backend did not become healthy. See $logs\backend.err.log" }

$auth = if ($token) {
    try { (Invoke-WebRequest "http://localhost:$Port/api/v1/voice/sip/start" -Method Post -UseBasicParsing `
        -Headers @{ Authorization = "Bearer $token" } -ContentType 'application/json' `
        -Body '{"call_id":"install-check"}').StatusCode } catch { $_.Exception.Response.StatusCode.value__ }
} else { 'no token' }

Write-Host "`nDone." -ForegroundColor Green
Write-Host "  Dashboard:          http://${ServerAddress}:$Port/"
Write-Host "  SIP endpoint check: $auth (200 expected)"
Write-Host "  Gateway settings:   Backend.BaseUrl = http://${ServerAddress}:$Port  (or http://127.0.0.1:$Port if on this machine)"
Write-Host "                      Backend.GatewayToken = $token"
Write-Host '  Keep that token secret; it is stored in backend\.env.'
