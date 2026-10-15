from django.test import TestCase


class SmokeTests(TestCase):
    def test_admin_login_page_loads(self):
        response = self.client.get("/admin/login/")
        self.assertEqual(response.status_code, 200)
