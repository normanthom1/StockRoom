import tempfile

from django.test import TestCase, override_settings


class HealthzTest(TestCase):
    def test_healthz_runs_a_query_and_returns_ok(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")

    def test_healthz_fails_when_collectstatic_didnt_run(self):
        # Production's storage, with an empty STATIC_ROOT: what a build that skipped collectstatic ships.
        storages = {
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
        }
        with (
            tempfile.TemporaryDirectory() as empty,
            override_settings(STATIC_ROOT=empty, STORAGES=storages),
            self.assertRaisesMessage(ValueError, "Missing staticfiles manifest entry"),
        ):
            self.client.get("/healthz")


class ReadableConsoleEmailTest(TestCase):
    def test_long_links_are_printed_whole(self):
        # The reason this backend exists: stock console output wraps at 76
        # characters with '=' breaks, which splits a reset link in two.
        from io import StringIO

        from django.core.mail import EmailMessage

        from .mail import ReadableConsoleBackend

        link = "https://stockroom-production-1adf.up.railway.app/accounts/reset/MQ/derlav-4994933fb525d9d5128f69a43e766b1a/"
        stream = StringIO()
        ReadableConsoleBackend(stream=stream).send_messages(
            [EmailMessage("Reset your StockRoom password", f"Set a new one here:\n\n{link}\n", to=["sandy@kowhai.test"])]
        )
        self.assertIn(link, stream.getvalue())
