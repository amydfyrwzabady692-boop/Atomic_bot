# Atomic Bot — safe VPS deployment

This runbook intentionally operates only on the Atomic Bot Compose project.
Do not use `docker system prune`, broad `docker stop`, or `docker compose down -v`
on a VPS that hosts other bots.

## Required secrets

Create `.env` only on the VPS. Never commit it.

- `BOT_TOKEN`
- `DB_PASSWORD`
- `ADMIN_CHAT_ID` (immutable owner Telegram numeric ID)
- `ZARINPAL_MERCHANT_ID`
- `G2BULK_API_KEY`
- card-transfer values, domain, and callback base

Use `chmod 600 .env`.

## Pre-deploy checks

1. Locate the existing Atomic Bot directory and Compose project.
2. Record `docker ps --format ...` and do not modify unrelated containers.
3. Back up the Atomic PostgreSQL volume/database.
4. Run `docker compose config --quiet`.
5. Build only the Atomic bot image.

## Deployment

From the confirmed Atomic Bot directory:

```sh
docker compose build bot
docker compose up -d --no-deps bot
docker compose ps
docker compose logs --tail=200 bot
curl -fsS http://127.0.0.1:8080/ready
```

Start/recreate the `db` service only if inspection confirms this repository
already owns `atomic_bot_db`. The bot runs idempotent schema migrations on
startup, so a new database volume is not required for these changes.

## Rollback

Keep the previous Git commit and image tag before deployment. If readiness
fails, restore the previous Atomic Bot image/commit only; do not restart or
remove containers belonging to other projects.
