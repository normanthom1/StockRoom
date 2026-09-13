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

# (path, login_as) - login_as is an email from seed_demo, or None for a public page.
PAGES = [
    ("/accounts/login/", None),
    ("/accounts/signup/", None),
    ("/offline/", None),
    ("/", "sandy@demodental.test"),
    ("/log-usage/", "sandy@demodental.test"),
    ("/reorder/", "sandy@demodental.test"),
    ("/deliveries/", "sandy@demodental.test"),
    ("/items/", "sandy@demodental.test"),
    ("/suppliers/", "sandy@demodental.test"),
    ("/spending/", "sandy@demodental.test"),
    ("/activity/", "sandy@demodental.test"),
    ("/stocktake/", "sandy@demodental.test"),
    ("/accounts/team/", "sandy@demodental.test"),
    ("/", "johanna@demodental.test"),
    ("/log-usage/", "johanna@demodental.test"),
]
SERIOUS = {"serious", "critical"}


def login(page, base_url, email):
    page.goto(f"{base_url}/accounts/login/")
    page.fill("input[name=username]", email)
    page.fill("input[name=password]", "DemoPass123")
    page.click("form:has(input[name=username]) button[type=submit]")
    page.wait_for_load_state("networkidle")


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
