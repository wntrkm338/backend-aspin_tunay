# Admin data backups

Open **Settings**, directly above **Logout** in the admin sidebar.

- **JSON** exports a versioned document containing database records.
- **Excel (.xlsx)** exports the same records, with one worksheet per model and a `Backup` worksheet mapping sheet names to models. Cells use JSON literals so IDs, decimals, nulls, booleans, and relationships survive a round trip. Text cells include double quotes. Formulas are rejected on import.
- **ZIP** contains `records.json` and the complete `media/` upload tree, including images, receipts, and other uploaded files.

Exports cover all models in `accounts`, `admin_api`, `user_api`, and `chatbot`, plus users, groups, permissions, and content types. Sessions, API tokens, environment secrets, static assets, and source code are excluded. Account exports include password hashes and personal data; store backups privately.

## Import behavior

Use backups from this application's current schema. Imports add new primary keys and update matching primary keys, including their many-to-many relationships. They do not delete records or uploads absent from the backup. Matching ZIP upload paths are replaced. This is a merge restore, not a database reset or a migration between unrelated installations with conflicting IDs.

1. Select a JSON, XLSX, or ZIP backup and click **Validate & preview**.
2. Review per-model create/update counts, sample records, file counts, and warnings for missing referenced uploads. The profile placeholders `default.jpg` and `default_cover.jpg` are excluded from missing-upload warnings; actual uploaded avatars and covers are still checked.
3. Check the confirmation box and click **Confirm import**. Cancel or select a different file to discard the preview.

Validation checks schema, field types and lengths, relationships, unique database constraints, archive integrity, path traversal, duplicate archive paths, symlinks, encryption, and Excel formulas. Errors identify records/fields where possible. The signed-in administrator's username, password hash, active status, and privileges must remain unchanged to avoid account lockout.

The preview performs database writes in a transaction that is rolled back, without installing files. Confirmation requires a signed, 15-minute preview token bound to the admin, uploaded bytes, and current records/upload contents. A changed database or replaced upload requires a fresh preview. Confirmation validates again, uses a database transaction, stages new files, and restores previous file contents if database or file installation fails. As with any filesystem/database operation, process termination or machine failure during file installation is not covered by transactional rollback; perform restores during a quiet period.

Default import limits in `admin_api/data_backup.py`: 100 MB uploaded, 50 MB JSON records, 512 MB expanded archives, 10,000 archive entries, and 100,000 records. Larger exports remain downloadable but require adjusted server limits to restore through Settings. Reverse-proxy upload/time limits must also accommodate the backup size.

The backend uses the configured default database and local `MEDIA_ROOT`. It does not back up external payment services or blockchain state and does not trigger payment, email, or notification view actions during import.

## Dependencies and checks

Install the backend requirements, including `openpyxl==3.1.5` and `defusedxml==0.7.1`. No database migration is required.

From `backend-main`:

```text
python manage.py test admin_api.test_data_backup --settings=backend.test_settings --noinput
```

`backend.test_settings` always uses an isolated in-memory SQLite database, regardless of the database URLs in `.env`.

From `frontend-master`:

```text
npm test -- --watchAll=false --runInBand AdminSettings.test.js
npm run build
```

API routes require an authenticated staff administrator:

- `GET /admin_api/settings/export/?file_format=json|xlsx|zip`
- `POST /admin_api/settings/import/preview/` — multipart `file`
- `POST /admin_api/settings/import/confirm/` — multipart `file`, `preview_token`, and `confirm=true`
