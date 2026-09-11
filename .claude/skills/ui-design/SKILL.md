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
| Order this week | `#b3701a` |
| OK | `#2f7d4f` |

Primary colour `#5980a6`, background `#faf9f5`. Use the Tailwind theme tokens, not hex values in templates.

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
