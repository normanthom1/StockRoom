# Epic #4 progress: issues 17-29

Tracks work so a fresh session can resume. Update after each issue closes.

## Status

| # | Title | Status |
|---|-------|--------|
| 17 | Demo organisation seed command | done |
| 18 | App shell: nav, toast, bottom sheet | done |
| 19 | Home: what to order today | done |
| 20 | Log usage: search, quick taps, undo | done |
| 21 | Item detail: forecast + usage chart | todo |
| 22 | Stocktake: record shelf counts | todo |
| 23 | Supplier management | todo |
| 24 | Item management and CSV import | todo |
| 25 | Reorder list and placing orders | todo |
| 26 | Deliveries: receive incoming orders | todo |
| 27 | Spending reports | todo |
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
  for #21/#22/#25 rather than re-querying events/order_lines per item.
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
