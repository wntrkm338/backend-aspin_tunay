from dataclasses import dataclass
from urllib.parse import unquote, urlparse

from django.db import transaction
from rest_framework.exceptions import ValidationError

from .models import AdoptionApplication, RemoteAssessment


DONE_ASSESSMENT_STATUS = 'Done'

CALL_ENDED_EVENT_NAMES = {
    'call_ended',
    'call-ended',
    'call.ended',
    'meeting_ended',
    'meeting-ended',
    'meeting.ended',
    'conference_ended',
    'conference-ended',
    'conference.ended',
    'conference.terminated',
    'video_conference_left',
    'videoconferenceleft',
    'ready_to_close',
    'readytoclose',
    'hangup',
    'ended',
}

TERMINAL_ASSESSMENT_STATUSES = {
    DONE_ASSESSMENT_STATUS,
    'Assessment 1 Done',
    'Assessment 1 Passed',
    'Assessment 1 Rejected',
    'Monitoring Passed',
    'Post-Adoption Rejected',
    'Reschedule Declined',
    'Declined',
    'Cancelled',
}


@dataclass(frozen=True)
class CallEndedResult:
    assessment: RemoteAssessment
    previous_status: str
    updated: bool


def assessment_schedule_datetime(assessment):
    if not assessment.date or not assessment.time:
        return None

    try:
        from .scheduling import scheduled_datetime
        return scheduled_datetime(assessment.date, str(assessment.time)[:5], assessment.time_zone)
    except ValidationError:
        return None


def sync_application_from_assessment(assessment):
    if not assessment.appId:
        return

    application = AdoptionApplication.objects.filter(id=assessment.appId).first()
    if not application:
        return

    status = assessment.status or ''
    update_fields = []
    application_is_post_adoption = application.status in {
        'Verified Adopted',
        'Post-Adoption Scheduled',
        'Post-Adoption Done',
        'Monitoring Passed',
        'Awaiting Feed Post',
        'Completed',
    }
    is_post_adoption_phase = assessment.phase == 'post_adoption'
    if assessment.phase == 'legacy':
        return  # Historical duplicates must not overwrite either current schedule.

    schedule = assessment_schedule_datetime(assessment)
    if schedule:
        if is_post_adoption_phase:
            application.assessment_2_schedule = schedule
            update_fields.append('assessment_2_schedule')
        else:
            application.assessment_1_schedule = schedule
            update_fields.append('assessment_1_schedule')

    if assessment.link:
        if is_post_adoption_phase:
            application.assessment_2_link = assessment.link
            update_fields.append('assessment_2_link')
        else:
            application.assessment_1_link = assessment.link
            update_fields.append('assessment_1_link')

    if status in {'Assessment 1 Done', DONE_ASSESSMENT_STATUS}:
        field = 'assessment_2_status' if is_post_adoption_phase else 'assessment_1_status'
        # A finished call is ready for review; it is not an approval decision.
        if getattr(application, field) in {'waiting', 'scheduled', 'rescheduled', 'done'}:
            setattr(application, field, 'done')
            update_fields.append(field)
        if is_post_adoption_phase and application.status in {'Verified Adopted', 'Post-Adoption Scheduled'}:
            application.status = 'Post-Adoption Done'
            update_fields.append('status')
        if update_fields:
            application.save(update_fields=list(dict.fromkeys(update_fields)))
        return

    if status in {'Cancelled', 'Declined', 'Reschedule Declined'}:
        field = 'assessment_2_status' if is_post_adoption_phase else 'assessment_1_status'
        setattr(application, field, 'cancelled')
        update_fields.append(field)
        if is_post_adoption_phase and application.status == 'Post-Adoption Scheduled':
            application.status = 'Verified Adopted'
            update_fields.append('status')
        elif not is_post_adoption_phase and application.status in {'Assessment 1 Scheduled', 'Assessment 1 Rescheduled'}:
            application.status = 'Under Review'
            update_fields.append('status')
        application.save(update_fields=list(dict.fromkeys(update_fields)))
        return

    if is_post_adoption_phase:
        if status in {'Scheduled', 'Pending Link', 'Accepted', 'Pending'} or 'Scheduled' in status:
            if application.status in {'Verified Adopted', 'Post-Adoption Scheduled', 'Post-Adoption Done'}:
                application.status = 'Post-Adoption Scheduled'
            application.assessment_2_status = 'scheduled'
            update_fields.extend(['status', 'assessment_2_status'])
        elif status in {'Reschedule Approved', 'Assessment 1 Rescheduled'}:
            if application.status in {'Verified Adopted', 'Post-Adoption Scheduled', 'Post-Adoption Done'}:
                application.status = 'Post-Adoption Scheduled'
            application.assessment_2_status = 'rescheduled'
            update_fields.extend(['status', 'assessment_2_status'])
        elif 'Passed' in status or status == 'Monitoring Passed':
            application.status = 'Monitoring Passed'
            application.assessment_2_status = 'passed'
            update_fields.extend(['status', 'assessment_2_status'])
        elif 'Rejected' in status:
            application.assessment_2_status = 'rejected'
            update_fields.append('assessment_2_status')

        if update_fields:
            application.save(update_fields=list(dict.fromkeys(update_fields)))
        return

    if status in {'Scheduled', 'Pending Link', 'Accepted', 'Pending'} or 'Scheduled' in status:
        if not application_is_post_adoption:
            application.status = 'Assessment 1 Scheduled'
        application.assessment_1_status = 'scheduled'
        update_fields.extend(['status', 'assessment_1_status'])
    elif status in {'Reschedule Approved', 'Assessment 1 Rescheduled'}:
        if not application_is_post_adoption:
            application.status = 'Assessment 1 Rescheduled'
        application.assessment_1_status = 'rescheduled'
        update_fields.extend(['status', 'assessment_1_status'])
    elif 'Passed' in status:
        application.status = 'Assessment 1 Passed'
        application.assessment_1_status = 'passed'
        update_fields.extend(['status', 'assessment_1_status'])
    elif 'Rejected' in status:
        application.status = 'Assessment 1 Rejected'
        application.assessment_1_status = 'rejected'
        update_fields.extend(['status', 'assessment_1_status'])

    if update_fields:
        application.save(update_fields=list(dict.fromkeys(update_fields)))


