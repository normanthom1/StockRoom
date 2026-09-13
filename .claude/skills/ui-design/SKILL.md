---
name: ui-design
description: StockRoom UI and copy rules for busy, gloved dental staff. Use when building or changing any template, component, button, form, toast or user-facing text.
---

# StockRoom UI design

## Who uses it
- **Dental assistants** log stock mid-procedure. They're wearing gloves, often using one hand, with seconds to spare.
- **Practice managers and owners** order and count stock between patients, on a phone or the reception PC.

Design for the assistant first. If they can't do it fast, they won't log it, and the forecast falls apart.

## Rules
- **Tap targets:** 48×48px minimum (`min-h-12 min-w-12`), with 8px or more between them. Main actions are 64px or taller and full width on a phone.
- **Speed:** logging anything takes 3 taps or fewer. The only typing is search. Numbers use `inputmode="numeric"`, with the unit shown next to the field.
- **No confirm dialogs.** Do the action, then show a toast with Undo (see [org-scoped-view](../org-scoped-view/SKILL.md)).
- **One main action per screen**, placed where a thumb reaches it. Item actions open in the shared bottom sheet.
- **Mobile first** at 390px wide. Check that it still works on a desktop.
- **Text size:** body text 16px or larger, and inputs at least 16px so iOS doesn't zoom. Key numbers (days left, counts) should be large.
- **Roles:** hide anything a role can't do rather than showing it disabled. Assistants never see prices.

## Status colours
Show status as a text chip plus colour, never colour alone. Rows also get a coloured left stripe.

| Status | Colour |
|---|---|
| Out of stock | `#7a1d18` |
| Order now | `#b3261e` |
| Order this week | `#8a5514` |
| OK | `#2f7d4f` |

Primary colour `#54789c`, background `#f2f2f3`. Use the Tailwind theme tokens (`status-*`, `status-out-tint`, `primary`, `primary-dark`, `primary-tint`, `app-bg`), not hex values in templates.

## Look
The prototype's "blueprint" style: Barlow body text, Barlow Condensed (`font-heading`) for headings, buttons, item names and key numbers, square corners, hairline borders. Tokens and components all live in `src/tailwind.css`. Reuse them rather than restyling per page:

| Need | Use |
|---|---|
| The one main action | `btn-primary` (64px, full width) |
| Other buttons | `btn` plus `btn-line` (outline), `btn-fill` (solid primary), `btn-tonal` (pale blue, secondary actions) or `btn-surface` (grey) |
| Status | `{% include "stock/_status_chip.html" with s=row %}` (needs `status_color`, `solid`, `status_label`), or `chip` for other tags |
| Section label | `kicker` |
| Framed panel (key figure, form, supplier card, empty state) | `blueprint` |
| Percentage bar | `<progress class="bar" value=… max=…>` |
| Dropdown menu | `x-data="dropdown"` wrapper + `menu-item` links |
| Bottom nav tab | `nav-tab` with `aria-current="page"` on the active one |

## Laying out a screen
- **Page header:** a bare `<h1>` (base styles set the font, size and colour), then an optional `text-sm text-gray-600` subtitle. `<main>` already gives 16px side padding; don't add more.
- **Lists** are rows divided by hairlines (`border-b border-gray-200`, with a `border-gray-300` top rule), not boxed cards. Full-width rows with a status stripe break out of the padding with `-mx-4`.
- **Rows stack** at 390px: name (and chip) on the first line, `text-[0.8125rem] text-gray-600` details on the second, then inputs and buttons on their own line. Never squeeze a name, chip, input and button side by side; that's what broke the reorder and supplier screens before.
- **Quantity inputs** are `w-20 text-center`, with the unit beside them in a fixed-width `text-sm text-gray-600` span so the buttons line up row to row.
- **Groups** (by supplier) get an `<h2>` over a `border-gray-300` rule, with the group's actions in a row of equal buttons underneath.
- **Empty states** are a centred `blueprint p-5` message.
- **Radio choices** (Django's `RadioSelect`) are styled as full-width 48px rows in the base layer, so `{{ form }}` needs no extra classes.
- **Grey text** is `gray-500` or darker (these clear 4.5:1 on the page). Inputs keep a white fill and a `gray-500` border so the field edge reads (3:1).
- **No rounded corners.** The radius tokens are 0; `rounded-full` is only for the "i" button.

## Gotchas
- **CSP:** no `style="…"` attributes (use classes, or `<progress>` for widths) and no inline scripts. Alpine is the CSP build, so every Alpine attribute must be a plain property or method name from `static/js/app.js` (`open`, `toggle`), never an expression like `!open`.
- **Status classes built from data** (`bg-status-{{ row.status_color }}`) only exist for the prefixes listed in the `@source inline(...)` line in `src/tailwind.css`. A new prefix (say `ring-`) must be added there, or the class silently does nothing.
- **Units:** `{% load stock_units %}` then `{{ item.unit|plural }}` or `{{ item.unit|plural:qty }}`. Never write `{{ unit }}s` (it gives "boxs").
- **Fonts** are self-hosted in `static/fonts/`. A new weight needs its own `@font-face`, and a font in regular use belongs in `PRECACHE_STATIC` (`stockroom/views.py`) so it works offline.
- **The prototype** `StockRoom.html` is a bundled export and can't be read directly. Its screen markup is a JSON string on the `__bundler/template` line (around line 382): extract it with `json.loads` to see the real layout, spacing and styles.

## Checking your work
- Run `python manage.py tailwind build` after adding classes, and restart `runserver` after editing templates: with `--noreload` it keeps serving the old ones.
- With `DEMO_MODE=1`, the login page has a one-click sign-in to the demo practice (`seed_demo` data), then the code pad: `00` Sandy (manager), `11` Johanna (assistant). Screenshot at 390px wide with Playwright, and look at the pages, not just the tests.
- Run `python scripts/axe_check.py http://127.0.0.1:<port>` before shipping. It must report every page clean.

## Copy
- Plain NZ practice English: "Order by Friday", "Used the last one", "Tell the manager we're low".
- Buttons say what they do. Use sentence case. No em dashes.
- Don't show false precision: "~2 wks" or "6 months+", not "214 days".
- When unsure, check the matching screen in `StockRoom.html`.

## Before calling a screen done
- [ ] Every target is 48px or larger, and the main action works with a gloved thumb
- [ ] The task takes 3 taps or fewer, with no required typing beyond search
- [ ] Status is readable without colour, and contrast meets WCAG AA
- [ ] Toasts use `aria-live`; focus moves into sheets and back out
- [ ] Checked as an assistant and as an admin
