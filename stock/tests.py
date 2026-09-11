from django.test import Client, TestCase


class HomeSmokeTest(TestCase):
    def test_home_page_loads(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "StockRoom")
        self.assertContains(response, "vendor/htmx.min.js")
        self.assertContains(response, "vendor/alpine.min.js")

    def test_ping_returns_a_fragment(self):
        response = self.client.get("/ping/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Server replied at")
        # A fragment, not a full page.
        self.assertNotContains(response, "<html")

    def test_bump_without_csrf_token_is_rejected(self):
        csrf_client = Client(enforce_csrf_checks=True)
        response = csrf_client.post("/bump/")
        self.assertEqual(response.status_code, 403)

    def test_bump_with_csrf_header_increments_the_counter(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.get("/")  # sets the csrftoken cookie
        token = csrf_client.cookies["csrftoken"].value

        response = csrf_client.post("/bump/", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<span id="counter">1</span>')

        response = csrf_client.post("/bump/", HTTP_X_CSRFTOKEN=token)
        self.assertContains(response, '<span id="counter">2</span>')
