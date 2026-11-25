# NSE Scanner Hub

One website that brings five stock-detection tools together under three headings:

| Heading | Tools |
| --- | --- |
| **Patterns** | NSE 500 Pattern Radar (chart + candlestick patterns), Triangle Breakout |
| **Supply & Demand** | SD-Radar (multi-timeframe zones), ZoneDesk (trade-ready setups) |
| **Volume** | Nifty 50 Volume Delta (buy/sell pressure) |

Base market data comes from **Yahoo Finance** (NSE feed, ~15 minutes delayed, no API key).
You can optionally connect a **Kotak Neo Trade API** credential to refresh live last-traded prices.

---

## How it works (the short version)

Think of it as a restaurant:

- **Your browser = the customer.** It shows the tables, charts and buttons. It never talks to Yahoo or Kotak directly.
- **The Python server (`app.py`) = the kitchen.** It fetches prices, runs the five detection engines, and hands finished results back to the browser as JSON.
- **Yahoo / Kotak = the suppliers.** The kitchen buys raw ingredients (price candles) from them.

Everything runs in one small Python program using only the built-in standard library — no `numpy`, `pandas` or `yfinance` to install. That is what makes it start reliably on a free host.

---

## Run it on your own computer (one click)

1. Open the project folder:
   `C:\Users\r_vik\Documents\Qoder\2026-11-25\a6fae8f7\nse-scanner-hub`
2. Double-click **`start.bat`**.
3. A black window opens (the server) and your browser opens **http://127.0.0.1:8080** automatically.
4. Pick a heading, choose a universe (Nifty 50 / Nifty 100 / NSE 500 / custom), press **Scan**.

Keep the black window open while you use the site. Close it to stop the server.
You need Python installed (the same Python you already use). No packages to install.

---

## Put it on the internet with Render (free)

Render runs the kitchen on its own computer so anyone can visit your site. The free plan
sleeps after ~15 minutes of no visits and takes ~50 seconds to wake up — that is normal.

### Step 1 — Put the code on GitHub

1. Create a new empty repository at **https://github.com/new** (name it `nse-scanner-hub`, keep it Public or Private — either works). Do **not** add a README there.
2. Upload the contents of the `nse-scanner-hub` folder to that repository.
   - Easiest without a console: on the new repo page click **uploading an existing file**, then drag in everything **except** the `.cache`, `__pycache__` and `engines/data` folders.
   - Make sure these files are included: `app.py`, `marketdata.py`, `universes.py`, `kotak.py`, `requirements.txt`, `render.yaml`, `runtime.txt`, `Procfile`, and the `engines/`, `web/`, `data/` folders.

### Step 2 — Create the Render service

1. Go to **https://dashboard.render.com/** and sign in (you can sign in with GitHub).
2. Click **New +** → **Blueprint**.
3. Connect the `nse-scanner-hub` GitHub repository. Render reads `render.yaml` and proposes a free web service named `nse-scanner-hub`.
4. Click **Apply** / **Create**. Render builds and starts it automatically.
5. When it finishes, Render shows your public address, like **https://nse-scanner-hub.onrender.com**. Open it — that is your live site.

`render.yaml` already sets everything: free plan, Python 3.12.7, start command `python app.py`, and a health check at `/api/health`. You do not need to type any commands.

> If you prefer not to use a Blueprint: **New +** → **Web Service** → pick the repo →
> Runtime **Python**, Build command `pip install -r requirements.txt`, Start command `python app.py`,
> Instance type **Free**, and add environment variable `PYTHON_VERSION = 3.12.7`.

---

## Protect the site with Google login (selected users only)

You can require visitors to sign in with Google and admit only people on your guest list.
The protection **turns on automatically** once you add your Google credentials on Render.
Until then the site is open (which is why your local `start.bat` preview needs no setup).

How it works: visitor → Google sign-in → your server checks their email against your
allowlist → approved users get a signed login cookie; everyone else sees "Access denied".

### Step 1 — Create a Google OAuth client (one time)

1. Open **https://console.cloud.google.com/apis/credentials** (sign in with your Google account).
2. If asked, create a project (any name, e.g. `nse-scanner-hub`).
3. First configure the consent screen: **https://console.cloud.google.com/apis/credentials/consent**
   → User type **External** → fill app name + your email → save. (While in "Testing", add the
   emails you want to allow under **Test users**, or publish the app to allow anyone you list.)
