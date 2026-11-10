# Postman

1. **Import** both files: `WORKCODE.postman_collection.json` and
   `WORKCODE.postman_environment.json`.
2. Select the **WORKCODE · Local** environment.
3. Start the backend (`uvicorn app.main:app --port 8000`).
4. Open **01 · Authentication → Login as admin**, replace `YOUR_ADMIN_PASSWORD` with a
   password printed by `scripts/init_admins.py`, then **Send**.
   The test script stores `ACCESS_TOKEN`, `REFRESH_TOKEN` and `USER_ID` automatically.
5. Create an intern account (03 · Users → Create intern) so `NEW_USER_ID` is set, then log
   in once as that intern and paste the token into `INTERN_TOKEN` — this powers the
   permission tests (403 cases).
6. Run the whole collection with **Collection Runner** in folder order 01 → 10.

## What the tests assert

| Group | Assertions |
|---|---|
| Auth | 200 + tokens + `redirect_to`; wrong password → 401; no token → 401; refresh rotates |
| Admin | dashboard shape; non-admin → 403 on admin routes |
| Users | 201 create, 409 duplicate, conversion returns `developer_onroll` |
| Tasks | `WC-####` code generated, 422 on empty title |
| Projects | version bump on save, path traversal rejected (422) |
| Workspace | `print(2+2)` exits 0 and prints `4` |
| Submissions | 201 + review decision recorded |
| Timer | start/heartbeat/pause/resume/stop; admin → 403 |
| Recordings | consent required (422), then 201 |
| Chat | room + message persisted |
| Meetings | never returns a fabricated Meet URL |
| Reports | CSV content-type and header row |

Environment variables are chained: `PROJECT_ID`, `TASK_ID`, `SUBMISSION_ID`, `ROOM_ID`,
`MEETING_ID`, `RECORDING_ID` are captured by the test scripts of earlier requests, so run
the folders in order.
