# Server deployment (Windows)

Everything that can be prepared ahead of time is already in this folder.

| Item | Purpose |
|---|---|
| `install-server.ps1` | One-shot install: venv, database, `.env`, migrations, seed, Windows service, firewall, health check |
| `frontend-dist/` | The pre-built dashboard (built with `VITE_API_BASE_URL=/api/v1`). The backend serves it at `/`, so the server needs no Node.js |

## Server prerequisites
Python 3.11 or 3.12 (not 3.13+), PostgreSQL 16, Git, and [NSSM](https://nssm.cc) on `PATH`.

## Steps
```powershell
git clone <repo-url> C:\hfmg ; cd C:\hfmg ; git checkout feature/sip-only
# elevated PowerShell:
.\deploy\install-server.ps1 -PgAdminPassword '<postgres pw>' -DbPassword '<new db pw>' `
    -OpenAiApiKey '<key>' -ServerAddress '<server ip>'
```
The script prints the generated `VOICE_SIP_GATEWAY_TOKEN`. Put it in the gateway's `appsettings.json` as `Backend.GatewayToken`, set `Backend.BaseUrl` to `http://<server ip>:8001` (or `http://127.0.0.1:8001` on the same machine), and restart the gateway.

Then review the `EMAIL_*` settings in `backend\.env` and follow the live-call checklist in `SIP_SETUP.md`.

## Updating
```powershell
git pull ; cd backend
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\alembic upgrade head
nssm restart HFMG-Backend
```

## Rebuilding the dashboard
After changing `frontend/`, on a machine with Node 20+:
```powershell
cd frontend ; $env:VITE_API_BASE_URL='/api/v1' ; npm ci ; npm run build
Remove-Item -Recurse ..\deploy\frontend-dist ; Copy-Item -Recurse dist ..\deploy\frontend-dist
```

## Updating a server that is a plain copy (no git)
Use this when the app folder on the server has no `.git` (e.g. `C:\HorizonApps\HFMG AI Helpdesk Backend`).
1. Copy `hfmg-sip-release.zip` to the server and extract it to a **separate** folder, e.g. `C:\HorizonApps\release`.
2. From an elevated PowerShell in that folder:
   `.\deploy\update-server.ps1 -ServiceName <your service name>`
   (add `-ServeDashboard` to have the backend serve the bundled dashboard).
The script backs up the database, keeps `.env` and `.venv`, migrates, restarts and health-checks.
