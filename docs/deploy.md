# Deploying to Railway

`railway.toml` in the repo root defines the build, migrate and start steps. Railway picks it up automatically once the service is connected to this GitHub repo, so provisioning ([#8](https://github.com/normanthom1/StockRoom/issues/8)) is mostly setting the environment variables below and attaching a Postgres service.

## Environment variables

| Variable | Required | Example | Notes |
|---|---|---|---|
| `SECRET_KEY` | Yes | `django-insecure-...` (generate a real one) | `python -c "import secrets; print(secrets.token_urlsafe(50))"`. Never reuse the local dev key. |
| `DEBUG` | Yes | `0` | Anything other than `1`/`true`/`yes`/`on` (case-insensitive) is treated as off. Must be `0` in production: it turns on secure cookies and `SECURE_PROXY_SSL_HEADER`, and it switches static files to Whitenoise's manifest storage, which needs `collectstatic` to have already run. |
| `ALLOWED_HOSTS` | Yes | `stockroom.up.railway.app` | Comma-separated. Defaults to `localhost,127.0.0.1` if unset, which is wrong in production. |
| `CSRF_TRUSTED_ORIGINS` | Yes | `https://stockroom.up.railway.app` | Comma-separated, full origin with scheme. Logins and any POST fail with a 403 without this. |
| `SIGNUP_ENABLED` | No | `0` | Defaults to on. `0` hides and 404s `/accounts/signup/`, e.g. on the public demo. |
| `DATABASE_URL` | Yes | `postgres://...` | Railway sets this automatically once a Postgres service is attached. Falls back to a local SQLite file when unset, which is only fine for local dev. |

`PORT` is set by Railway itself; the start command in `railway.toml` reads it. Don't set it manually.

## Email

There's no email provider yet (see [#13](https://github.com/normanthom1/StockRoom/issues/13)), so email is printed to stdout as plain text, which on Railway means the deploy logs. To fetch a password reset link someone asked for:

```bash
railway logs --service StockRoom --deployment --lines 1000 | grep -A 12 "Subject: Reset your StockRoom password"
```

Railway's Hobby plan blocks outbound SMTP, so a real provider will need an HTTPS API (e.g. Resend via django-anymail), not SMTP settings.

## Healthcheck

`GET /healthz` runs a trivial database query and returns `200 ok`. Railway is configured to poll it (`healthcheckPath` in `railway.toml`); a database that's down or mid-migration shows as unhealthy rather than the app looking up.

## First deploy

See [#8](https://github.com/normanthom1/StockRoom/issues/8) for creating the Railway project itself (a one-off, human step).

## Deploying changes

Pushes to `main` redeploy automatically only if the **Railway GitHub App** has access to this repo (GitHub → Settings → Applications → Railway → Configure → Repository access). Without it, Railway can still build the public repo but never hears about pushes, so deploy by hand:

```bash
railway redeploy --service StockRoom --from-source -y
```

Use `--from-source`. Plain `railway redeploy` reuses the previous build and its environment snapshot, which kept a stale `ALLOWED_HOSTS` alive during #8.

Migrations run at container start (`startCommand` in `railway.toml`), before gunicorn. A new container only gets traffic once `/healthz` passes, so a migration that fails leaves the previous deployment serving.
