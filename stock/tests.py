from django.test import SimpleTestCase


class HomeSmokeTest(SimpleTestCase):
    def test_home_page_loads(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
