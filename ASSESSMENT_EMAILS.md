# Remote Assessment emails through Gmail

Assessment confirmations, reschedules, cancellations, and reminders use Django's SMTP backend. The local backend environment is set to Gmail's `smtp.gmail.com`, port `587`, with STARTTLS. The sender defaults to the Gmail account used to authenticate.

For Render Free, normal SMTP ports are blocked. Use the same Gmail account through the new HTTPS backend following [Gmail on Render setup](GMAIL_RENDER_SETUP.md). The templates, assessment queue, recipient selection, and reminder worker are shared by both delivery methods.

## Complete the Gmail credentials

1. Enable 2-Step Verification on the sending Google account, then create an [App Password](https://support.google.com/mail/answer/185833?hl=en). Use this password for SMTP, not the normal Google account password. Some managed accounts restrict App Passwords.
2. Edit `backend-main/.env` locally:

   ```dotenv
   EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
   EMAIL_HOST=smtp.gmail.com
   EMAIL_PORT=587
   EMAIL_USE_TLS=True
   EMAIL_USE_SSL=False
   EMAIL_HOST_USER=your-shelter-account@gmail.com
   EMAIL_HOST_PASSWORD=your-google-app-password
   DEFAULT_FROM_EMAIL=
   ```

   Leave `DEFAULT_FROM_EMAIL` blank to send from `EMAIL_HOST_USER`. Only use another sender if it is an authorized Gmail alias. Spaces in Google's displayed App Password are removed automatically. Credentials belong only on the backend and must not be put in React or committed.
3. Set `FRONTEND_BASE_URL` to the public frontend origin so the account link in emails works for adopters.
4. From `backend-main`, verify the credentials without sending email:

   ```powershell
   python manage.py check_assessment_email --connect
   ```

5. Restart Django and any existing email worker after changing environment settings.

Google documents the SMTP settings in [Send email from an app](https://support.google.com/a/answer/176600?hl=en).

## Keep reminders running

In admin Adoption Applications, **Schedule Post-Adoption Check** and the post-adoption timeline shortcut open Remote Assessment for that exact application and phase. An existing follow-up is edited or rebooked rather than duplicated. For older applications missing a follow-up, the form suggests one calendar month after their first assessment, preserving its time and time zone. The admin must save before a booking or notification is created. If no first assessment exists, the form instead offers to schedule both appointments.

Run a separate, persistent process alongside Django:

```powershell
python manage.py process_assessment_emails --loop --interval 30
```

For production, use a process supervisor or a scheduled task to keep that command running. Alternatively, run `python manage.py process_assessment_emails` once per minute. Django's web server alone cannot wake up to send reminders when nobody is visiting the site.

Scheduling or changing an assessment attempts to send the current notification after the database transaction commits. The worker sends due reminders 24 hours and 1 hour before each appointment. All confirmations, updates, cancellations, and reminders use the applicant email saved on the adoption application (`AdoptionApplication.email`), including when it differs from their account email. The current application email is re-read before delivery, so corrections also apply to queued messages. Missing or invalid application emails must be corrected before scheduling. Messages include both schedules, the time zone, instructions, and access links. The admin assessment card displays the recipient as "Send to".

Cancelling an assessment retains its record and notification history and stops that appointment's reminders. Creating a new booking for that cancelled phase reopens the same record with a new revision and fresh reminders. Active, completed, or rejected assessments still cannot be duplicated. Booking the first assessment again restores a cancelled automatic follow-up to one calendar month later, using the first assessment's time and time zone. It preserves a follow-up that an admin rescheduled separately. Repeating the same booking does not create duplicate appointments or notifications.

Missing Gmail credentials leave messages queued and the admin page reports that delivery is not configured. Connection/authentication failures before submission are retried with backoff. The queue deduplicates schedule events and reminders, skips stale revisions, and avoids automatically retrying a message whose SMTP delivery outcome is uncertain. SMTP cannot guarantee exactly-once delivery after a network failure.

Review `AssessmentEmail` in Django admin for pending, sent, failed, or stuck `sending` messages. Only after checking that an uncertain message was not delivered should you explicitly retry it:

```powershell
python manage.py process_assessment_emails --retry-email EMAIL_ID
```

The SMTP check command never sends a test message or drains queued notifications.

After correcting SMTP credentials and restarting the worker, safely retry notifications that failed before submission with `python manage.py process_assessment_emails --retry-connections`. This does not retry sent messages or messages with an uncertain delivery outcome.
