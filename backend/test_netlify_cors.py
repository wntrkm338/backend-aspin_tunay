from django.conf import settings
from django.test import SimpleTestCase
from rest_framework.test import APIClient


class NetlifyCorsTests(SimpleTestCase):
    def test_configured_netlify_site_gets_cors_permission_without_wildcards(self):
        self.assertFalse(settings.CORS_ALLOW_ALL_ORIGINS)
        self.assertIn('https://aspin-tunay-at-tapat.netlify.app', settings.CORS_ALLOWED_ORIGINS)
        client = APIClient()
        for endpoint in ('/api/public/animals/', '/api/public/impact-stats/', '/api/paymongo/summary/', '/api/login/'):
            response = client.options(endpoint,
                HTTP_ORIGIN='https://aspin-tunay-at-tapat.netlify.app',
                HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST',
                HTTP_ACCESS_CONTROL_REQUEST_HEADERS='authorization,content-type')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Access-Control-Allow-Origin'], 'https://aspin-tunay-at-tapat.netlify.app')
            self.assertIn('authorization', response['Access-Control-Allow-Headers'])
            self.assertIn('Retry-After', response['Access-Control-Expose-Headers'])

    def test_another_netlify_site_is_not_trusted(self):
        response = APIClient().options('/api/public/animals/',
            HTTP_ORIGIN='https://untrusted-other-site.netlify.app',
            HTTP_ACCESS_CONTROL_REQUEST_METHOD='GET')
        self.assertNotIn('Access-Control-Allow-Origin', response)
