# Widget stability tiers

These tiers describe how likely a widget's data source is to break.

## Stable

Stable widgets use official, documented APIs. They break only when the provider deliberately makes a breaking change, usually with notice.

| Widget | Upstream |
|---|---|
| `weather_now`, `weather_now_scenic`, `weather_hourly`, `weather_forecast` | open-meteo.com |
| `weather_air_quality`, `weather_pollen_count` (Open-Meteo path) *(marketplace)* | open-meteo.com |
| `f1_next`, `f1_last_race`, `f1_weekend`, `f1_standings_drivers` *(marketplace)* | jolpi.ca/ergast (maintained Ergast successor) |
| `news_rss` | any RSS 2.0 / Atom 1.0 feed |
| `news_hacker_news` | hacker-news.firebaseio.com (Firebase-hosted, documented) |
| `news_wikipedia_otd` | Wikimedia REST API |
| `finance_crypto` *(marketplace)* | CoinGecko v3 (documented) |
| `finance_currency` *(marketplace)* | frankfurter.app (ECB-sourced, documented) |
| `github_repo`, `github_releases`, `github_activity`, `github_pr_queue`, `github_actions`, `github_contributions` *(marketplace)* | api.github.com (REST v3 + GraphQL v4) |
| `calendar_month`, `calendar_week`, `calendar_day` | iCal feeds from Google / iCloud / Outlook |
| `clock_sunrise_sunset` | open-meteo.com |
| `sky_aurora` *(marketplace)* | services.swpc.noaa.gov (NOAA Space Weather, documented) |
| `sky_air_traffic` *(marketplace)* | opensky-network.org (rate-limited but documented) |
| `public_transport_times` *(marketplace)* | timetableapi.ptv.vic.gov.au (official PTV API v3) |
| `picture_apod` | science.nasa.gov (`wp-json/wp/v2/apod-basic`) |
| `picture_unsplash` *(marketplace)* | api.unsplash.com |

## Best-effort

Best-effort widgets use undocumented endpoints that many open-source projects have used for years.
A break would affect enough projects to be noticeable.
The widget usually shows an error until someone updates its parser.

| Widget | Why best-effort |
|---|---|
| `finance_stock` *(marketplace)* | Yahoo Finance's `/v8/finance/chart/` endpoint isn't officially documented. Has been stable for 7+ years and is what most "free stock API" libraries reach for. |
| `picture_apple_album` *(marketplace)* | Reverse-engineered iCloud Shared Album endpoints. The shape has held up for ~10 years; Apple has shifted partitioning twice without breaking the protocol. |

## Fragile

Fragile widgets scrape web pages or depend on a policy that can change without notice.
They are the most likely to break. They show an error instead of silently serving old data.

| Widget | Why fragile |
|---|---|
| `weather_pollen_count` (Melbourne fallback) *(marketplace)* | Scrapes melbournepollen.com.au's home page when Open-Meteo's pollen data is null (it's Europe-only). Any DOM redesign on their end breaks this path. The Open-Meteo path is stable. |
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
