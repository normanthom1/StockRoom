# StockRoom UX review: what we found and what changed

A usability review of StockRoom done as a New Zealand practice manager with no
training, no demo and no manual. The question was narrow: **can someone who
signed up between patients get the practice's stock into it without being shown
how?** Everything was found by using the running app at 390px, not by reading
the code.

Full detail, including the five issues still open, is in
[UX_issues.md](UX_issues.md).

## What we found

StockRoom is in better shape than most apps this size. Accessibility is clean —
axe-core reports **0 serious or critical violations across 29 pages**, as both a
manager and an assistant. A sweep of every link, button, input and summary
across 15 pages found **2 elements under the project's 48px minimum**, both
radio inputs sitting inside full-width 48px labels. The copy is genuinely NZ:
"Order by Friday", "Overdue since Tue", GST and tax-year wording that matches
how IRD talks, and no US spellings anywhere in user-facing text.

The problems were all in one place: **what happens when something goes wrong, or
when the practice's setup does not match the happy path.** Ten issues, five
fixed here.

The worst of them was the first thing a new practice sees. On an install without
an AI key — a supported configuration — a manager signing up landed on Home,
tapped the only button, reached the setup checklist, tapped its one big blue
button, and got a **Django 404**. The whole first-run funnel died on the first
tap. The landing page, Home, Stock and Deliveries all checked whether AI was
available and changed what they offered; the setup checklist did not.

Two more were quieter and cost more. The undo on an invoice import — the action
that receives stock **and overwrites item prices** — existed only on a toast
that hides itself after 6 seconds, against a server window of 30. After six
seconds a misread price was permanent, and those prices feed what the practice
hands its accountant. And an invoice StockRoom could not fully read was saved as
"Needs checking", received nothing, and was then **reachable from nowhere in the
app**: a manager could import 20 invoices, have 4 come back needing checking,
navigate away, and never find them again. Silent data loss dressed up as
success.

## What changed

| | Issue | What it was | What it is now |
|---|---|---|---|
| [#127](https://github.com/normanthom1/StockRoom/pull/127) | UX-01 | Setup's main button 404s without an AI key | The checklist adapts: 3 reachable steps starting at the catalogue |
| [#128](https://github.com/normanthom1/StockRoom/pull/128) | UX-02 | Import undo reachable for 6 seconds | 10 minutes, matching the rest of the app, with a durable button on the invoice |
| [#129](https://github.com/normanthom1/StockRoom/pull/129) | UX-03 | "Needs checking" invoices unreachable | Listed at the top of `/invoices/`, counted on Deliveries |
| [#130](https://github.com/normanthom1/StockRoom/pull/130) | UX-04 | Dates read "Sept. 18, 2026" | "18 September 2026", via a real `en_NZ` format module |
| [#131](https://github.com/normanthom1/StockRoom/pull/131) | UX-05 | No instrumentation at all | Structured flow events and `manage.py ux_metrics` |

All five are merged to `main`. Each PR carries before/after screenshots, its
acceptance criteria, and test steps.

Two of the fixes are worth singling out for *how* they were done rather than
what they did. UX-04's obvious fix — setting `DATE_FORMAT` in `settings.py` —
silently does nothing while `USE_I18N` is on, because `get_format()` reads the
active locale's format module first. It took a real `en_NZ` module to work, and
that now fixes every date in the app rather than the two templates that happened
to show the bug. And UX-05 deliberately avoided a third-party analytics script:
StockRoom self-hosts everything, runs a strict CSP with no inline script, and
tells practices it keeps nothing about patients. Python's own logging, one JSON
object per line, breaks none of that and needed no dependency, no model and no
migration.

## Metrics

The success metric is **setup completion rate**: the share of practices that get
from signing up to a stock list with at least one active item. It is the one
number UX-01 should move, and it is computed from data StockRoom already stores:

```
$ python manage.py ux_metrics --days 90
  Setup completion rate: 83%
    5 of 6 reached a stock list with at least one active item
```

Alongside it, the report tracks the needs-checking backlog UX-03 made findable,
and how often a matched name gets undone — which says whether the product
matching in `stock/matching.py` is trusted or merely tolerated.

Six structured events now cover the flows these fixes touch
(`setup_step_opened`, `setup_completed`, `invoice_imported`,
`invoice_needs_checking`, `invoice_undone`, `merge_undone`). Each carries an
organisation id and counts, never a name, an email, an item or a price — there
is a test that asserts exactly that.

**There is no before figure.** Nothing was measured until #131, so these are a
baseline, not an improvement. The honest read on the five fixes is that UX-01
removed a verified dead end, UX-02 and UX-03 closed two paths to silent data
loss, and the numbers will say from here whether that was enough.

## Quality

Every PR was merged only after all of these passed:

- `python manage.py test` — **549 tests, OK** (24 added across the five fixes)
- `python manage.py makemigrations --check --dry-run` — no changes
- `python manage.py check` — no issues
- `ruff check .` — passes
- `python scripts/axe_check.py` — **29/29 pages clean**, no serious or critical
  violations, as both a manager and an assistant
- GitHub Actions CI green on the PR before merge

New tests were checked against the *old* code first, to confirm they fail — a
test that passes either way documents a fix without catching the bug.

## What to do next

1. **UX-06 — batch import changes every price with no preview and no undo.**
   The last place a manager can lose real money with no way back. Up to 50
   invoices are ingested with no review step, every matched line overwrites its
   item's price, and there is no batch-level undo. The setup flow pushes a
   brand-new manager straight at it, so their first action is the least
   reversible one in the app.
2. **UX-08 — no plain English for the words StockRoom invented.** "Matched
   names", "Needs checking", "Confident" are never defined. Cheap to fix, and it
   is what stops an untrained manager using the merge review that keeps the
   stock list free of duplicates.
3. **UX-07, UX-09, UX-10** — time and friction rather than money.

One thing outside the review worth mentioning: the test suite fails if a
developer's local `.env` has `DEMO_MODE=1`, because several tests assert the
demo sign-in button is absent. It cost a confusing few minutes here. Either the
tests should pin `DEMO_MODE=0` themselves or `CONTRIBUTING` should say so.

## How this was done

`ui-ux-pro-max` supplied the review lens, the severity model, and the guidance
behind UX-02 and UX-03 (Feedback/Toast, Feedback/Error Recovery,
Navigation/dead ends). The repo's own `ui-design` skill governed every template
change — tap targets, status colours, the `blueprint` panel, list row structure
at 390px, and the copy rules. `org-scoped-view` governed the view and permission
changes, including the two-organisation isolation test in #129.

To re-run the review:

```bash
python manage.py seed_demo --reset
python manage.py runserver 8000 --noreload &
python scripts/axe_check.py http://127.0.0.1:8000   # expect 29/29 clean
```

Then walk the app at 390px as `0000` (manager) and `11` (assistant) — **and as a
practice with no items at all.** That last one is where UX-01 was hiding, and
the demo data does not cover it.
