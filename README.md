# TechNest Support: Autonomous Escalation Agent

**Live demo:** https://technest-support.onrender.com (free tier: the first load can take about a minute to wake up)

An agentic customer-support system for a retail shop. A customer raises a complaint, an AI agent routes it to the right
person, proposes a fix **within that person's authority**, and keeps escalating to the next senior until the customer
confirms they are satisfied.

## How it works

| Level | Person | Authority |
|---|---|---|
| L1 | Customer Service Executive | Refund up to Rs 2,000 (within 7 days), defect replacement, free in-warranty repair, 5% coupon |
| L2 | Store Manager | Refund up to Rs 15,000, replacement within 30 days, loaner device, 15% discount |
| L3 | Regional Manager | Unlimited refund/replacement, compensation up to Rs 50,000, policy exceptions, investigations |

**The agent loop:** understand the complaint -> route to a starting level -> check power limits -> make the best allowed
offer -> if rejected, timed out or beyond authority, escalate with full history -> repeat until the customer accepts.

## Features

- **Autonomous escalation** L1 -> L2 -> L3, with SLA timers (2h / 4h / 24h) that escalate stalled cases automatically
- **Hard policy engine:** authority limits are enforced in code, never delegated to a language model
- **Negotiation chat:** the customer can counter-propose ("I want a replacement instead") and the agent re-plans
- **Explainable decisions:** every offer shows *why* it was made
- **Priority scoring**, **VIP loyalty perks** and **refund-abuse detection** from per-customer history
- **Email OTP verification** (MX/typo/disposable-domain checks + a real emailed code), so nobody can open cases under someone else's address
- **Email notifications** for every offer, escalation and resolution
- **Staff dashboard** with live stats, search and filters
- Animated, responsive UI with dark-mode support

## Tech stack

Python, FastAPI, vanilla JavaScript, PostgreSQL (Neon), SMTP / Brevo email API, dnspython, deployed on Render. Optional: Anthropic Claude for richer complaint understanding
and message wording (the system works without it using built-in heuristics).

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in your SMTP details (Gmail app password works)
python -m uvicorn app:app --port 8000
```

Open http://127.0.0.1:8000. Without SMTP settings, verification codes cannot be emailed. For local UI preview only, set
`ALLOW_DEV_OTP=1` in `.env`. To protect the staff dashboard, set `STAFF_PIN` in `.env`.

## Deploy (Render, free)

1. Create a free [Brevo](https://www.brevo.com) account, verify a sender address and create an API key (SMTP & API -> API keys).
   Render's free tier blocks SMTP ports, so the app uses Brevo's HTTPS API there.
2. On [Render](https://render.com): New -> Blueprint -> select this repo. `render.yaml` configures everything.
3. When asked, enter `BREVO_API_KEY`, `BREVO_SENDER` and `PUBLIC_URL` (your `https://<name>.onrender.com` address).
4. **Persistent data (recommended):** Render's free disk is ephemeral, so create a free [Neon](https://neon.tech) Postgres
   database and add its connection string as the `DATABASE_URL` environment variable. Cases are then stored in Postgres
   and survive restarts and deploys. Without `DATABASE_URL` the app falls back to a local JSON file.
5. **Staff dashboard:** `render.yaml` sets `DASHBOARD_OPEN=1` (public demo: open dashboard, customer emails masked).
   Remove that variable and set `STAFF_PIN` to protect the dashboard with a PIN instead.

Notes: the free tier sleeps after ~15 minutes idle (first load takes ~1 minute). Pushing to GitHub does not auto-deploy
on this setup, so use Render -> Manual Deploy (and Blueprint -> Manual sync for new environment variables).
Abuse protection: per-IP and global rate limits on OTP and case creation.

## Project layout

```
agent.py      escalation agent: analysis, policy engine, offers, negotiation
app.py        FastAPI server: auth, OTP, cases, notifications, stats
store.py      case storage: PostgreSQL (DATABASE_URL) or JSON file
mailer.py     email sending: SMTP or Brevo HTTPS API
emailcheck.py email address screening
static/       single-page UI (customer panel + staff dashboard)
```
