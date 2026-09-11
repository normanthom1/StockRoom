---
name: org-scoped-view
description: The pattern for adding or changing any StockRoom Django view, URL or HTMX endpoint so it stays tenant-isolated and role-checked. Use for every new page, fragment, form or POST action.
---

# Adding an org-scoped view

The org-scoping helpers (`OrgOwned`, `for_org()`, `@admin_required`) are built in issue #11. If the code doesn't match this skill, the code is right: update this skill.

## Rules
1. **The organisation comes from `request.user.organisation`, never from the URL, a form or a hidden field.**
2. **Always look objects up through the org:** `get_object_or_404(Item.objects.for_org(org), pk=pk)`. An object from another organisation returns 404, never 403.
3. **Login is required by default** (`LoginRequiredMiddleware`). Mark public views with `@login_not_required`.
4. **Manager-only views** use `@admin_required`. Don't put prices in an assistant's template context at all. Hiding them in the template isn't enough.
5. **Changes are POST only** (`@require_POST`). CSRF is handled by `hx-headers` on `<body>`.
6. **Stock changes create a `StockEvent`.** Never edit a quantity in place. Undo means deleting the user's own event within 10 minutes.

## HTMX
- One view serves both the full page and the fragment:
  ```python
  template = "stock/home.html#rows" if request.headers.get("HX-Request") else "stock/home.html"
  return render(request, template, ctx)
  ```
- Swappable parts go in `{% partialdef rows %}` inside the page template, not in separate fragment files.
- To show a toast, set a header instead of writing toast markup:
  ```python
  response["HX-Trigger"] = json.dumps({"toast": {"message": "Logged: running low on gloves", "undo_url": url}})
  ```
- If a form is invalid, re-render the form partial with **status 200**. htmx doesn't swap 4xx responses by default.
- Use `hx-disabled-elt="this"` on buttons that change data, so a double tap doesn't submit twice.

## Tests (required)
- Add the URL to the tenant isolation suite from #11:
  - a user from another org gets 404
  - an assistant on an admin view gets 403
  - an assistant's HTML contains no prices
- Add one test for the view's own behaviour.