4. Back on **https://console.cloud.google.com/apis/credentials** → **Create credentials** → **OAuth client ID**.
5. Application type **Web application**. Name it `nse-scanner-hub`.
6. Under **Authorized redirect URIs** add exactly:
   `https://nse-scanner-hub.onrender.com/auth/callback`
   (replace `nse-scanner-hub` with your actual Render service name if different).
7. Click **Create**. Copy the **Client ID** and **Client secret**.

### Step 2 — Add the credentials + guest list on Render

1. Open your service on **https://dashboard.render.com/** → **Environment**.
2. Add these environment variables:
   - `GOOGLE_CLIENT_ID` = the Client ID from Step 1
   - `GOOGLE_CLIENT_SECRET` = the Client secret from Step 1
   - `ALLOWED_EMAILS` = the emails allowed in, comma-separated, e.g.
     `you@gmail.com,friend@gmail.com,colleague@work.com`
   - `SESSION_SECRET` = any long random string (Render can auto-generate it).
3. Save. Render redeploys. Now opening your site asks for Google sign-in, and only the
   emails you listed get in.

You can also edit the guest list in the file `data/allowed_users.json` (a JSON array of
emails) instead of, or in addition to, `ALLOWED_EMAILS`. If both are empty, **any** verified
Google account is admitted — so add your emails to actually restrict access.

To change who is allowed later, edit `ALLOWED_EMAILS` on Render (or the JSON file) and save.
To turn protection off, remove `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`.

---

## Optional: connect Kotak Neo for live prices

Yahoo prices are ~15 minutes delayed. To overlay live last-traded prices:

1. In the site header click the **settings (gear)** button.
2. Paste your **Kotak Neo Trade API credential** (the token used as the `Authorization` header for Kotak's `/script-details/1.0` REST API — the same credential the Pattern Radar desktop app uses).
3. Save. The credential is stored **only in your browser** (localStorage) and sent to the server per request to fetch quotes; it is never saved on the server.
4. Use the live-price refresh to update the visible rows.

Notes:
- This requires a valid, current Kotak Neo Trade API credential. It is an optional enhancement; the whole site works without it on Yahoo data.
- True tick-by-tick buy/sell volume delta needs Kotak's live tick stream (a desktop feature). The Volume tool here approximates delta from candle direction and is labelled as an approximation.

---

## What each tool reports

- **Pattern Radar** — classical chart patterns (head & shoulders, double/triple tops & bottoms, triangles, flags, wedges, cup & handle, channels) plus candlestick patterns, across 5m / 15m / 1h / 1d / 1w, each with confidence, direction, breakout/target/stop and risk-reward.
- **Triangle Breakout** — symmetrical / ascending / descending triangles with a recent volume-backed breakout, tightness score, stop and target.
- **SD-Radar** — multi-timeframe (Daily/Weekly/Monthly/Quarterly) demand & supply zones with heat, institutional conviction and a trade plan.
- **ZoneDesk** — trade-ready setups graded A+…D across Daily/Weekly/Monthly income trades, with entry, stop, target and confluence.
- **Volume Delta** — per-stock buy vs sell volume and net delta over selectable windows, with advancing/declining breadth and top bought/sold rankings.

---

## Project layout

```
nse-scanner-hub/
  app.py            HTTP server + JSON API + background scan managers
  marketdata.py     shared Yahoo fetch + cache + resampling
  universes.py      Nifty 50 / Nifty 100 / NSE 500 lists + index tickers
  kotak.py          optional Kotak Neo live-LTP feed
  engines/          the five detection engines (stdlib)
    patterns/       Pattern Radar port
  web/              index.html, styles.css, app.js (the browser UI)
  data/             nse500.json, nifty50.csv
  render.yaml       Render blueprint (free Python service)
  requirements.txt  empty — stdlib only
  start.bat         one-click local preview
```

## Limitations (honest)

- Yahoo data is delayed ~15 minutes and occasionally rate-limits; scans retry and cache.
- Render free tier sleeps when idle; the first visit after a pause is slow (~50 s).
- Volume delta is an approximation from candle direction, not exchange tick data.
- The Kotak live overlay needs your own valid credential and is unverified in this build.