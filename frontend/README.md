# HFMG AI Help Desk — Frontend (MVP)

React + Vite + TypeScript dashboard. No login screen — matches the no-auth MVP backend.

## Setup

```bash
npm install
cp .env.example .env   # points at http://localhost:8000/api/v1 by default
npm run dev            # http://localhost:5173
```

Requires the backend (`../backend`) running separately — see its README.

## Pages

- `/` — ticket list, filterable by status
- `/tickets/new` — ticket creation form
- `/tickets/:ticketId` — ticket detail: full description, AI summary (or a message explaining why it's absent), status-change control

## Build

```bash
npm run build   # type-checks then builds to dist/
```
