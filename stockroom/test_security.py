"""Deploy settings and the Content Security Policy."""

import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase

TEMPLATE_DIRS = [Path(settings.BASE_DIR) / "templates", *Path(settings.BASE_DIR).glob("*/templates")]
# What a CSP with no 'unsafe-inline' blocks: inline <script>, on*= handlers, style="".
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>", re.IGNORECASE)
INLINE_HANDLER = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)
INLINE_STYLE = re.compile(r"\sstyle\s*=", re.IGNORECASE)
# What the CSP build of Alpine can't evaluate: anything but a property path.
ALPINE_EXPRESSION = re.compile(r'(?:\sx-(?:data|show|text|html|init|if|effect)|\s[@:][\w.-]+)="([^"]*)"')


class DeployCheckTests(SimpleTestCase):
    def test_deploy_check_is_clean_with_production_settings(self):
        env = {**os.environ, "DEBUG": "0", "SECRET_KEY": secrets.token_urlsafe(50), "ALLOWED_HOSTS": "stockroom.test"}
        result = subprocess.run(
            [sys.executable, "manage.py", "check", "--deploy", "--fail-level", "WARNING"],
            cwd=settings.BASE_DIR, env=env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class ContentSecurityPolicyTests(TestCase):
    def test_every_response_carries_a_strict_policy(self):
        policy = self.client.get("/accounts/login/")["Content-Security-Policy"]
        self.assertIn("script-src 'self'", policy)
        self.assertIn("style-src 'self'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertNotIn("unsafe", policy)

    def test_the_offline_page_and_service_worker_are_covered_too(self):
        for url in ["/offline/", "/sw.js"]:
            self.assertIn("script-src 'self'", self.client.get(url)["Content-Security-Policy"], url)

    def test_htmx_runs_without_eval(self):
        content = self.client.get("/accounts/login/").content.decode()
        self.assertIn('<meta name="htmx-config" content=\'{"allowEval": false, "includeIndicatorStyles": false}\'>', content)
        self.assertIn("vendor/alpine-csp.min.js", content)

    def test_no_template_has_inline_script_handlers_or_styles(self):
        found = []
        for directory in TEMPLATE_DIRS:
            for path in directory.rglob("*.html"):
                text = path.read_text(encoding="utf-8")
                for pattern in (INLINE_SCRIPT, INLINE_HANDLER, INLINE_STYLE):
                    found += [f"{path.relative_to(settings.BASE_DIR)}: {m.group(0).strip()}" for m in pattern.finditer(text)]
        self.assertEqual(found, [], "Move it to static/js/app.js or a CSS class; the CSP blocks it.")

    def test_alpine_expressions_are_plain_property_paths(self):
        found = []
        for directory in TEMPLATE_DIRS:
            for path in directory.rglob("*.html"):
                for m in ALPINE_EXPRESSION.finditer(path.read_text(encoding="utf-8")):
                    expression = m.group(1)
                    if m.group(0).lstrip().startswith((":hx", ":data")) or expression.startswith("{%"):
                        continue
                    if expression and not re.fullmatch(r"[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*", expression):
                        found.append(f"{path.relative_to(settings.BASE_DIR)}: {m.group(0).strip()}")
        self.assertEqual(found, [], "The CSP build of Alpine only reads names; add a method or getter in app.js.")
