from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import AdoptionApplication, Animal, RemoteAssessment
from .services import sync_application_from_assessment


class RemoteAssessmentCallEndedWebhookTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_user = User.objects.create_user(
            username='admin',
            password='SafePass123!',
            is_staff=True,
        )
        self.adopter = User.objects.create_user(
            username='adopter',
            email='adopter@example.com',
            password='SafePass123!',
        )
        self.animal = Animal.objects.create(
            id='A200',
            name='Milo',
            species='Dog',
            status='Ready for Adoption',
            availability='Available',
        )
        self.application = AdoptionApplication.objects.create(
            submitted_by=self.adopter,
            animalId=self.animal.id,
            status='Assessment 1 Scheduled',
            assessment_1_status='scheduled',
            fullName='Applicant Two',
            age=28,
            civilStatus='Single',
            address='Lucena City',
            contact='09171234567',
            email=self.adopter.email,
            validIdType='Passport',
            residenceType='Owned',
            householdAgreement='Yes',
            dogPlace='Inside',
            indoorAllowed='Yes',
            whyAdopt='Companionship',
            lifetimeCommitment='Yes',
            agreedToTerms=True,
        )

    def create_assessment(self, assessment_id='RA-CALL-1', status='Accepted'):
        return RemoteAssessment.objects.create(
            id=assessment_id,
            appId=str(self.application.id),
            adopterName='Applicant Two',
            animalName='Milo',
            date=date(2026, 6, 20),
            time='09:00',
            status=status,
            link=f'https://meet.jit.si/ShelterCall_{assessment_id}',
            jitsiRoom=f'ShelterCall_{assessment_id}',
        )

    def test_admin_call_ended_event_marks_assessment_done(self):
        assessment = self.create_assessment()
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.post(
            '/api/assessments/call-ended/',
            {
                'event': 'call_ended',
                'assessment_id': assessment.id,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['updated'])
        self.assertEqual(response.data['assessment']['status'], 'Done')

        assessment.refresh_from_db()
        self.assertEqual(assessment.status, 'Done')

    @override_settings(CALL_ENDED_WEBHOOK_SECRET='super-secret')
    def test_call_ended_webhook_can_resolve_assessment_by_room(self):
        assessment = self.create_assessment(assessment_id='RA-CALL-2')

        response = self.client.post(
            '/api/assessments/call-ended/',
            {
                'event_type': 'meeting.ended',
                'room': assessment.jitsiRoom,
            },
            format='json',
            HTTP_X_CALL_WEBHOOK_SECRET='super-secret',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['updated'])

        assessment.refresh_from_db()
        self.assertEqual(assessment.status, 'Done')

    @override_settings(CALL_ENDED_WEBHOOK_SECRET='super-secret')
    def test_call_ended_webhook_rejects_unauthorized_request(self):
        assessment = self.create_assessment()

        response = self.client.post(
            '/api/assessments/call-ended/',
            {
                'event': 'call_ended',
                'assessment_id': assessment.id,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 403)

        assessment.refresh_from_db()
        self.assertEqual(assessment.status, 'Accepted')

    def test_call_ended_event_does_not_overwrite_final_decision(self):
        assessment = self.create_assessment(status='Assessment 1 Passed')
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.post(
            '/api/assessments/call-ended/',
            {
                'event': 'call_ended',
                'assessment_id': assessment.id,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['updated'])

        assessment.refresh_from_db()
        self.assertEqual(assessment.status, 'Assessment 1 Passed')

    def create_post_assessment(self, status='Accepted'):
        self.application.status = 'Verified Adopted'
        self.application.assessment_2_status = 'rescheduled'
        self.application.save()
        assessment = self.create_assessment(status=status)
        assessment.phase = 'post_adoption'
        assessment.application = self.application
        assessment.save()
        return assessment

    def test_finished_post_call_updates_application_once_and_awaits_final_review(self):
        assessment = self.create_post_assessment()
        self.client.force_authenticate(self.admin_user)
        for updated in [True, False]:
            response = self.client.post('/api/assessments/call-ended/', {
                'event': 'call_ended', 'assessment_id': assessment.pk,
            }, format='json')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['updated'], updated)
            self.application.refresh_from_db()
            self.assertEqual(self.application.status, 'Post-Adoption Done')
            self.assertEqual(self.application.assessment_2_status, 'done')

    def test_done_post_does_not_overwrite_final_application_decisions(self):
        assessment = self.create_post_assessment(status='Done')
        for status in ['Completed', 'Monitoring Passed', 'Awaiting Feed Post', 'Post-Adoption Rejected']:
            self.application.status = status
            self.application.assessment_2_status = 'rejected' if status == 'Post-Adoption Rejected' else 'passed'
            self.application.save()
            sync_application_from_assessment(assessment)
            self.application.refresh_from_db()
            self.assertEqual(self.application.status, status)
            self.assertIn(self.application.assessment_2_status, ['passed', 'rejected'])

    def test_first_call_done_does_not_complete_post_adoption(self):
        assessment = self.create_assessment(status='Done')
        sync_application_from_assessment(assessment)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, 'Assessment 1 Scheduled')
        self.assertEqual(self.application.assessment_1_status, 'done')
        self.assertEqual(self.application.assessment_2_status, 'waiting')

    def test_approving_adoption_reconciles_an_already_finished_post_assessment(self):
        self.create_post_assessment(status='Done')
        self.application.status = 'Assessment 1 Passed'
        self.application.save()
        self.client.force_authenticate(self.admin_user)
        response = self.client.patch(f'/admin_api/adoption-applications/{self.application.pk}/', {
            'status': 'Verified Adopted',
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'Post-Adoption Done')
        self.assertEqual(response.data['assessment_2_status'], 'done')

    def test_backfill_repairs_stale_records_and_preserves_final_decisions(self):
        import importlib
        from django.apps import apps
        from django.db import connection
        from types import SimpleNamespace
        reconcile = importlib.import_module('admin_api.migrations.0029_post_adoption_done').reconcile_completed_assessments
        assessment = self.create_post_assessment(status='Done')
        reconcile(apps, SimpleNamespace(connection=connection))
        reconcile(apps, SimpleNamespace(connection=connection))
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, 'Post-Adoption Done')
        self.assertEqual(self.application.assessment_2_status, 'done')
        self.application.status = 'Completed'
        self.application.assessment_2_status = 'passed'
        self.application.save()
        reconcile(apps, SimpleNamespace(connection=connection))
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, 'Completed')
        self.assertEqual(self.application.assessment_2_status, 'passed')
        assessment.refresh_from_db()
        self.assertEqual(assessment.status, 'Done')
