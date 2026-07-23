# Atomic Bot

Zarinpal callbacks go through Caddy HTTPS proxy to the bot.

Public URLs:
- https://bot.atomicshop.ir/payment/callback
- https://bot.atomicshop.ir/payment/wallet-callback

DNS: create A record name=bot pointing to your VPS IP.

Then:
docker compose up -d --build
curl https://bot.atomicshop.ir/health

Admin panel and deployment guide: [ADMIN_PANEL.md](ADMIN_PANEL.md)
