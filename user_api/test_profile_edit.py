from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import UserProfile as AccountProfile
from admin_api.models import AdoptionApplication, AssessmentEmail, RemoteAssessment
from admin_api.scheduling import process_due_emails
from .models import UserProfile


class ProfileEditTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='adopter', email='before@example.com', first_name='Original Name')
        self.other = User.objects.create_user(username='other', email='other@example.com')
        self.profile = UserProfile.objects.create(user=self.user, full_name='Original Name')
        self.account_profile = AccountProfile.objects.create(user=self.user, full_name='Original Name')
        self.application = AdoptionApplication.objects.create(submitted_by=self.user, animalId='DOG1',
            fullName='Original Name', email='before@example.com', age=30)
        self.other_application = AdoptionApplication.objects.create(submitted_by=self.other, animalId='DOG2',
            fullName='Other Name', email='other@example.com', age=30)
        self.unlinked = AdoptionApplication.objects.create(animalId='DOG3', fullName='Original Name',
            email='before@example.com', age=30)
        self.assessment = RemoteAssessment.objects.create(id='PROFILE-RA1', application=self.application,
            appId=str(self.application.pk), phase='first', date=date(2030, 1, 1), time='09:00',
            adopterName='Original Name', animalName='Milo', status='Scheduled')
        self.job = AssessmentEmail.objects.create(application=self.application, assessment=self.assessment,
            event_key='profile-edit-pending', kind='schedule', recipient='before@example.com',
            subject='Assessment schedule', body='Your assessment schedule', due_at=timezone.now(),
            schedule_versions={self.assessment.pk: self.assessment.revision})
        self.sent_job = AssessmentEmail.objects.create(application=self.application, assessment=self.assessment,
            event_key='profile-edit-sent', kind='update', recipient='before@example.com', status='sent',
            subject='Past update', body='Past update', due_at=timezone.now() - timedelta(days=1))
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_owner_can_update_name_email_and_linked_application_contacts(self):
        response = self.client.patch('/api/profile/', {'full_name': '  Updated Name  ', 'email': ' UPDATED@example.com '}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['full_name'], 'Updated Name')
        self.assertEqual(response.data['email'], 'updated@example.com')
        for obj in (self.user, self.profile, self.account_profile, self.application, self.assessment, self.job, self.sent_job):
            obj.refresh_from_db()
        self.assertEqual(self.user.username, 'adopter')
        self.assertEqual(self.user.get_full_name(), 'Updated Name')
        self.assertEqual(self.profile.full_name, self.account_profile.full_name)
        self.assertEqual(self.application.fullName, 'Updated Name')
        self.assertEqual(self.application.email, 'updated@example.com')
        self.assertEqual(self.assessment.adopterName, 'Updated Name')
        self.assertEqual(self.job.recipient, 'updated@example.com')
        self.assertEqual(self.sent_job.recipient, 'before@example.com')
        self.assertEqual(self.client.get('/api/profile/').data['profile']['email'], 'updated@example.com')
        process_due_emails()
        self.assertEqual(mail.outbox[0].to, ['updated@example.com'])

    def test_other_and_unlinked_applications_are_not_changed(self):
        self.client.patch('/api/profile/', {'full_name': 'Updated Name', 'email': 'updated@example.com'})
        self.other_application.refresh_from_db()
        self.unlinked.refresh_from_db()
        self.assertEqual(self.other_application.email, 'other@example.com')
        self.assertEqual(self.unlinked.email, 'before@example.com')
        self.assertEqual(self.unlinked.fullName, 'Original Name')

    def test_duplicate_email_is_case_insensitive_and_nothing_is_partially_saved(self):
        response = self.client.patch('/api/profile/', {'full_name': 'Must Not Save', 'email': 'OTHER@EXAMPLE.COM'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('email', response.data)
        self.profile.refresh_from_db()
        self.application.refresh_from_db()
        self.assertEqual(self.profile.full_name, 'Original Name')
        self.assertEqual(self.application.email, 'before@example.com')

    def test_invalid_or_blank_values_are_rejected(self):
        for payload in ({'email': 'invalid'}, {'email': ''}, {'full_name': '  '}, {'full_name': 'a' * 101}):
            self.assertEqual(self.client.patch('/api/profile/', payload).status_code, 400)

    def test_partial_name_change_preserves_email_and_images(self):
        response = self.client.put('/api/profile/', {'full_name': 'Name Only'})
        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'before@example.com')
        self.assertEqual(self.profile.avatar.name, 'default.jpg')
        self.assertEqual(self.profile.cover_photo.name, 'default_cover.jpg')

    def test_email_is_only_visible_to_profile_owner(self):
        self.assertIn('email', self.client.get('/api/profile/adopter/').data['profile'])
        self.assertNotIn('email', self.client.get('/api/profile/other/').data['profile'])

    def test_cannot_edit_someone_else_or_change_account_privileges(self):
        self.assertEqual(self.client.patch('/api/profile/other/', {'email': 'attacker@example.com'}).status_code, 403)
        response = self.client.patch('/api/profile/', {'username': 'other', 'is_staff': True, 'user': self.other.pk,
                                                       'full_name': 'Allowed Name'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)
        self.assertEqual(self.user.username, 'adopter')

    def test_login_is_required(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.patch('/api/profile/', {'email': 'new@example.com'}).status_code, 401)
