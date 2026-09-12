"""PWA installability: manifest, icons, service worker, iOS meta tags.

Static files (the manifest, the icons) aren't reliably fetchable through the
test client without a collectstatic run, so their *content* is checked
straight off disk - the same approach an installability check would need
regardless of how they're served. /sw.js is a real view, so that one is
checked the normal way.
"""

import json
import struct
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.templatetags.static import static
from django.test import TestCase

from accounts.models import Organisation, User
from stockroom.views import PRECACHE_STATIC, sw_version

MANIFEST_PATH = Path(settings.BASE_DIR) / "static" / "manifest.webmanifest"
ICONS_DIR = Path(settings.BASE_DIR) / "static" / "icons"


def _png_size(path):
    with open(path, "rb") as f:
        header = f.read(24)
    return struct.unpack(">II", header[16:24])


class ManifestTests(TestCase):
    def test_manifest_is_valid_json_with_the_required_fields(self):
        manifest = json.loads(MANIFEST_PATH.read_text())
        self.assertEqual(manifest["name"], "StockRoom")
        self.assertEqual(manifest["short_name"], "StockRoom")
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(manifest["display"], "standalone")
        self.assertIn("theme_color", manifest)
        self.assertIn("background_color", manifest)

    def test_manifest_has_a_192_and_a_512_icon(self):
        manifest = json.loads(MANIFEST_PATH.read_text())
        sizes = {icon["sizes"] for icon in manifest["icons"]}
        self.assertIn("192x192", sizes)
        self.assertIn("512x512", sizes)

    def test_the_512_icon_is_also_marked_maskable(self):
        manifest = json.loads(MANIFEST_PATH.read_text())
        icon_512 = next(icon for icon in manifest["icons"] if icon["sizes"] == "512x512")
        self.assertIn("maskable", icon_512["purpose"])

    def test_manifest_icon_files_exist_at_their_declared_sizes(self):
        manifest = json.loads(MANIFEST_PATH.read_text())
        for icon in manifest["icons"]:
            path = Path(settings.BASE_DIR) / icon["src"].lstrip("/")
            self.assertTrue(path.exists(), f"{path} is missing")
            width, height = _png_size(path)
            self.assertEqual(f"{width}x{height}", icon["sizes"])


class IconFileTests(TestCase):
    def test_apple_touch_icon_is_180_square(self):
        self.assertEqual(_png_size(ICONS_DIR / "apple-touch-icon.png"), (180, 180))

    def test_favicon_png_exists(self):
        self.assertTrue((ICONS_DIR / "favicon-32.png").exists())

    def test_svg_icon_exists_for_the_vector_favicon(self):
        svg = (ICONS_DIR / "icon.svg").read_text()
        self.assertIn("<svg", svg)


class BaseTemplateTests(TestCase):
    def test_login_page_declares_the_manifest_and_ios_tags(self):
        content = self.client.get("/accounts/login/").content.decode()
        self.assertIn('rel="manifest"', content)
        self.assertIn('name="theme-color" content="#5980a6"', content)
        self.assertIn('rel="apple-touch-icon"', content)
        self.assertIn('name="apple-mobile-web-app-capable" content="yes"', content)
        self.assertIn('name="apple-mobile-web-app-title" content="StockRoom"', content)

    def test_login_page_registers_the_service_worker(self):
        content = self.client.get("/accounts/login/").content.decode()
        self.assertIn('navigator.serviceWorker.register("/sw.js")', content)

    def test_login_page_has_the_ios_install_hint(self):
        content = self.client.get("/accounts/login/").content.decode()
        self.assertIn("Add to Home Screen", content)
        self.assertIn('id="ios-install-hint"', content)


class ServiceWorkerViewTests(TestCase):
    def test_sw_js_is_served_from_the_site_root(self):
        response = self.client.get("/sw.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/javascript")

    def test_sw_js_does_not_require_login(self):
        # No force_login here - this must work for a signed-out visitor too.
        response = self.client.get("/sw.js")
        self.assertEqual(response.status_code, 200)

    def test_sw_js_registers_lifecycle_handlers(self):
        content = self.client.get("/sw.js").content.decode()
        self.assertIn('addEventListener("install"', content)
        self.assertIn('addEventListener("activate"', content)
        self.assertIn('addEventListener("fetch"', content)

    def test_sw_js_precaches_the_app_shell_and_offline_page(self):
        content = self.client.get("/sw.js").content.decode()
        for path in PRECACHE_STATIC:
            self.assertIn(json.dumps(static(path)), content)
        self.assertIn('"/offline/"', content)
        self.assertIn('const SESSION_URLS = ["/accounts/login/", "/accounts/logout/"]', content)
        self.assertIn('const CAPTURE_PAGE_URL = "/log-usage/"', content)

    def test_sw_js_embeds_the_offline_fragment(self):
        content = self.client.get("/sw.js").content.decode()
        self.assertIn("You're offline. Check your Wi-Fi", content)

    def test_new_worker_waits_for_the_user_instead_of_skipping_waiting_on_install(self):
        content = self.client.get("/sw.js").content.decode()
        self.assertNotIn('addEventListener("install", () => self.skipWaiting())', content)
        self.assertIn('event.data === "skip-waiting"', content)


class CacheVersionTests(TestCase):
    def test_version_is_stable_between_requests(self):
        self.assertEqual(sw_version("<p>offline</p>"), sw_version("<p>offline</p>"))

    def test_version_changes_when_the_offline_page_changes(self):
        self.assertNotEqual(sw_version("<p>offline</p>"), sw_version("<p>offline!</p>"))

    def test_version_changes_when_a_precached_file_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            asset = Path(tmp) / "asset"
            asset.write_bytes(b"v1")
            with patch("stockroom.views.finders.find", return_value=str(asset)):
                before = sw_version("<p>offline</p>")
                asset.write_bytes(b"v2")
                after = sw_version("<p>offline</p>")
        self.assertNotEqual(before, after)

    def test_version_ignores_precached_files_that_are_not_built_yet(self):
        with patch("stockroom.views.finders.find", return_value=None):
            self.assertTrue(sw_version("<p>offline</p>"))


class OfflinePageTests(TestCase):
    def test_offline_page_does_not_require_login(self):
        response = self.client.get("/offline/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You're offline")
        self.assertContains(response, "Try again")

    def test_offline_page_never_includes_the_logged_in_users_details(self):
        # The service worker precaches this page while someone is logged in,
        # so it must not carry their name, practice or navigation.
        org = Organisation.objects.create(name="Smile Dental")
        user = User.objects.create_user("sandy@smile.test", "pw", organisation=org, name="Sandy")
        self.client.force_login(user)
        content = self.client.get("/offline/").content.decode()
        self.assertNotIn("Smile Dental", content)
        self.assertNotIn("sandy@smile.test", content)
        self.assertNotIn("Log out", content)


class UpdateToastTests(TestCase):
    def test_base_template_has_the_update_toast_and_its_wiring(self):
        content = self.client.get("/accounts/login/").content.decode()
        self.assertIn('id="sw-update"', content)
        self.assertIn("New version available, tap to reload", content)
        self.assertIn('postMessage("skip-waiting")', content)
