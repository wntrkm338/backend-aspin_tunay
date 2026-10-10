from datetime import datetime

from django.db import migrations
from django.utils import timezone


def _assessment_schedule_datetime(assessment):
    if not assessment.date or not assessment.time:
        return None

    try:
        time_value = datetime.strptime(str(assessment.time)[:5], '%H:%M').time()
    except ValueError:
        return None

    scheduled_at = datetime.combine(assessment.date, time_value)
    if timezone.is_naive(scheduled_at):
        scheduled_at = timezone.make_aware(scheduled_at, timezone.get_current_timezone())
    return scheduled_at


def _sync_application(application, assessment):
    update_fields = []

    schedule = _assessment_schedule_datetime(assessment)
    if schedule:
        application.assessment_1_schedule = schedule
        update_fields.append('assessment_1_schedule')

    if assessment.link:
        application.assessment_1_link = assessment.link
        update_fields.append('assessment_1_link')

    status = assessment.status or ''
    if status in {'Scheduled', 'Pending Link', 'Accepted', 'Pending'} or 'Scheduled' in status:
        application.status = 'Assessment 1 Scheduled'
        application.assessment_1_status = 'scheduled'
        update_fields.extend(['status', 'assessment_1_status'])
    elif status in {'Reschedule Approved', 'Assessment 1 Rescheduled'}:
        application.status = 'Assessment 1 Rescheduled'
        application.assessment_1_status = 'rescheduled'
        update_fields.extend(['status', 'assessment_1_status'])

    if update_fields:
        application.save(update_fields=list(dict.fromkeys(update_fields)))


def backfill_remote_assessment_app_id(apps, schema_editor):
    Animal = apps.get_model('admin_api', 'Animal')
    AdoptionApplication = apps.get_model('admin_api', 'AdoptionApplication')
    RemoteAssessment = apps.get_model('admin_api', 'RemoteAssessment')

    assessments = RemoteAssessment.objects.filter(appId__in=[None, ''])
    for assessment in assessments:
        name = (assessment.adopterName or '').strip()
        if not name:
            continue

        candidates = AdoptionApplication.objects.filter(fullName__iexact=name).order_by('-submittedAt', '-id')

        animal_name = (assessment.animalName or '').strip()
        if animal_name:
            animal_ids = list(
                Animal.objects
                .filter(name__iexact=animal_name)
                .values_list('id', flat=True)
            )
            if animal_ids:
                candidates = candidates.filter(animalId__in=[str(animal_id) for animal_id in animal_ids])

        application = candidates.first()
        if not application:
            continue

        assessment.appId = str(application.id)
        assessment.save(update_fields=['appId'])
        _sync_application(application, assessment)


class Migration(migrations.Migration):

    dependencies = [
        ('admin_api', '0021_backfill_adoption_application_submitted_by'),
    ]

    operations = [
        migrations.RunPython(backfill_remote_assessment_app_id, migrations.RunPython.noop),
    ]
