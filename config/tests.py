from django.test import TestCase


class SmokeTests(TestCase):
    def test_admin_login_page_loads(self):
        response = self.client.get("/admin/login/")
        self.assertEqual(response.status_code, 200)

    def test_404_page_is_friendly(self):
        from django.contrib.auth import get_user_model
        self.client.force_login(get_user_model().objects.create_user("ana"))
        response = self.client.get("/no-such-page/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Page not found", status_code=404)

    def test_500_page_renders_without_context(self):
        from django.template.loader import render_to_string
        self.assertIn("Something went wrong", render_to_string("500.html"))
