# Keep Gmail on Render Free

This system keeps its existing Gmail sender, branded templates, OTP verification, password resets, and assessment notifications. Render Free blocks outbound SMTP ports 25, 465, and 587. A Gmail App Password cannot fix that network restriction. The new `backend.gmail_api.EmailBackend` uses the same Gmail account through Google's HTTPS API instead.

## Authorize the existing sender once

1. In [Google Cloud Console](https://console.cloud.google.com/), create or select a project and enable **Gmail API** under APIs & Services.
2. Configure its Google Auth Platform/OAuth consent screen. Authorize the Gmail account that currently sends this application's emails. For an External app in Testing, add that account as a test user. Google expires refresh tokens issued for these Gmail scopes in Testing after seven days; for continuous production use, move to an appropriate production/internal configuration and follow Google's verification requirements before issuing the long-lived credentials.
3. Create an OAuth client of type **Web application**. Add this exact authorized redirect URI: `https://developers.google.com/oauthplayground`.
4. Open [Google OAuth Playground](https://developers.google.com/oauthplayground/). In its settings, enable **Use your own OAuth credentials** and enter that project's client ID and client secret. This is necessary: tokens using Playground's default credentials are automatically revoked after 24 hours.
5. In Step 1, enter the send-only scope `https://www.googleapis.com/auth/gmail.send`, then authorize using the **existing sender Gmail account**. In Step 2, exchange the authorization code for tokens. Save the refresh token privately along with the client ID and secret. Do not place them in React, GitHub source, screenshots, or chat.

## Configure Render

`backend-main/.env` is the single local configuration file for both applications. It now includes the three Gmail OAuth fields. Fill them there after authorizing Gmail, and change its existing `EMAIL_BACKEND` setting to `backend.gmail_api.EmailBackend` when ready. Do not add a second definition of the same setting. The `.env` stays private and is excluded from Git, so pushing source alone does not copy its values to Render.

In the backend service's Environment settings, set:

```dotenv
EMAIL_BACKEND=backend.gmail_api.EmailBackend
GMAIL_OAUTH_CLIENT_ID=your-own-google-oauth-client-id
GMAIL_OAUTH_CLIENT_SECRET=your-own-google-oauth-client-secret
GMAIL_OAUTH_REFRESH_TOKEN=refresh-token-for-the-existing-gmail-sender
DEFAULT_FROM_EMAIL=your-existing-sender@gmail.com
FRONTEND_BASE_URL=https://aspin-tunay-at-tapat.netlify.app
EMAIL_TIMEOUT=15
```

`DEFAULT_FROM_EMAIL` must match the OAuth-authorized Gmail account or a Gmail-approved sending alias. The existing `EMAIL_HOST_USER` can stay set to that same account; leaving `DEFAULT_FROM_EMAIL` blank then retains that sender. SMTP credentials can remain for local SMTP use, but they are not used by the HTTPS backend. No replacement email provider or frontend secret is introduced.

Push the updated `backend-main` source to its GitHub repository and deploy it to Render. Setting an environment variable alone cannot install the new backend module. Run existing migrations as part of deployment if not already applied.

To validate configuration and refresh OAuth without sending any email, run this command in an environment with those variables and the updated source:

```powershell
python manage.py check_assessment_email --connect
```

This check does not send messages or drain the assessment queue. Successful OAuth refresh alone does not prove the Gmail scope, sender alias, or inbox delivery. Review real delivery in Gmail and the application's queue after requesting the normal email flow.

## Assessment reminders still need a scheduler

OTP and reset emails send during their requests. Assessment confirmations send after scheduling commits. Reminders need the existing periodic worker:

```powershell
python manage.py process_assessment_emails --loop --interval 30
```

Run that worker on a supervised process or use a scheduler to run the command periodically with the same backend environment and production database. A Render Free web service can sleep and is not a reliable always-running reminder worker. No database migration or duplicate scheduler is introduced by the mail backend change.

After OAuth works, `process_assessment_emails --retry-connections` can safely reschedule notifications that failed before submission. Do not automatically retry `failed`/`sending` jobs: a network interruption during Gmail submission can leave delivery uncertain. Check Gmail before using the existing `--retry-email EMAIL_ID` command.

## Other errors from the browser

- A `503` from `/api/password/forgot/` means email delivery is unavailable. If the Render variables above have not been configured and the updated source deployed, do that first. Pushing code alone does not authorize Gmail. Run the connection check and review Render logs for missing setting names or sanitized OAuth errors.
- A `429` means the email request limit was reached. Even a failed email attempt reserves the 60-second cooldown; there is also a five-attempt hourly limit per recipient and a separate 30-request hourly limit per client IP. Wait for the response's `Retry-After` delay. The updated forgot-password page displays a countdown and blocks repeat submissions. Redeploy `frontend-master` to use it. Waiting clears the rate limit; it does not repair Gmail configuration.
- A `401` from `/api/login/` means backend authentication rejected the username/password. Use the registered username, not the account email. Registration creates the login account only after successful OTP verification. An account in local SQLite is not necessarily present in the deployed database.
- A `401` for Netlify's `/manifest.json` comes from Netlify's access settings and is separate from Gmail and Django authentication. Check site access or authenticate through Netlify's access screen when protection is intentional.
- Video autoplay `AbortError` messages do not prevent account email delivery.

References: [Render Free SMTP restriction](https://render.com/docs/free#other-limitations), [Gmail API sending](https://developers.google.com/workspace/gmail/api/guides/sending), [Google offline OAuth](https://developers.google.com/identity/protocols/oauth2/web-server#offline), [Refresh-token expiration](https://developers.google.com/identity/protocols/oauth2#expiration).
