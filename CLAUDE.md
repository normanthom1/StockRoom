# StockRoom

StockRoom is a stock-ordering PWA for NZ dental practices. The roadmap is epic #4, and `StockRoom.html` is the design prototype.

## Audience and design
Dental assistants log stock mid-procedure, wearing gloves and short on time. Managers order between patients.
- Buttons are big: 48px minimum, main actions larger. Logging anything takes 3 taps or fewer.
- The only typing is search. Mistakes are fixed with undo, not confirm dialogs.
- Status is text plus colour. Copy is plain NZ English.

Details: [ui-design](.claude/skills/ui-design/SKILL.md)

## Build and architecture
- Django 6, Postgres, HTMX, Alpine.js, and Tailwind (django-tailwind-cli, no Node), on Railway.
- Pages are server-rendered. HTMX swaps `{% partialdef %}` fragments, and Alpine only holds local UI state. No SPA or API.
- Every row belongs to an Organisation, and users are `admin` or `assistant`. The organisation always comes from `request.user`.
- Each practice has one practice login (email and password) that opens a device; staff then sign in with a 2-digit code. Stock work always runs as a staff member (`accounts/middleware.py`).
- Stock is an append-only `StockEvent` log. On-hand and forecasts are calculated, never stored.
- New views follow [org-scoped-view](.claude/skills/org-scoped-view/SKILL.md). Test with `python manage.py test`.

## Workflow
- Each issue names a model and effort. If yours is different, say so.
- **After completing any issue, commit the code, merge to main and close the issue.** Follow [finish-issue](.claude/skills/finish-issue/SKILL.md).
