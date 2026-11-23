---
title: ShopBot API
emoji: 🛍️
colorFrom: green
colorTo: yellow
sdk: docker
app_port: 7860
pinned: false
---

# ShopBot API

The agent API behind the Bazaar Lane store: `POST /chat`, `GET /health`, `/products`, `/me/*`, `/admin/*`.

Set these in **Settings → Variables and secrets** (as secrets): `ANTHROPIC_API_KEY`, `DATABASE_URL`, `ADMIN_TOKEN`.
Optional: `CORS_ORIGINS` (your Vercel URL), `MODEL`, `EFFORT`.

Source and docs: see the GitHub repo's README.
