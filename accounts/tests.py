from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import LearnerProfile


class LoginRequiredTests(TestCase):
    def test_anonymous_home_redirects_to_login(self):
        response = self.client.get(reverse("home"))
        self.assertRedirects(response, f"{reverse('account_login')}?next=/", fetch_redirect_response=False)

    def test_login_page_offers_google_only(self):
        response = self.client.get(reverse("account_login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/accounts/google/login/")
        self.assertNotContains(response, 'type="password"')


class HomeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("ana", "ana@example.com", first_name="Ana")
        self.client.force_login(self.user)

    def test_first_visit_creates_profile_and_goes_to_welcome(self):
        response = self.client.get(reverse("home"))
        self.assertRedirects(response, reverse("welcome"))
        self.assertTrue(LearnerProfile.objects.filter(user=self.user, onboarded=False).exists())

    def test_welcome_greets_learner(self):
        self.assertContains(self.client.get(reverse("welcome")), "Welcome, Ana")

    def test_logout_page_uses_site_layout(self):
        response = self.client.get(reverse("account_logout"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "navbar")
