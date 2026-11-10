# WORKCODE — frontend & API verification

Date: 2026-11-06 · Backend: FastAPI on PostgreSQL · Frontend: React build served by FastAPI

## Backend
- Total `/api` routes: **151**
- Simple GET sweep: **35 / 44 returned 2xx**
- Non-2xx items are intentional guards, not defects:
  - `/api/timer/current|sessions|attendance` → **403** for admin ("Admin accounts do not use the work timer")
  - `/api/monitoring/me` → **403** for admin (self-view is employee-only)
  - `/api/meetings/google/auth-url` → **503** (Google OAuth not configured)
- Parameterised endpoints verified **200**: project detail, project files, versions, user detail, monitoring employee detail, tasks.
- Worker-scoped endpoints verified **200**: timer current/sessions/attendance, chat rooms/contacts, monitoring/me, notifications.

## Frontend (Playwright, 18 route loads)
| Role | Routes | Result |
|---|---|---|
| Admin | /admin /users /tasks /reviews /monitoring /reports /meetings /recordings /chat /profile | all render, 0 blank, 0 console errors |
| Employee | /onroll /tasks /workspace /reviews /meetings /recordings /chat /profile | all render, 0 blank, 0 console errors |

Rendered text length ranged 417–1783 characters, i.e. real content, not empty shells.

## Fixes applied during this check
1. `app/api/monitoring.py` — `IST` timezone constant was missing, which made `/monitoring/reports` and `/monitoring/export.xlsx` return **500**. Added `IST = timezone(timedelta(hours=5, minutes=30))`.

## Known configuration-dependent endpoints
- Google OAuth (`/meetings/google/*`) needs `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`; returns 503 until set.
