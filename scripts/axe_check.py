"""Run axe-core across StockRoom's key pages and fail on serious/critical
violations (issue #34's "Done when").

Not part of `manage.py test` or CI: it drives a real Chromium browser and
needs a server already running with demo data seeded. A dev-only check, run
by hand before a UI change ships:

    pip install playwright axe-core-python
    playwright install chromium
    python manage.py seed_demo --reset
    python manage.py runserver 8000 &
    python scripts/axe_check.py http://localhost:8000
"""

import sys

from axe_core_python.sync_playwright import Axe
from playwright.sync_api import sync_playwright

# seed_demo's practice login (stockroom.demo.DEMO_EMAIL / DEMO_PASSWORD).
PRACTICE_EMAIL = "reception@nzdentist.co.nz"
PRACTICE_PASSWORD = "password"

# (path, login_as) - login_as is "practice" (the practice login before anyone
# has entered a code), a seed_demo staff code, or None for a public page.
PAGES = [
    ("/", None),  # the public page, for anyone logged out
    ("/accounts/login/", None),
    ("/accounts/signup/", None),
    ("/offline/", None),
    ("/accounts/code/", "practice"),
    ("/accounts/team/", "practice"),
    ("/", "0000"),  # Sofia, manager
    ("/log-usage/", "0000"),
    ("/reorder/", "0000"),
    ("/deliveries/", "0000"),
    ("/items/", "0000"),
    ("/suppliers/", "0000"),
    ("/spending/", "0000"),
    ("/activity/", "0000"),
    ("/stocktake/", "0000"),
    ("/accounts/team/", "0000"),
    ("/setup/", "0000"),
    ("/setup/draft/", "0000"),
    ("/items/import/", "0000"),
    ("/items/catalogue/", "0000"),
    ("/items/matched/", "0000"),
    ("/ask/", "0000"),  # only with AI_API_KEY set; a 404 page otherwise
    ("/", "11"),  # Johanna, assistant
    ("/log-usage/", "11"),
    ("/reorder/", "11"),
    ("/ask/", "11"),
]
SERIOUS = {"serious", "critical"}


def login(page, base_url, who):
    page.goto(f"{base_url}/accounts/code/")
    if "/accounts/login/" in page.url:
        page.fill("input[name=username]", PRACTICE_EMAIL)
        page.fill("input[name=password]", PRACTICE_PASSWORD)
        page.click("form:has(input[name=username]) button[type=submit]")
        page.wait_for_load_state("networkidle")
    if who != "practice":
        # Through the on-screen keypad, the way staff actually sign in: a
        # manager's 4 digits submit themselves, an assistant's 2 need Go.
        for digit in who[:-1]:
            page.click(f"[data-code-key='{digit}']")
        with page.expect_navigation():
            page.click(f"[data-code-key='{who[-1]}']")
            if len(who) == 2:
                page.click("button[type=submit]:has-text('Go')")


def check_page(axe, page, label, failures):
    results = axe.run(page)
    violations = [v for v in results["violations"] if v["impact"] in SERIOUS]
    if violations:
        print(f"FAIL {label}")
        for v in violations:
            print(f"  [{v['impact']}] {v['id']}: {v['description']} ({len(v['nodes'])} node(s))")
            for node in v["nodes"][:3]:
                print(f"    {node['target']}")
        failures.append(label)
    else:
        print(f"ok   {label}")


def main(base_url):
    axe = Axe()
    failures = []
    checked = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        logged_in_as = None
        for path, login_as in PAGES:
            if login_as != logged_in_as:
                if login_as:
                    login(page, base_url, login_as)
                logged_in_as = login_as
            page.goto(f"{base_url}{path}")
            page.wait_for_load_state("networkidle")
            check_page(axe, page, f"{path} (as {login_as or 'anonymous'})", failures)
            checked += 1

            if path == "/" and login_as:
                # No fixed item pk to link to (seed_demo --reset recreates
                # rows with fresh ids), so follow a real link to the detail page.
                link = page.locator("main a[href^='/item/']").first
                if link.count():
                    link.click()
                    page.wait_for_load_state("networkidle")
                    check_page(axe, page, f"/item/<pk>/ (as {login_as})", failures)
                    checked += 1
        browser.close()

    print(f"\n{checked - len(failures)}/{checked} pages clean")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"))
