"""PWA installability: manifest, icons, service worker, iOS meta tags.

Static files (the manifest, the icons) aren't reliably fetchable through the
test client without a collectstatic run, so their *content* is checked
straight off disk - the same approach an installability check would need
regardless of how they're served. /sw.js is a real view, so that one is
checked the normal way.
"""

import json
import struct
from pathlib import Path

from django.conf import settings
from django.test import TestCase

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
