# Adding an org-scoped view

Every practice's data is private to that practice. These rules keep it that way; the isolation suite in `stockroom/test_isolation.py` checks them.

## Rules

1. **The organisation comes from `request.user.organisation`.** Never from the URL, a form field or a hidden input.
2. **Look everything up through it.** `Item.objects.for_org(org)` for lists, `get_object_or_404(Item.objects.for_org(org), pk=pk)` for one object. Another practice's object is a 404, not a 403, so its existence doesn't leak.
3. **New practice-owned models subclass `accounts.models.OrgOwned`.** That gives them the `organisation` foreign key and `.for_org()`.
4. **Forms never offer another practice's rows.** Leave `organisation` off every form and set it in the view. Scope every dropdown too: a `ModelChoiceField` defaults to `.objects.all()`, so an `Item` form's supplier list must be `Supplier.objects.for_org(org)`, or someone can post another practice's supplier id.
5. **Login is required by default** (`LoginRequiredMiddleware`). Mark a genuinely public view with `@login_not_required` (see `/healthz`).
6. **Manager-only views** use `@admin_required` from `accounts.decorators`. Assistants and platform superusers get a 403.
7. **Assistants never get prices.** Leave them out of the template context when `not request.user.is_org_admin`; hiding them in the template isn't enough.

Django admin is for platform superusers only. Practice admins never see it.

## Registering a view in the isolation suite

In the same PR as the view, add it to the lists at the top of `stockroom/test_isolation.py`:

```python
ORG_OBJECT_URLS = [
    Case("stock:item_detail", make=lambda org: Item.objects.create(organisation=org, ...)),
    Case("stock:item_archive", make=make_item, method="post"),
]
ADMIN_ONLY_URLS = [Case("stock:suppliers")]
ASSISTANT_PAGES = [Case("stock:home"), Case("stock:item_detail", make=make_item)]
```

- `ORG_OBJECT_URLS`: a user from another practice must get a 404.
- `ADMIN_ONLY_URLS`: an assistant must get a 403.
- `ASSISTANT_PAGES`: must render for an assistant with no `$` amount in it.
- `NO_PRACTICE_DATA`: URLs that show no practice data (login, healthz...), with the reason.

A test walks every URL pattern and fails if one isn't in any of these lists.

`make(org)` creates an object owned by `org`; its `pk` fills the URL's `pk`.
