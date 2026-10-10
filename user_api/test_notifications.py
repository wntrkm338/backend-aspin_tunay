from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from admin_api.models import AdoptionApplication, Animal
from .models import Notification
from .notifications import notify_application_submitted


class NotificationBellTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='adopter',
            email='adopter@example.com',
            password='SafePass123!',
        )
        self.other_user = User.objects.create_user(
            username='other',
            email='other@example.com',
            password='SafePass123!',
        )
        self.admin = User.objects.create_user(
            username='admin',
            email='admin@example.com',
            password='SafePass123!',
            is_staff=True,
        )

    def test_user_notification_feed_is_scoped_and_can_mark_read(self):
        own_notification = Notification.objects.create(
            user=self.user,
            audience=Notification.AUDIENCE_USER,
            title='Application update',
            message='Your application changed.',
            event_type='adoption_status_changed',
            target_url='/user/animals',
        )
        other_notification = Notification.objects.create(
            user=self.other_user,
            audience=Notification.AUDIENCE_USER,
            title='Other update',
            message='Private update.',
        )
        admin_notification = Notification.objects.create(
            user=self.admin,
            audience=Notification.AUDIENCE_ADMIN,
            title='Admin update',
            message='Admin-only event.',
        )

        self.client.force_authenticate(user=self.user)
        response = self.client.get('/api/notifications/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.data], [own_notification.id])
        self.assertFalse(response.data[0]['is_read'])
        self.assertEqual(response.data[0]['link'], '/user/animals')

        patch_response = self.client.patch(
            '/api/notifications/',
            {'ids': [own_notification.id, other_notification.id, admin_notification.id], 'is_read': True},
            format='json',
        )

        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.data['updated'], 1)
        own_notification.refresh_from_db()
        other_notification.refresh_from_db()
        admin_notification.refresh_from_db()
        self.assertTrue(own_notification.is_read)
        self.assertFalse(other_notification.is_read)
        self.assertFalse(admin_notification.is_read)

    def test_admin_notification_feed_includes_admin_audience_only(self):
        user_notification = Notification.objects.create(
            user=self.user,
            audience=Notification.AUDIENCE_USER,
            title='User update',
            message='User-only event.',
        )
        admin_notification = Notification.objects.create(
            user=self.admin,
            audience=Notification.AUDIENCE_ADMIN,
            title='Admin update',
            message='Admin-only event.',
        )
        shared_admin_notification = Notification.objects.create(
            user=None,
            audience=Notification.AUDIENCE_ADMIN,
            title='Shared admin update',
            message='Visible to admin users.',
        )

        self.client.force_authenticate(user=self.admin)
        response = self.client.get('/api/notifications/?audience=admin')

        self.assertEqual(response.status_code, 200)
        returned_ids = {item['id'] for item in response.data}
        self.assertEqual(returned_ids, {admin_notification.id, shared_admin_notification.id})
        self.assertNotIn(user_notification.id, returned_ids)

        patch_response = self.client.patch(
            '/api/notifications/?audience=admin',
            {'all': True, 'is_read': True},
            format='json',
        )

        self.assertEqual(patch_response.status_code, 200)
        self.assertEqual(patch_response.data['updated'], 2)

    def test_application_submission_event_creates_user_and_admin_notifications(self):
        animal = Animal.objects.create(
            id='A001',
            name='Bantay',
            species='Dog',
            status='Ready for Adoption',
            availability='Available',
        )
        application = AdoptionApplication.objects.create(
            animalId=animal.id,
            submitted_by=self.user,
            fullName='Adopter One',
            age=28,
            civilStatus='Single',
            address='123 Sample Street',
            contact='09171234567',
            email=self.user.email,
            validIdType='Passport',
            residenceType='House',
            householdAgreement='Yes',
            dogPlace='Inside the house',
            indoorAllowed='Yes',
            whyAdopt='Ready to adopt.',
            lifetimeCommitment='Yes',
            agreedToTerms=True,
        )

        notify_application_submitted(application, animal)

        self.assertTrue(
            Notification.objects.filter(
                user=self.user,
                audience=Notification.AUDIENCE_USER,
                event_type='adoption_submitted',
                target_url='/user/animals',
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.admin,
                audience=Notification.AUDIENCE_ADMIN,
                event_type='adoption_submitted',
                target_url='/admin/adoption',
            ).exists()
        )
