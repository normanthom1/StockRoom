# StockRoom UX issues

A usability review of StockRoom done as a New Zealand dental practice manager
who has had no training, no demo and no manual: someone who signed up between
patients and has to get the practice's stock into it before the next one.

Everything below was found by using the running app at 390px, not by reading
the code. Each issue says what the manager actually hits, what it costs them,
and what fixes it.

| Field | Value |
|---|---|
| Reviewed | 18 September 2026 |
| Build | `main` at 7ac45c0 |
| Persona | Practice manager, non-technical, NZ English, phone first |
| Method | Playwright walkthrough at 390×844, axe-core, first-run as a brand-new practice |
| Skills used | `ui-ux-pro-max` (review lens, severity model, undo/empty-state guidance), repo `ui-design` (tap targets, copy, status colours, components), repo `org-scoped-view` (view/permission pattern for the fixes) |

## What is already good

Worth saying plainly, because it sets the bar the issues below fall short of:

- **Accessibility is clean.** axe-core reports 0 serious or critical violations
  across all 29 checked pages, as both a manager and an assistant.
- **Tap targets pass.** A sweep of every link, button, input and summary across
  15 pages at 390px found 2 elements under 48px, both radio inputs inside
  full-width 48px labels. Effectively zero.
- **The copy is genuinely NZ.** "Order by Friday", "Overdue since Tue",
  "~2 wks", GST and tax-year wording that matches how IRD talks. No US
  spellings anywhere in user-facing text.
