# Widget stability tiers

Every widget that hits the network depends on an upstream, and not
every upstream is equally reliable. Tesserae's tiers say what to expect
when your widget suddenly shows an error after a year of working fine.

## Stable

Backed by an official, documented API. Won't break unless the upstream
ships a deliberate breaking change (which usually comes with notice).

| Widget | Upstream |
|---|---|
| `weather_now`, `weather_hourly`, `weather_forecast`, `weather_air_quality`, `weather_pollen_count` (Open-Meteo path) | open-meteo.com |
| `f1_next`, `f1_last_race`, `f1_weekend`, `f1_standings_drivers` *(marketplace)* | jolpi.ca/ergast (maintained Ergast successor) |
| `news_rss` | any RSS 2.0 / Atom 1.0 feed |
| `news_hacker_news` | hacker-news.firebaseio.com (Firebase-hosted, documented) |
| `news_wikipedia_otd` | Wikimedia REST API |
| `finance_crypto` | CoinGecko v3 (documented) |
| `finance_currency` | frankfurter.app (ECB-sourced, documented) |
| `github_repo`, `github_releases`, `github_activity`, `github_pr_queue`, `github_actions`, `github_contributions` *(marketplace)* | api.github.com (REST v3 + GraphQL v4) |
| `calendar_month`, `calendar_week`, `calendar_day` | iCal feeds from Google / iCloud / Outlook |
| `clock_sunrise_sunset` | open-meteo.com |
| `sky_aurora` | services.swpc.noaa.gov (NOAA Space Weather, documented) |
| `sky_air_traffic` | opensky-network.org (rate-limited but documented) |
| `public_transport_times` | timetableapi.ptv.vic.gov.au (official PTV API v3) |
| `picture_apod` | api.nasa.gov |
| `picture_unsplash` | api.unsplash.com |

## Best-effort

Undocumented endpoint, but used by enough open-source projects for long
enough that breakage would be a notable event. Failure mode is usually
"widget returns an error payload until somebody updates the parser."

| Widget | Why best-effort |
|---|---|
| `finance_stock` | Yahoo Finance's `/v8/finance/chart/` endpoint isn't officially documented. Has been stable for 7+ years and is what most "free stock API" libraries reach for. |
| `picture_apple_album` | Reverse-engineered iCloud Shared Album endpoints. The shape has held up for ~10 years; Apple has shifted partitioning twice without breaking the protocol. |

## Fragile

Scraping or relying on a soft policy. Most likely to break with no
notice. The widget will surface an error payload rather than silently
serve stale data.

| Widget | Why fragile |
|---|---|
| `weather_pollen_count` (Melbourne fallback) | Scrapes melbournepollen.com.au's home page when Open-Meteo's pollen data is null (it's Europe-only). Any DOM redesign on their end breaks this path. The Open-Meteo path is stable. |
| `news_reddit` | Reads Reddit's per-subreddit RSS feed (`/r/<sub>/<sort>.rss`). The public `.json` API now 403-blocks server-side reads outright, so RSS is the remaining no-auth path, it carries titles/authors/times but **no** score or comment counts, and could itself be locked down. |

## Tier policy

* **Stable widgets** can be relied on for long-running dashboards. PR
  welcome if you spot an upstream that's tightened terms.
* **Best-effort widgets** should not be the only display in a
  twelve-month deployment. If they break, the widget's error payload
  tells you exactly which endpoint failed.
* **Fragile widgets** should be considered convenience. If your
  dashboard cares about pollen counts every day, treat the widget as
  a starting point and contribute a more durable backend.
