---
name: org-scoped-view
description: The pattern for adding or changing any StockRoom Django view, URL or HTMX endpoint so it stays tenant-isolated and role-checked. Use for every new page, fragment, form or POST action.
---

# Adding an org-scoped view

The helpers live in `accounts.models` (`OrgOwned`, `for_org()`) and `accounts.decorators` (`admin_required`). The human version of these rules is `docs/org-scoped-views.md`. If the code doesn't match this skill, the code is right: update this skill.

## Rules
1. **The organisation comes from `request.user.organisation`, never from the URL, a form or a hidden field.**
2. **Always look objects up through the org:** `get_object_or_404(Item.objects.for_org(org), pk=pk)`. An object from another organisation returns 404, never 403.
3. **Login is required by default** (`LoginRequiredMiddleware`). Mark public views with `@login_not_required` (from `django.contrib.auth.decorators`).
   `request.user` in a stock view is always a person: `PracticeLoginMiddleware` keeps a practice login on its own (before anyone enters a code) to `/accounts/`. A view the practice login itself needs, like staff management, goes under `/accounts/`.
4. **Manager-only views** use `@admin_required`. Don't put prices in an assistant's template context at all (check `request.user.is_org_admin`). Hiding them in the template isn't enough.
5. **Forms:** never include `organisation` as a field (set it in the view), and scope every `ModelChoiceField` queryset with `for_org(org)`. A default `.objects.all()` dropdown lets someone post another practice's row id.
6. **New practice-owned models** subclass `accounts.models.OrgOwned`.
7. **Changes are POST only** (`@require_POST`). CSRF is handled by `hx-headers` on `<body>`.
8. **Stock changes create a `StockEvent`.** Never edit a quantity in place. Undo means deleting the user's own event within 10 minutes.

## HTMX
- One view serves both the full page and the fragment:
  ```python
  template = "stock/home.html#rows" if request.headers.get("HX-Request") else "stock/home.html"
  return render(request, template, ctx)
  ```
- Swappable parts go in `{% partialdef rows %}` inside the page template, not in separate fragment files.
- A toast is a Django message; its Undo button posts to the URL in `extra_tags`:
  ```python
  messages.success(request, "Logged: running low on gloves", extra_tags=reverse("stock:log_undo", args=[event.pk]))
  ```
  Then reload the page the tap came from (`response["HX-Refresh"] = "true"`) rather than sending people somewhere else, so a run of taps stays one tap each. An undo view does the same through `_toast_response(request, "Undone.")`, so the page stops showing what was undone.
- If a form is invalid, re-render the form partial with **status 200**. htmx doesn't swap 4xx responses by default.
- Use `hx-disabled-elt="this"` on buttons that change data, so a double tap doesn't submit twice.

## Tests (required)
- Register the URL in the lists at the top of `stockroom/test_isolation.py`, in the same PR:
  - `ORG_OBJECT_URLS`: another practice's user gets 404. `Case("stock:item_detail", make=make_item)`, add `method="post"` for actions.
  - `ADMIN_ONLY_URLS`: an assistant gets 403.
  - `ASSISTANT_PAGES`: renders for an assistant with no `$` amount.
  - `NO_PRACTICE_DATA`: only for URLs with no practice data at all, with a one-line reason. An unregistered URL fails the suite.
- Add one test for the view's own behaviour.
- Signing in as staff in a test: `force_login` a staff member on their own and the middleware signs them out, because no practice login opened the session. Use `force_login(practice_login)`, then `POST /accounts/code/` with their code (see `accounts.tests.PracticeLoginTests`). Or use an email user from `create_user()`, which works on its own.
