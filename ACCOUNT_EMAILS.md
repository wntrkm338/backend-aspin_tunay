# Account emails

Registration OTPs and password-reset links use the same `EMAIL_*` settings and Gmail sender as Remote Assessment. They send immediately during the request; the assessment reminder worker is not needed for account emails. Delivery errors return HTTP 503 with a safe error message. There is no console-email fallback or reset-token response, including with `DEBUG=True`.

For Render Free, keep the existing Gmail account and use its HTTPS API because Free blocks normal SMTP ports. Configure `EMAIL_BACKEND=backend.gmail_api.EmailBackend` and the Gmail OAuth credentials following [Gmail on Render setup](GMAIL_RENDER_SETUP.md). Gmail SMTP and App Passwords remain available for environments permitting SMTP connections.

All outgoing account and assessment emails include a branded HTML design and a plain-text alternative. The shared design lives in `accounts/templates/emails/notification.html`; verification emails highlight the code, and reset/assessment emails include action buttons.

To change the sending account, update `EMAIL_HOST_USER` and its matching `EMAIL_HOST_PASSWORD` in `backend-main/.env`. Leave `DEFAULT_FROM_EMAIL` blank to follow that account automatically, then restart the backend and any assessment email worker. For Gmail, use the new account's Google App Password.

## Registration

1. `POST /api/register/` with `username`, `email`, and `password` validates the details and sends an OTP. HTTP 202 returns a random `challenge` identifier, the email, and expiry/resend intervals. It does not create a login account.
2. `POST /api/register/verify/` with `challenge` and the six-digit `code` creates the account only after successful verification. The challenge is then deleted and cannot be reused.
3. `POST /api/register/resend/` with `challenge` sends a replacement code. Earlier codes stop working. Codes expire after 10 minutes; five incorrect attempts require a new code. Resends have a 60-second cooldown and a five-emails-per-hour limit per address. Requests are also limited per IP.

Only hashed passwords and OTPs are stored in pending registrations. Pending verification and email rate-limit records are excluded from application backups. Existing accounts are unchanged.

## Password reset

`POST /api/password/forgot/` accepts `identifier` (account email or username) or `email`. Instructions go to the active account's saved email. The response does not disclose whether the account exists. Missing-email and inactive accounts do not receive reset links. Reset emails have a 60-second cooldown and a five-emails-per-hour limit per address.

The emailed link opens `/reset-password` on `FRONTEND_BASE_URL`. Set this backend environment variable to the public frontend URL when deploying. The link expires after one hour and stops working after a successful reset. `POST /api/password/reset/` takes `uid`, `token`, `password`, and `confirm_password`.

Both the Login registration tab and `/register` use the same verification interface. Account pages use `REACT_APP_API_BASE_URL` for backend routing.

Apply the database migration when deploying:

```powershell
python manage.py migrate
```

Check the shared SMTP credentials without sending mail:

```powershell
python manage.py check_assessment_email --connect
```

Run isolated tests without sending real emails:

```powershell
python manage.py test accounts.tests --settings=backend.test_settings
```