def is_call_ended_event(payload):
    event_found = False

    for item in _iter_payload_dicts(payload):
        for key in ('event', 'event_type', 'eventName', 'type', 'action', 'status', 'name'):
            value = item.get(key)
            if isinstance(value, (dict, list)) or value in (None, ''):
                continue

            event_found = True
            if _is_call_ended_value(value):
                return True

    return not event_found


def resolve_assessment_from_call_event(payload):
    assessment_id = _first_payload_value(
        payload,
        (
            'assessment_id',
            'remote_assessment_id',
            'remoteAssessmentId',
            'assessmentId',
        ),
    )

    if assessment_id:
        assessment = RemoteAssessment.objects.filter(id=assessment_id).first()
        if assessment:
            return assessment

    room = _first_payload_value(
        payload,
        (
            'jitsiRoom',
            'jitsi_room',
            'room',
            'room_name',
            'roomName',
            'conference',
            'conference_name',
            'conferenceName',
            'meeting_id',
            'meetingId',
            'meeting_name',
            'meetingName',
        ),
    )
    link = _first_payload_value(
        payload,
        (
            'link',
            'url',
            'meeting_url',
            'meetingUrl',
            'call_url',
            'callUrl',
            'conference_url',
            'conferenceUrl',
        ),
    )

    room = room or _room_from_meeting_link(link)
    if room:
        assessment = RemoteAssessment.objects.filter(jitsiRoom__iexact=room).first()
        if assessment:
            return assessment

        assessment = RemoteAssessment.objects.filter(link__icontains=room).order_by('-created_at').first()
        if assessment:
            return assessment

    if link:
        assessment = RemoteAssessment.objects.filter(link=link).first()
        if assessment:
            return assessment

    return None


@transaction.atomic
def mark_assessment_done_from_call_event(assessment):
    locked_assessment = RemoteAssessment.objects.select_for_update().get(id=assessment.id)
    previous_status = locked_assessment.status or ''

    if previous_status not in TERMINAL_ASSESSMENT_STATUSES:
        locked_assessment.status = DONE_ASSESSMENT_STATUS
        locked_assessment.save(update_fields=['status'])
        locked_assessment.email_jobs.filter(kind='reminder', status='pending').update(status='cancelled')

    sync_application_from_assessment(locked_assessment)
    updated = locked_assessment.status != previous_status

    if updated:
        from user_api.notifications import notify_assessment_call_done

        notify_assessment_call_done(locked_assessment)

    return CallEndedResult(
        assessment=locked_assessment,
        previous_status=previous_status,
        updated=updated,
    )


def _is_call_ended_value(value):
    normalized = str(value).strip().lower().replace(' ', '_')
    compact = normalized.replace('_', '').replace('-', '').replace('.', '')

    return normalized in CALL_ENDED_EVENT_NAMES or compact in {
        event_name.replace('_', '').replace('-', '').replace('.', '')
        for event_name in CALL_ENDED_EVENT_NAMES
    }


def _iter_payload_dicts(payload):
    if not isinstance(payload, dict):
        return

    yield payload

    for value in payload.values():
        if isinstance(value, dict):
            yield from _iter_payload_dicts(value)


def _first_payload_value(payload, keys):
    key_set = {key.lower() for key in keys}

    for item in _iter_payload_dicts(payload):
        for key, value in item.items():
            if key.lower() not in key_set:
                continue
            if isinstance(value, (dict, list)) or value in (None, ''):
                continue

            text = str(value).strip()
            if text:
                return text

    return None


def _room_from_meeting_link(link):
    if not link:
        return None

    cleaned_link = unquote(str(link).strip().strip('"'))
    parsed_link = urlparse(cleaned_link)
    path = parsed_link.path.strip('/')

    if not path:
        return None

    return path.split('/')[-1] or None
