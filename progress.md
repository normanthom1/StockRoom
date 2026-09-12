# Epic #4 progress: issues 17-29

Tracks work so a fresh session can resume. Update after each issue closes.

## Status

| # | Title | Status |
|---|-------|--------|
| 17 | Demo organisation seed command | done |
| 18 | App shell: nav, toast, bottom sheet | done |
| 19 | Home: what to order today | done |
| 20 | Log usage: search, quick taps, undo | done |
| 21 | Item detail: forecast + usage chart | done |
| 22 | Stocktake: record shelf counts | done |
| 23 | Supplier management | done |
| 24 | Item management and CSV import | done |
| 25 | Reorder list and placing orders | done |
| 26 | Deliveries: receive incoming orders | done |
| 27 | Spending reports | done |
| 28 | Activity log and CSV export | todo |
| 29 | PWA manifest, icons, installability | todo |

## Notes for resuming
- Issue 18 added stub "coming soon" views/URLs for pages later issues own:
  `stock:log_usage` (#20), `stock:reorder_list` (#25), `stock:deliveries` (#26),
  `stock:items` (#24), `stock:suppliers` (#23), `stock:spending` (#27). Replace
  the view body when implementing that issue; keep the URL name (base.html's
  nav and test_isolation.py already reference it).
- Shared UI primitives from #18, reuse rather than re-inventing:
  - Toast: server code sets `response["HX-Trigger"] = json.dumps({"toast":
    {"message": ..., "undo_url": ...}})` (see `stock/views.py::_toast_response`).
    Undo buttons that fire another such response must go through
    `htmx.ajax(...)`, not a plain `fetch()` - fetch doesn't process HX-Trigger.
  - Bottom sheet: `hx-target="#sheet-content" hx-swap="innerHTML"` on any
    `hx-get`/`hx-post` opens it automatically (base.html listens for
    `htmx:afterSwap` on `#sheet-content`). Close with
    `document.getElementById('sheet').close()`.
- After editing any template class names, `python manage.py tailwind build
  --force` before eyeballing in a browser - plain `runserver` does not rebuild
  CSS on its own (needs `manage.py tailwind runserver` for that).
- Issue 19 added `stock/humanize.py` (plain-English formatting: run-out
  estimates, order-by dates, qty/rate text) - reuse it in item detail (#21),
  reorder list (#25) and anywhere else that shows a forecast to a person.
  `round()` is banker's rounding in Python (`round(0.5) == 0`) - `format_rate`
  already guards against this; watch for the same trap elsewhere.
- Status colours (out/now/week/ok) are chosen by name in Python, so Tailwind's
  static scanner never sees `bg-status-out` etc. written in a template -
  `src/tailwind.css` force-generates all `{bg,text,border}-status-*` utilities
  via `@source inline(...)`. Any *new* dynamic Tailwind class needs the same
  treatment or it silently won't exist in the built CSS.
- `{% partialdef name %}` alone only *defines* a fragment; add `inline` (`{%
  partialdef name inline %}`) or it won't render on the full-page request -
  bit us once on home.html's rows block.
- Added `stock:item_detail` (`/item/<pk>/`) as a "coming soon" stub for #21 to
  replace, same pattern as the #18 stubs.
- Deleted the original scaffold `ping`/`bump` demo views (their job - proving
  CSRF/HTMX wiring - is now done by real views); kept `demo_toast`/`demo_sheet`
  from #18 but detached their fragment from home.html into their own
  `stock/templates/stock/demo_sheet.html`.
- Issue 20's "redirect then toast" pattern (used by any action that should
  land back on another page, not swap in place): create the StockEvent, call
  `messages.success(request, text, extra_tags=undo_url)`, then respond with
  `HttpResponse(status=200)` plus an `HX-Redirect` header set to the target
  URL. base.html's static Django-messages loop already renders an Undo button
  when `message.extra_tags` is set (plain `hx-post`, no Alpine needed since
  it's server-rendered HTML htmx auto-processes on load - only *dynamically*
  inserted nodes, like the Alpine `x-for` toast list, need `htmx.ajax(...)`
  instead of `hx-post`). Reuse this for #22/#25/#26's own undo-within-window
  actions rather than reinventing it.
- `_org_items(org)` / `_forecast_for(item, now)` in stock/views.py are the
  shared per-item forecast helpers home and log-usage both use - reuse them
  for #22/#25 rather than re-querying events/order_lines per item.
- Issue 21 added `stock/chart.py` (`usage_chart_svg`) - server-rendered inline
  SVG bar chart, no JS library. Excluded weeks render with a diagonal hatch
  fill (`url(#excluded-week-hatch)`), not just faded opacity - matches the
  issue's literal wording, check for it by string if a future issue needs to
  assert the chart flags a week.
- `forecast.weekly_consumption` now takes an optional `window_weeks` (default
  8, same as before); `forecast.drop_outliers` is now built on a new
  `forecast.outlier_mask(weeks) -> list[bool]`, which is what the chart uses
  to know *which* weeks to hatch (drop_outliers alone only gives a count).
  Both are newest-first like weekly_consumption; reverse before charting.
- `stock/humanize.py` also gained `build_sentence`, `build_caveat`,
  `format_rate_sentence`, `CONFIDENCE_LABEL` - the item detail page's copy.
  Reuse for #25/#27 rather than re-deriving forecast copy.
- Added `Item.pinned_to_reorder_at` toggle (`stock:item_toggle_reorder`) and
  price/order_size inline edit forms on item detail - #24's item edit form and
  #25's reorder list both build on the same `pinned_to_reorder_at` field.
- `stock:item_count_sheet` is a "coming soon" stub sheet for #22 to replace
  (same incremental pattern as #18's stubs).
- Issue 22 replaced that stub with the real count sheet, and gave `stock:items`
  (the "Stock" admin page, still otherwise a stub for #24) a real "Start a
  full stocktake" entry point at `stock/stock_page.html` - #24 should keep
  that button when it builds out the full item list on the same page/URL.
  A quick "Count" button was also added to home's "other items" fine-rows
  list (admin only), opening the same shared sheet.
  `stock/templates/stock/coming_soon_sheet.html` (the #21 stub) is deleted -
  no longer referenced anywhere.
- Full stocktake mode (`stock:stocktake_step`/`stocktake_save`) tracks
  progress in `request.session["stocktake"]` (`{item_ids, index}`) rather than
  a DB model - resuming is just revisiting `/stocktake/`, no extra state to
  design around. Items are walked in name order. This is a plain (non-htmx)
  form/redirect flow, unlike the sheet actions - a full per-step page reload
  is fine for a deliberate walk-the-shelf task and avoids HX-Redirect vs.
  htmx-follows-3xx-transparently footguns.
- Issue 23 added `Supplier.is_active` (migration 0002_supplier_is_active) -
  suppliers archive (hide, don't delete) only when they have no active items.
  Added `stock/forms.py` (`SupplierForm`), the app's first ModelForm. Gotcha:
  Django's automatic `validate_unique()` does NOT check a `UniqueConstraint`
  built from an expression (ours is `Lower("name")`) - only plain-field
  constraints. Without a manual `clean_name()` doing `name__iexact`, a
  duplicate name reached the DB as a raw IntegrityError instead of a form
  error. Same trap awaits any future ModelForm on an OrgOwned model with a
  similar case-insensitive-name constraint (e.g. Item in #24).
  Click-to-edit rows swap `#supplier-row-<pk>` via `hx-target`/`outerHTML`;
  archive/unarchive reuse #20's HX-Redirect + `messages` `extra_tags` undo
  pattern, but archive isn't time-limited (a soft is_active flip is always
  safely reversible, unlike a StockEvent).
  Lesson: an edit-mode row with several inputs in one `flex flex-wrap` line
  does NOT wrap on a narrow screen - flex items shrink before wrapping kicks
  in, squeezing text inputs unreadably thin. Stack such rows vertically (or
  give every input an explicit `min-w-*`) instead.
- **Process hygiene**: always pass `--noreload` to a backgrounded
  `manage.py runserver` used for a one-off Playwright check. Without it,
  Django's autoreloader child process survives `jobs -p | xargs kill` (which
  only kills the parent), leaking one orphaned python.exe per check. This
  session accumulated 30+ before `manage.py test` itself started hanging from
  the resource pressure - cleaned up via PowerShell
  `Get-CimInstance Win32_Process | Where CommandLine -like '*runserver*' |
  Stop-Process -Force`. See memory feedback_runserver_noreload.md.
  Also: if `runserver ... &` followed by more commands in the SAME Bash call
  ever hangs with no output (even after `--noreload`), add `disown` right
  after backgrounding it and split the follow-up curl/check into a separate
  Bash tool call - a single compound call seems to sometimes wait on the
  background job's job-table entry.
- Issue 24 replaced `stock/templates/stock/stock_page.html` (deleted) with
  the real `stock/templates/stock/items.html` - full admin item list with
  search (same `hx-get`+debounce pattern as #20's log-usage tiles), an
  ItemForm add form, click-to-edit rows (`_item_row.html`, same
  `#item-row-<pk>`/outerHTML pattern as #23's supplier rows - remember to
  stack multi-field edit rows vertically, not `flex flex-wrap` on one line),
  and archive/unarchive reusing the same HX-Redirect + Undo pattern. The
  "Start a full stocktake" button from #22 and a new "Import a CSV" button
  both live at the top of this same page.
  `ItemForm` (new, in `stock/forms.py`) scopes its `supplier` ModelChoiceField
  to `Supplier.objects.for_org(...)` in `__init__` - tested explicitly
  (posting another org's supplier pk must fail, not silently succeed).
  New `stock/csv_import.py` (`parse_csv`) is pure and framework-free (stdlib
  `csv` module, ladder rung 3) - parses to `ImportRow` dataclasses with
  per-row `errors`; nothing touches the DB until `item_import_confirm`.
  The parsed *valid* rows are stashed in `request.session["pending_import"]`
  between the preview and confirm POSTs (plain JSON-safe dicts, not the
  dataclasses/Decimals directly) - same "stash across a preview/confirm
  round trip" shape as #22's stocktake session state.
- Issue 25 added the reorder list: `_wanted_items(org, now)` (status in
  {OUT, ORDER_NOW, ORDER_THIS_WEEK} or `pinned_to_reorder_at` set) grouped by
  supplier. Placing an order is just `OrderLine.objects.create(...)` - no new
  Item field needed, since `forecast()` already reports `ON_ORDER` the moment
  the item has an open order line (`item.order_lines` non-empty & `is_open`).
  Mailto body reuses `accounts.mailto.build_mailto_link`, signed with
  `admin.name` and `org.name` (not the prototype's literal "STOCKROOM").
  Per-line "Mark ordered" and the supplier's "Mark all ordered" bulk button
  share one screen's inputs without nested `<form>`s: each qty `<input
  name="qty_<item_id>" data-supplier="<supplier_id>">`, the line button
  `hx-include="[name='qty_<id>']"`, the bulk button
  `hx-include="[data-supplier='<id>']"` - htmx includes whatever the selector
  matches regardless of form nesting.
  Undo reuses the `HX-Redirect` + `messages.extra_tags` pattern, but a bulk
  "mark all ordered" needs to undo *several* OrderLines from one toast: its
  undo URL is `reverse("stock:reorder_undo_batch") + "?ids=1,2,3"` (a query
  string on a POST works fine - Django puts query params in `request.GET`
  regardless of method). Unlike #20's StockEvent undo, any admin (not just
  the one who ordered) can undo within the 10-minute window - ordering is a
  team action, not a personal one.
  `assertNotIn("$", content)` is too broad a "no price leaked" check - it
  also flags Alpine's `$event` in base.html's toast wiring. Use
  `assertNotRegex(content, r"\$\s?\d")` (same pattern test_isolation.py's
  `PRICE` regex already uses) instead.
- Issue 26 (deliveries) uses plain (non-htmx) forms, unlike reorder/log-usage -
  no undo is required for receiving, so a normal POST+redirect+`messages` is
  simplest. One `<form>` per supplier group wraps every open line; both the
  per-line "Receive" and the group's "Receive all" are `type="submit"` buttons
  with distinct `name`/`value` pairs (`receive_line`=order pk /
  `receive_all`=supplier pk) - `delivery_submit` checks which key is in
  `request.POST` to tell single vs. bulk apart, no nested forms needed.
  **Real bug caught only by a browser check, not by unit tests**: the
  double-submit guard `onsubmit="this.querySelectorAll('button').forEach(b =>
  b.disabled = true)"` disabled the clicked submit button *before* the
  browser serializes the form, and a disabled control's name/value pair is
  dropped from the submission per the HTML spec - so `receive_line` never
  reached the server and every receive silently no-opped. Fix: defer the
  disabling with `setTimeout(fn)` (next tick) so serialization has already
  happened. Django's test client builds POST bodies directly in Python and
  never exercises the browser's form-serialization step, so this class of bug
  is invisible to `manage.py test` no matter how thorough the suite is - a
  real Playwright click-through is the only thing that catches it. Same risk
  exists in any other form using this pattern; #22/#24's plain admin forms
  don't have JS submit handlers so they're unaffected, but check this first
  if a future plain-form feature adds one.
  Partial receipt: the ORIGINAL OrderLine's `qty` stays as originally ordered;
  `received_qty`/`received_at`/`received_by` record what actually arrived
  (closing it), and any shortfall becomes a *new* open OrderLine (same
  `ordered_at`/`ordered_by`/`expected_at`) so it stays visible as a backorder
  - don't mutate `qty` down to the received amount, the model's fields are
  already shaped for this split.
  `humanize.order_by_text` and the new `humanize.arriving_text` ("Arriving
  ~Thu") now share a `_day_bucket(date, today)` helper - reuse it for any
  future "N days from now" copy rather than re-deriving the weekday/date
  formatting.
  `_median_lead_days(supplier)` (in views.py) computes the actual
  ordered-to-received median over the last 10 receipts - suggested, never
  applied automatically; `supplier_apply_lead_days` is the explicit "Use
  this" action. Shown on the supplier row only when it would actually change
  something (`actual_lead_days != lead_days`).
- **Process hygiene, continued**: `disown` (used to dodge the runserver-hang
  bug above) removes the process from bash's job table, so a later `jobs -p |
  xargs kill` finds nothing and kills NOTHING - the old server keeps running
  and silently keeps answering with stale code after every subsequent restart
  attempt on the same port, making template fixes look like they "didn't
  take" no matter how many times you edit and restart. Always verify with
  the PowerShell one-liner (`Get-CimInstance Win32_Process -Filter
  "Name='python.exe' or Name='python3.12.exe'" | Where-Object { $_.CommandLine
  -like '*runserver*' }`) before concluding a fix isn't working, and kill via
  `Stop-Process -Force` there rather than bash job control once disown is in
  play. Full detail in memory feedback_runserver_noreload.md.
- Issue 27 added `stock/spending.py` (`period_bounds`, `previous_period_bounds`)
  - pure calendar-period date math, deliberately DB-free so it's testable by
  hand-calculation alone (see `test_spending_periods.py`). "Actual spend" =
  `OrderLine`s not cancelled, `ordered_at` in the period, `qty * unit_price`
  (lines with no `unit_price` are excluded from the sum, not treated as 0).
  The 3-prior-periods comparison is hidden unless the organisation's
  *earliest* OrderLine predates the start of that 3-period window - a proxy
  for "enough history exists" rather than checking each bucket has data,
  which avoids a misleading comparison for a brand-new practice.
  Lesson from a flaky-then-fixed test: a fixture line added to keep "outside
  the current period" out of the *actual* total can just as easily land
  inside the *comparison* window and quietly skew that average too - when a
  test spans two separate date-filtered aggregations, place stray fixture
  dates outside BOTH windows (e.g. next period, not "yesterday"), not just
  the one the test currently in view is checking.
  "Estimated ongoing spend" reuses `_org_items`/`_forecast_for` (weekly_usage
  x price x `spending.PERIOD_WEEKS[period]`), counting items with no price
  set rather than skipping them silently.
- Each issue: branch `issue-<N>-<slug>` off main, implement, `python manage.py test` +
  `makemigrations --check --dry-run` + `manage.py check` + `ruff check .`, commit,
  PR with `gh pr create --fill`, merge `--squash --delete-branch`, confirm issue closed.
- Follow `.claude/skills/finish-issue`, `org-scoped-view`, `ui-design`.
- Prototype data mined from StockRoom.html (single long line, use grep -o, not Read):
  - `ITEMS` (7 items shown on home) + `FINE_ITEMS` (37 "fine" items) = 44 items total.
  - `prices:{...}` only covers the 7 ITEMS (suction 0.20, gloves 8.50, ligno 0.95,
    composite 32.00, prophy 0.35, pouches 0.12, barrier 18.00). FINE_ITEMS have no
    prototype prices — seed_demo invents plausible ones.
  - Suppliers: Henry Schein lead=5 phone='0800 807 707' email=orders@henryschein.co.nz;
    Dentsply lead=7 phone='0800 335 626' email=orders@dentsply.co.nz.
  - Demo users in prototype: Sandy (Reception/admin), Johanna + Liz (assistants).
    Issue 17 also wants a "Practice Owner" admin - not in prototype, invented.
