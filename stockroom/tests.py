from django.test import TestCase


class HealthzTest(TestCase):
    def test_healthz_runs_a_query_and_returns_ok(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")