- **Product matching is properly built**, not bolted on. See
  [Product canonicalisation and dedup](#product-canonicalisation-and-dedup).

The issues are concentrated in one place: **what happens when something goes
wrong, or when the practice's setup does not match the happy path.**

---

## Priority

| # | Issue | Severity | Effort | Status |
|---|---|---|---|---|
| [UX-01](#ux-01--setup-sends-a-new-practice-to-a-404) | Setup sends a new practice to a 404 | Critical | S | Fixed — [#127](https://github.com/normanthom1/StockRoom/pull/127) |
| [UX-02](#ux-02--undo-on-an-invoice-import-is-gone-in-six-seconds) | Undo on an invoice import is gone in six seconds | High | S | Fixed — [#128](https://github.com/normanthom1/StockRoom/pull/128) |
| [UX-03](#ux-03--invoices-that-need-checking-become-invisible) | Invoices that need checking become invisible | High | M | Fixed — [#129](https://github.com/normanthom1/StockRoom/pull/129) |
| [UX-04](#ux-04--dates-render-in-us-format) | Dates render in US format | High | S | Fixed — [#130](https://github.com/normanthom1/StockRoom/pull/130) |
| [UX-05](#ux-05--nothing-measures-whether-any-of-this-works) | Nothing measures whether any of this works | Medium | S | Fixed — [#131](https://github.com/normanthom1/StockRoom/pull/131) |
| [UX-06](#ux-06--a-batch-import-never-says-what-it-did) | A batch import never says what it did | High | M | Fixed — [#133](https://github.com/normanthom1/StockRoom/pull/133) |
| [UX-07](#ux-07--the-stock-list-cannot-tell-you-what-is-low) | The stock list cannot tell you what is low | Medium | M | Pending |
| [UX-08](#ux-08--no-plain-english-for-the-words-stockroom-invented) | No plain English for the words StockRoom invented | Medium | S | Pending |
| [UX-09](#ux-09--match-to-assumes-you-remember-what-you-ordered) | "Match to" assumes you remember what you ordered | Medium | M | Pending |
| [UX-10](#ux-10--a-failed-invoice-read-loses-the-managers-place) | A failed invoice read loses the manager's place | Low | S | Pending |

Pending issues are ordered by severity, not by number. **UX-08 is the one to do
next**: it is cheap, and it is what stops an untrained manager using the merge
review that keeps the stock list free of duplicates.

Severity is the `ui-ux-pro-max` scale: **Critical** blocks the task outright,
**High** costs real money or data, **Medium** costs time, **Low** is friction.
Effort is S (under an hour), M (half a day), L (more than a day).

---

## Fixed in this review

### UX-01 — Setup sends a new practice to a 404

**Labels:** `frontend` `backend`

**Problem.** A practice that signs up on an install without `AI_API_KEY` set
lands on Home, taps the only button there ("Get started: set up your
practice"), and arrives at the setup checklist. Step 1 is "Bring in what you
order" and its primary 64px blue button says **Upload invoices**. Tapping it
gives a Django 404 page.

`/invoices/` is decorated `@ai_required` (`stock/views.py:1042`), which 404s
whenever `AI_API_KEY` is empty. Running without an AI key is a supported
configuration — the landing page, Home, Stock and Deliveries all check
`ai_enabled` and change what they offer. `stock/setup.py` does not, so it keeps
pointing the manager at a page that cannot exist.

Verified end to end on a fresh practice:

```
home:         /                -> "Get started: set up your practice"
setup:        /setup/          -> primary CTA "Upload invoices" -> /invoices/
CTA lands on: 404              http://127.0.0.1:8731/invoices/
```

**Impact.** This is the first thing a new practice is told to do, and the whole
first-run funnel dies on it. The two paths that do work — "Pick from the
catalogue" and "Bring in a list" — are in a secondary panel below four steps,
past the fold. A manager with no training has no reason to believe the app
works at all. `ui-ux-pro-max` Feedback/Error Recovery: *"Don't: error without
recovery path."* A raw 404 is the purest form of that.

**Fix.** Make the checklist follow what the install can actually do.
`setup.steps()` takes whether invoice reading is available; when it is not,
step 1 becomes "Pick what you stock" pointing at the catalogue, and the "Check
the draft" step (which only exists to review invoice output) drops out
entirely, leaving a three-step list that is all reachable. The page's intro
copy stops promising invoices, and `setup_draft.html`'s "Upload more invoices"
button is guarded the same way.

**Acceptance criteria**
1. With `AI_API_KEY` unset, no link or button on `/setup/` or `/setup/draft/`
   resolves to a URL that returns 404 — asserted by walking every `href` in the
   rendered page.
2. With `AI_API_KEY` unset, `/setup/` shows 3 steps and its primary CTA points
   at `stock:catalogue`; with a key set, it shows 4 steps and the CTA points at
   `stock:invoice_upload`.
3. With `AI_API_KEY` unset, the word "invoice" does not appear in the setup
   page's step titles, blurbs or CTA text.

**UI note.** The step list keeps its numbering, stripe colours and `btn-primary`
on the next step; only the count, wording and destination change. No new
component — the checklist already renders whatever `steps()` returns.

**Sample invoice mapping.** Not applicable: this issue is the path taken when
there are no invoices to map. The catalogue route creates the same `Item` shape
that an invoice draft would:

```python
# stock/setup.py -- an invoice draft becomes an Item
Item(name="Nitrile gloves, size M", unit="box",
     supplier=Supplier(name="Henry Schein"), price=Decimal("28.50"),
     order_size=10, supplier_sku="HS-4471")
# stock/catalogue.py -- the catalogue route creates the same fields,
# with price and order_size left None for the manager to fill in later.
```

---

### UX-02 — Undo on an invoice import is gone in six seconds

**Labels:** `frontend` `backend`

**Problem.** Confirming an invoice is the most consequential thing a manager
does: it receives stock against open orders, writes `StockEvent` rows, splits
back-orders and **overwrites item prices**. It is offered an Undo, on the toast
that appears after confirming.

That toast auto-hides after 6 seconds (`static/js/app.js:33`). The server-side
undo window is 30 seconds (`stock/invoices.py:306`). There is no Undo anywhere
else — not on the invoice detail page the manager is redirected to, not
anywhere in the nav. After 6 seconds the only way back is to fix every order
line and price by hand.

The app's own standard for undo is 10 minutes: `stock/views.py:66` sets
`UNDO_WINDOW = timedelta(minutes=10)` for logging usage and marking things
ordered. Invoice import — far more destructive than either — got 30 seconds.

If the manager does somehow POST the undo after it expires, the view raises
`PermissionDenied` and they get a bare 403 page.

**Impact.** A misread quantity or a wrong price from a photo silently becomes
the practice's stock level and its catalogue price, and stays there. Prices
feed the Spending and Stock expenses pages, so a bad import quietly corrupts
what the manager hands the accountant. CLAUDE.md says "Mistakes are fixed with
undo, not confirm dialogs" — for this action the undo is not reachable.

`ui-ux-pro-max` Feedback/Toast: toasts *should* auto-dismiss in 3-5 seconds, so
the toast is not the bug. The bug is that a 6-second toast is the only surface
for a destructive action's only recovery path.

**Fix.** Align the invoice undo window with the 10 minutes the rest of the app
already uses, and put a durable "Undo this import" panel on the invoice detail
page for as long as the window is open, saying when it closes. Keep the toast
exactly as it is. An expired undo returns the manager to the invoice with a
plain message instead of a 403.

**Acceptance criteria**
1. After confirming an invoice, its detail page shows an "Undo this import"
   button and the deadline; the button is absent once `undo_until` has passed.
2. Tapping it restores every order line, item price and stock level to what
   they were before the import, and the invoice returns to "Nothing received yet".
3. POSTing to `stock:invoice_undo` after `undo_until` redirects to the invoice
   with a readable message and returns no 4xx status.

**UI note.** The panel is a `blueprint` box above the line list, matching the
"Needs checking" and "Already imported" panels already on that page, with the
button as `btn btn-line` — a secondary action, not a primary one.

**Sample invoice mapping.** The snapshot the undo restores from, one entry per
received line:

```python
# stock/invoices.py -- Invoice.undo_snapshot
{"invoice_line_id": 5012, "order_line_id": 880, "remainder_id": 881,
 "unit_price": "28.50",        # the order line's price before this receipt
 "item_price": "27.00",        # the item's catalogue price before this receipt
 "item_before_id": 145, "stock_event_id": 9931}
# undo deletes stock_event_id and remainder_id, reopens order_line_id,
# and puts both prices back.
```

---

### UX-03 — Invoices that need checking become invisible

**Labels:** `frontend` `backend`

**Problem.** An invoice StockRoom cannot fully read is saved with status
`conflict`, displayed as **"Needs checking"** — no supplier it recognises, or
lines that do not add up. Nothing was received from it, so the practice's stock
is wrong until someone opens it and sorts it out.

There is no invoice list in StockRoom. The only links to `invoice_detail` in
the whole codebase are:

- the batch page, which needs the batch URL you are on at the time,
- the preview's Cancel button,
- the Stock expenses page's "Not in any year" list, which only holds invoices
  with **no date**.

The upload page lists unfinished batches, but "unfinished" is
`TO_READ = [PENDING, FAILED]` (`stock/invoices.py:469`). A file that parsed
into a needs-checking invoice is not pending and did not fail, so its batch
disappears from that list the moment reading finishes.

So: a manager uploads 20 invoices, 4 come back needing checking, they navigate
away — and those 4 are now reachable from nowhere in the UI. The practice's
counts and prices stay wrong and the app never mentions it again.

**Impact.** Silent data loss dressed up as success. The manager believes the
import worked. Stock levels under-report, the forecast orders the wrong things,
and the invoices are missing from the tax-year totals they hand the accountant.

**Fix.** `/invoices/` is already the invoices home; make it list them. Invoices
needing checking come first with what is wrong and a direct link, then recent
imports so there is always a way back to any invoice. The Deliveries page's
existing "Upload an invoice or receipt" button carries the count, so the
manager sees there is something to do without going looking.

**Acceptance criteria**
1. `/invoices/` lists every invoice with status `conflict`, newest first, each
   linking to its detail page and naming why it needs checking.
2. When at least one invoice needs checking, the Deliveries page's invoice
   button shows the count; with none, it reads as it does today.
3. Both lists are scoped to `request.user.organisation` — another practice's
   invoices never appear, asserted by a two-org test.

**UI note.** Rows follow the list pattern in `ui-design`: hairline dividers, a
`status-week` chip for "Needs checking", name and reason stacked at 390px. No
new component.

**Sample invoice mapping.** What makes an invoice a conflict:

```python
# stock/invoices.py check_invoice()
invoice.status = (Invoice.Status.PARSED
                  if invoice.totals_ok and invoice.supplier_id
                  else Invoice.Status.CONFLICT)
# totals_ok is False when any line breaks: abs(qty * unit_price - line_total) > 0.01
# e.g. {"sku": "HS-4471", "description": "Nitrile gloves M 100/box",
#       "qty": 10, "unit_price": "28.50", "line_total": "295.00"}  -> 285.00 expected
```

---

### UX-04 — Dates render in US format

**Labels:** `backend`

**Problem.** `LANGUAGE_CODE = "en-nz"`, but Django ships no `en_NZ` locale —
only `en`, `en_AU`, `en_CA`, `en_GB`, `en_IE`. `en-nz` falls back to `en`,
whose `DATE_FORMAT` is `"N j, Y"`. Confirmed in the running app:

```
bare  : Sept. 18, 2026
j M Y : 18 Sep 2026
DATE_FORMAT: N j, Y
```

Most templates dodge this by passing an explicit format. Two do not, and both
are invoice screens, where the date decides which tax year the invoice lands
in: `invoice_preview.html:8` and `invoice_detail.html:8` render
`{{ invoice.issued_on }}` bare.

**Impact.** A manager checking a read invoice against the paper one sees the
date in the wrong order. 09/11 versus 11/09 is exactly the mix-up that puts an
invoice in the wrong income year, and this is the screen where they are being
asked to confirm the reading is right. It also quietly contradicts the NZ
framing the rest of the app works hard at.

**Fix.** Give Django a real `en_NZ` format module and point
`FORMAT_MODULE_PATH` at it, rather than patching two templates. That fixes both
screens, every Django form and admin widget, and every date added later — the
root cause rather than the two places it currently shows.

Worth recording, because it is the obvious-looking fix that silently fails:
setting `DATE_FORMAT` in `settings.py` does **nothing** while `USE_I18N` is on.
`get_format()` reads the active locale's format module first and only falls back
to the setting if no module defines it, and `en` defines it. Tried that first;
the value did not change.

**Acceptance criteria**
1. `django.utils.formats.get_format("DATE_FORMAT")` returns a day-first format,
   and `{{ some_date }}` with no filter renders as `18 September 2026`.
2. `/invoices/<pk>/` and the invoice preview show the issue date day-first.
3. `DATE_INPUT_FORMATS` accepts `18/09/2026`, so a date typed the NZ way validates.

**UI note.** No template or layout change. Dates get slightly longer in the
preview subtitle, which already wraps.

**Sample invoice mapping.**

```python
# stock/invoices.py already parses NZ-first, so only display was wrong:
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y")
# CSV "invoice_date" -> Invoice.issued_on
{"invoice_date": "18/09/2026"} -> date(2026, 9, 18) -> "18 September 2026"
```

---

### UX-05 — Nothing measures whether any of this works

**Labels:** `backend` `infra`

**Problem.** There is no instrumentation of any kind in StockRoom. No counters,
no events, no funnel. Nobody can answer "do new practices get a stock list?"
or "does anyone ever undo an import?", so a UX change like UX-01 ships on
argument alone and its effect is never seen.

**Impact.** Not felt by the manager directly, but it means every issue in this
document is a guess about severity rather than a measurement, and the next
review starts from scratch.

**Fix.** The smallest thing that answers real questions. StockRoom has a strict
CSP, self-hosted everything and an explicit "nothing about patients" privacy
stance, so a third-party analytics script is the wrong shape. Instead, emit
structured log lines through Python's `logging` — which Django already
configures and Railway already collects — at the points these fixes touch, and
compute the success metric from data the app already stores.

**Success metric: setup completion rate** — the share of practices that get
from signing up to a usable stock list (at least one active item). It is the
one number UX-01 should move, and it needs no new storage.

**Acceptance criteria**
1. `stockroom/analytics.py` emits one structured line per key flow event
   (`setup_step_opened`, `setup_completed`, `invoice_imported`,
   `invoice_undone`, `invoice_needs_checking`, `merge_undone`) carrying the
   organisation id and no personal data.
2. `python manage.py ux_metrics` prints setup completion rate, plus counts for
   invoices needing checking and undo usage, over a `--days` window.
3. No new dependency, no new model, no migration, and no change to any page's
   HTML.

**UI note.** Nothing user-visible. Deliberately: the manager should never pay
for our measurement.

**Sample invoice mapping.**

```python
# stockroom/analytics.py -- emitted after stock/invoices.py ingest()
track("invoice_imported", org=invoice.organisation_id,
      status=invoice.status,        # complete | partial | parsed | conflict
      lines=invoice.lines.count(), received=received, source="batch")
# -> INFO stockroom.analytics {"event": "invoice_imported", "org": 12,
#       "status": "partial", "lines": 14, "received": 9, "source": "batch"}
```

---

### UX-06 — A batch import never says what it did

**Labels:** `frontend` `backend`

**Problem.** The single-file path is careful: read, preview, edit the lines,
confirm, undo. The batch path is not. `/invoices/` says so plainly — "Each one
is imported as it's read, without a preview" — and up to 50 invoices go straight
into the practice's records. The only feedback was a state chip per file, which
says a file was *read*, not what changed because of it. There was no batch-level
undo, so putting a bad run back meant opening each invoice inside its own
ten-minute window.

This is also the path the setup flow pushes hardest ("Read my invoices"), so a
brand-new manager's first action was the least reviewable one in the app.

**Corrected from the first draft of this review.** This entry originally said
every matched line "overwrites the item's price". That is wrong, and the truth
is more interesting. `receive()` already declines to apply a unit price that is
a rise of more than `PRICE_RISE_THRESHOLD` (10%) on what the item costs now,
because a misread photo is likelier than a 3x price rise. Measured on the real
code:

```
tripled      8.50 -> invoice 25.50   item price now 8.50   HELD
small rise   8.50 -> invoice  9.00   item price now 9.00   APPLIED
fall         8.50 -> invoice  4.00   item price now 4.00   APPLIED
```

So the threshold this entry asked for already existed, at a stricter 10% than
the 25% proposed. The actual defect was worse and quieter: **the batch held the
price and never told anyone.** The manager believed the import had repriced the
item, it had not, and there was no way to find that out or to act on it. Adding
a second threshold on top would have been redundant; surfacing the first one was
the fix.

**Impact.** A folder of 50 invoices changed stock levels, order status and some
prices, and the manager had no list of what happened and no way back. A held
price stayed wrong indefinitely because nothing mentioned it.

**Acceptance criteria**
1. A finished batch shows what it changed — lines received, prices held,
   invoices skipped as repeats, invoices needing checking, files that could not
   be read — as a reviewable summary, not just per-file chips.
2. A batch has a single "Undo this batch" action valid while the batch's window
   is open, restoring every invoice in it: stock, order status and prices.
3. A price the import held back is listed with what the practice pays now, what
   the invoice asked, and a one-tap way to accept it.

**Test steps**
1. `python manage.py seed_demo --reset`, sign in as `0000`.
2. Upload 4 CSV invoices at `/invoices/`: one whose unit price is triple the
   item's current price, two ordinary ones, and a duplicate of one of those.
3. On the batch page, confirm the summary reads "3 lines received", "1 price
   held back" and "1 skipped as a repeat", and that the held price is listed
   with both figures.
4. Tap "Use $X from now on" — the item is repriced and leaves the held list.
5. Tap "Undo this whole import" — check `/items/` shows every price as it was,
   `/deliveries/` shows the orders open again, and the undo button is gone.

**UI note.** The batch page already polls with htmx every 2s; the summary is
built only once the run finishes, so mid-run totals do not move under the
manager every two seconds. It replaces the same `#batch-files` fragment, so no
new page. Undo is `btn btn-line` — a secondary action, not a primary one.

**Sample invoice mapping.**

```python
# The held case: line price vs Item.price at ingest time
{"sku": "HS-4471", "description": "Nitrile gloves, size M", "qty": 6,
 "unit": "25.50", "line_total": "153.00"}       # invoice says 25.50
Item.objects.get(supplier_sku="HS-4471").price  # -> Decimal("8.50")
# price_needs_confirming(8.50, 25.50) -> 25.50 > 8.50 * 1.10 -> True
# receive(..., update_item_price=False): the line is still received and the
# order still closes; only Item.price is left alone, and batch_summary()
# lists it as waiting for a decision.
```

---

## Pending

Not implemented in this review. Each one is ready to pick up: acceptance
criteria are observable, and the test steps are what to do in the running app.

### UX-07 — The stock list cannot tell you what is low

**Labels:** `frontend`

**Problem.** `/items/` lists all 44 items alphabetically with name, unit,
supplier and price. No status, no days left, no stripe. Home has all of that
but only shows what needs attention. So "is anything close to running out that
I have not been told about yet?" cannot be answered on the page called Stock;
the manager has to hold Home in their head while scrolling Stock.

**Impact.** Time, not money. The split is defensible — Home is the to-do list,
Stock is the catalogue — but an untrained manager reads "Stock" as "my stock"
and expects the state of it.

**Acceptance criteria**
1. Each row on `/items/` shows its status chip and days-left figure, using the
   same `_status_chip.html` and colours as Home.
2. Rows keep their left status stripe, and status is readable without colour.
3. The search box filters without losing the status on filtered rows.

**Test steps**
1. Sign in as `0000`, open `/items/`.
2. Check "Suction tips, disposable" shows "Out of stock" and "Nitrile gloves,
   size M" shows "Order now", matching Home.
3. Type "gloves" in the search box; confirm the filtered row keeps its chip.
4. Sign in as `11` (assistant) and confirm `/items/` is still 403.

**UI note.** Reuse `stock/_item_row.html` and the `status_color` annotation the
home view already computes; do not recompute the forecast per row.

**Sample invoice mapping.** Not applicable — no invoice data on this screen.
Status comes from `StockEvent` history via `stock/forecast.py`.

---

### UX-08 — No plain English for the words StockRoom invented

**Labels:** `frontend`

**Problem.** The app asks a manager to act on terms it never defines:
"Matched names", "Needs checking", "Nothing received yet", "Already imported",
"Confident" (on the item chart), "Standard order size". The About dialog
explains the product but none of these. The catalogue and matching screens are
where a manager is most likely to stop and not tap anything.

**Impact.** Hesitation on exactly the screens that need a decision. A manager
who does not know what "Matched names" means will not open it, and the merge
review that protects the stock list from duplicates goes unused.

**Acceptance criteria**
1. Every status chip and page title listed above has a one-sentence plain-NZ
   explanation reachable without leaving the page.
2. The explanation is available to keyboard and screen-reader users, with the
   trigger labelled and focus returning to it on close.
3. No new words are introduced by the explanations themselves.

**Test steps**
1. Open `/items/matched/` and confirm the page says, in one sentence, what a
   matched name is and why undoing one matters.
2. Open an invoice with status "Needs checking" and confirm the status is
   explained where it is shown.
3. Tab to each explanation trigger, open with Enter, close with Escape, and
   confirm focus returns to the trigger.

**UI note.** Reuse the existing rounded "i" button and `<dialog>` pattern from
`base.html`; do not invent a tooltip component, which does not work on touch.

**Sample invoice mapping.**

```python
# The terms that need defining map straight to Invoice.Status:
Invoice.Status.PARSED    # "Nothing received yet" -> read, but nothing ticked off an order
Invoice.Status.PARTIAL   # "Partly received"      -> some lines matched an order
Invoice.Status.CONFLICT  # "Needs checking"       -> no supplier, or lines that don't add up
Invoice.Status.IGNORED   # "Already imported"     -> same checksum or number as an earlier one
```

---

### UX-09 — "Match to" assumes you remember what you ordered

**Labels:** `frontend`

**Problem.** When an invoice line matches nothing on order, the preview offers
a "Match to" search over open orders. It is a text box: the manager has to
recall which order the delivery belongs to and type enough of the item name to
find it. For an untrained manager reading a supplier's abbreviated product
description ("NIT GLV M 100BX"), that is a guess.

**Impact.** Lines get left unmatched, so stock is not received and the
back-order stays open forever, which then makes the reorder list wrong.

**Acceptance criteria**
1. With an unmatched line, the open orders from that supplier are listed
   without typing anything, most recent first.
2. Each option shows item name, quantity ordered and order date, so it can be
   chosen without knowing the item name.
3. Choosing one and confirming receives against that order and closes it.

**Test steps**
1. Seed demo data and mark an item ordered from Henry Schein at `/reorder/`.
2. Upload a CSV invoice whose description does not match the item name.
3. On the preview, confirm the Henry Schein open orders are listed before any
   typing, showing name, quantity and date.
4. Pick the order, confirm, and check `/deliveries/` no longer lists it.

**UI note.** The options list already exists as `_order_choices.html`; render it
on load rather than only on search input.

**Sample invoice mapping.**

```python
# The line the manager has to match by hand
{"sku": "", "description": "NIT GLV M 100BX", "qty": 10,
 "unit": "28.50", "line_total": "285.00"}
# Matcher.match() normalises to "100pk glv m nit" -> below UNSURE_FLOOR (0.60)
# against "Nitrile gloves, size M" -> no suggestion, manual match required.
```

---

### UX-10 — A failed invoice read loses the manager's place

**Labels:** `frontend`

**Problem.** Every failure in `assistant/views.py` redirects back to
`stock:invoice_upload` with a toast: a file too large, an unreadable photo, the
daily AI limit hit. The toast is gone in 6 seconds and the upload page looks
exactly as it did before, so a manager who looked away cannot tell whether
anything happened, and the file they chose is gone from the input.

**Impact.** Repeated uploads of the same failing file, each one spending an AI
call against the daily limit.

**Acceptance criteria**
1. A failed read leaves a message on the upload page itself, not only in a toast.
2. The message names the file that failed and what to do instead.
3. Hitting the daily AI limit says when it resets and offers the CSV path.

**Test steps**
1. With `AI_API_KEY` set, upload a 6 MB photo at `/invoices/`.
2. Wait 10 seconds, then confirm the page still explains that the file was too
   large and names it.
3. Set `AI_DAILY_LIMIT=0`, upload again, and confirm the message offers CSV
   import and says when the limit resets.

**UI note.** A `blueprint` panel above the upload form, in `status-week`, using
the same dashed-border treatment as the preview's "Some lines don't add up".

**Sample invoice mapping.** Not applicable — the failure happens before any
line is parsed. The `InvoiceDocument` is still kept, so the bytes are not lost:

```python
# assistant/views.py -- kept before parsing, so a failed read still has its file
invoices.keep_document(org, user, upload.name, mime_type, data)
invoices.parse_invoice(...)   # raises GeminiError -> redirect with a toast
```

---

## Product canonicalisation and dedup

The brief asked for this to be designed and implemented. Most of it already is,
in `stock/matching.py`; this section documents the design as built, and names
the one gap.

**Why it matters.** Invoices from three suppliers call the same box of gloves
three different things. Without canonicalisation the stock list grows a
duplicate per spelling, every duplicate has its own forecast, and the reorder
list becomes untrustworthy.

### The ladder

Each name is tried against the practice's active items in order, first hit wins.
Anything at or above `SURE` is applied and remembered; anything below is a
suggestion a manager confirms.

| Step | Method | Confidence | Applied without asking |
|---|---|---|---|
| 1 | Same supplier code (`supplier_sku` + supplier) | 1.00 | Yes |
| 2 | A name matched before (`ItemAlias`) | 1.00 | Yes |
| 3 | Same normalised name | 1.00 | Yes |
| 4 | Same after synonyms | 0.98 | Yes |
| 5 | Close spelling (fuzzy) | computed | Only at or above 0.95 |
| 6 | Similar meaning (Gemini embeddings) | cosine | Never — always a suggestion |

### Normalisation

`normalize()` produces a key that is identical for names differing only in
case, punctuation, plurals, unit spelling, filler words or word order:

```python
normalize("Nitrile Gloves (Medium) 100/box")  # -> "100pk glove medium nitrile"
normalize("100 per pack nitrile glove, medium")  # -> same key
```

Units are written one way (`5 mls`, `5ml`, `5 millilitres` all become `5ml`),
pack sizes are collapsed (`box of 100`, `100/box`, `x100`, `100pk` all become
`100pk`), stopwords are dropped, words are singularised and then sorted, which
is what makes word order irrelevant. `synonym_key()` runs the same pipeline
with known alternative names swapped for one agreed spelling.

### Thresholds and config

```bash
# .env -- both are floats between 0 and 1
PRODUCT_FUZZY_THRESHOLD=0.85     # at or above: a "likely" suggestion
PRODUCT_EMBEDDING_THRESHOLD=0.80 # cosine at or above: a semantic suggestion
```

```python
# stock/matching.py -- not environment-tunable, deliberately
SURE = 0.95            # at or above this a match is applied without asking
UNSURE_FLOOR = 0.60    # below this a name is not suggested as anything
TIE = 0.02             # candidates this close are a tie, settled by tie-break
SIZE_CLASH = 0.8       # penalty when two names differ only in their numbers
UNDO_DAYS = 30         # how long a remembered match can be undone
```

`SIZE_CLASH` is the one that earns its keep: "Syringe 5ml" and "Syringe 10ml"
spell almost identically and are different products. Any name whose numbers
differ is multiplied down so it can never reach `SURE`.

### Embedding fallback

Only reached when spelling could not settle it, only when `AI_API_KEY` is set,
and **never** allowed to produce a sure match — `Match.band` forces a semantic
hit to "likely" regardless of its cosine score, so a machine-guessed meaning
always gets a human's yes. Up to 5 candidates per unsure name, one batched
Gemini call per invoice, item vectors cached on `Item.name_embedding` and
rebuilt only when the name stops normalising to the cached key. A
`GeminiError` degrades to spelling alone rather than failing the import.

### SKU and supplier tie-break

When several items score within `TIE` of each other, preference is:

1. the item whose `supplier_sku` equals the invoice line's SKU,
2. then the item whose supplier is the invoice's supplier,
3. then the item whose price is closest to the line's unit price.

If more than one candidate survives, confidence is capped just below `SURE`, so
a genuine ambiguity is always asked about rather than guessed.

### Review flow and merge audit

Every applied match is written as an `ItemAlias` row: the raw name, the
normalised key, the method, the confidence, the source invoice, who and when.
Nothing is ever deleted — an undo sets `reverted_at` and `reverted_by`, so the
table is the audit log as well as the cache.

Managers review at **Stock → Check matched names** (`/items/matched/`), which
lists the last 30 days. Undo does three things: marks the alias reverted,
unmatches the invoice lines that alias matched, and records `(key, item_id)` in
`Matcher.not_same` so that name is never matched to that item again. A live
unique constraint on `(organisation, key)` where `reverted_at is null` means a
name can only be one thing at a time.

### The gap

Canonicalisation is per practice, and rightly so — one practice's "gloves" is
not another's. But `CatalogueProduct` is a shared list of what NZ practices
order, and nothing feeds matching results back into it. A name that three
hundred practices have each matched by hand is still matched by hand by the
three hundred and first. Worth doing once there are enough practices for the
signal to mean anything; not worth doing now. Tracked as future work rather
than an issue, because no manager is currently hurt by it.

---

## Requirements from the brief

Where each requested guarantee already lives, and what this review changed.

| Requirement | State | Where |
|---|---|---|
| Idempotent invoice ingest | Already built | `Invoice` unique constraints on `(organisation, checksum)` and `(organisation, supplier, invoice_number)`, enforced in the database so two simultaneous uploads cannot both win |
| Duplicate detection (checksum + force_import) | Already built | `invoices.find_original()`; a repeat saves as `ignored`, and a manager can import anyway by choosing a reason, recorded in `forced_by` / `force_reason` |
| Resumable batch import | Already built | `InvoiceBatchFile.state` per file; `TO_READ` drives the Resume button, so a stopped run continues rather than restarting |
| Parsed preview and edit | Already built | `invoice_preview.html` — quantity, unit price and line total are all editable before anything is saved |
| Clear complete/partial status | Built, **was unfindable** | `Invoice.Status`; findability fixed in [UX-03](#ux-03--invoices-that-need-checking-become-invisible) |
| Undo for merges | Already built | `matching.undo_merge()`, 30 days, reviewed at `/items/matched/` |
| Undo for imports | **Fixed** | [UX-02](#ux-02--undo-on-an-invoice-import-is-gone-in-six-seconds) |
| NZ localisation | Strong, one defect **fixed** | [UX-04](#ux-04--dates-render-in-us-format) |
| Minimal clicks | Already met | Logging is 1-2 taps; the reorder list orders a whole supplier in one |
| Undoability generally | **Complete** | [UX-06](#ux-06--a-batch-import-never-says-what-it-did) closed the remaining hole |
| Analytics + success metric | **Added** | [UX-05](#ux-05--nothing-measures-whether-any-of-this-works) |

## How to re-run this review

```bash
python manage.py seed_demo --reset
python manage.py runserver 8000 --noreload &
python scripts/axe_check.py http://127.0.0.1:8000   # must be 29/29 clean
```

Then walk the app at 390px as `0000` (manager) and `11` (assistant), and as a
practice with no items at all — that last one is where UX-01 was hiding, and
it is not covered by the demo data.
