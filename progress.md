# Epic #4 progress: issues 17-29

Tracks work so a fresh session can resume. Update after each issue closes.

## Status

| # | Title | Status |
|---|-------|--------|
| 17 | Demo organisation seed command | done |
| 18 | App shell: nav, toast, bottom sheet | done |
| 19 | Home: what to order today | todo |
| 20 | Log usage: search, quick taps, undo | todo |
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
