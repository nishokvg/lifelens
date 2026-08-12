# 🔭 LifeLens — The World Since You Were Born

A Streamlit application that turns a date of birth into a personalized view of
global development. Enter a name, date of birth, birth country and current
country, and LifeLens retrieves historical indicators from the **World Bank
Indicators API** to show how the world — and your two countries — changed during
your lifetime.

**Live application:** _<!-- Paste your Streamlit Community Cloud URL here after deploying -->_

---

## Problem statement

Global development statistics are abundant and almost entirely impersonal. The
World Bank publishes decades of data on population, health, income and
connectivity, but it arrives as tables and country profiles that are hard to
feel anything about. "Under-5 mortality in India fell from 126 to 27 per 1,000"
is a true sentence that most people read straight past.

LifeLens reframes the same public data around a single anchor the reader
already cares about: **their own lifetime**. Instead of "since 1990", the app
says "since the year you were born" and "by the year you turned 18". The data is
unchanged and fully sourced — only the frame of reference is personal. The goal
is to make development trends legible to someone who would never open a World
Bank data portal.

---

## Assignment context — Path B

Built as a **Week 1 Path B vibe-coding assignment**: an application developed
through structured collaboration with an AI coding assistant, where the process
is part of the deliverable alongside the working app.

The project was built in documented phases — architecture and design first, then
a deploy-ready skeleton, then the data layer with tests, then one tab at a time.
[DESIGN.md](DESIGN.md) is the planning artifact: architecture, user flow, API
strategy, MVP versus deferred scope, a catalogue of error and missing-data
scenarios, and the phase order that was actually followed. Prompt and planning
evidence is submitted separately and is deliberately kept out of this
repository, which holds application code only.

Two constraints shaped every decision: a close deadline, and a commitment that
the first version be **small, functional and polished** rather than broad and
half-working. The deferred list in DESIGN.md § 6 is a scope commitment, not a
wishlist.

---

## Features currently implemented

**Input and validation**
- Name (optional — narratives switch to neutral "During your lifetime" wording)
- Full date of birth, birth country, current country
- Country lists loaded live from the API, with aggregates such as "Euro area"
  filtered out so they cannot be selected
- Friendly validation: no future dates, no birth year before 1960 (where World
  Bank coverage begins), no implausible ages; the two countries may be the same

**📖 My Story** — personalized introduction, exact age, days lived, and clearly
labelled estimates (full moons, breaths, sunrises, distance travelled by Earth's
orbit). Then-versus-latest cards for world population, birth-country population,
life expectancy and internet use, each showing its real data year. An
automatically calculated "what changed the most" insight, and a deterministic
narrative built from the retrieved values.

**📈 Lifetime in Data** — indicator, country and year-range controls; an
interactive Plotly chart with birth-year and latest-available-year markers;
birth value, latest value, absolute change, percentage change, and
percentage-point change for percentage-based indicators; a plain-English
interpretation, an expandable raw-data table, CSV download and source
attribution.

**🌍 My Two Worlds** — birth country versus current country versus the world
aggregate, with flags, side-by-side metric cards, a grouped bar chart, a
historical line comparison, and calculated largest-difference and
strongest-improvement insights. When both countries are the same, the tab
compares that country with the world average instead of rendering empty.

**🗓️ Timeline & Discoveries** — a personal timeline marking birth and ages 5, 10,
18, 21 and 30 (only once reached), ending on the latest available data year; a
selected-year statistic card showing age, calendar year, one indicator, the year
that value was actually observed, and a deterministic interpretation; and
category-filtered discovery cards drawn only from `data/milestones.csv`, each
with the user's age at the time and a clickable source.

**🎯 Quiz & Share** — not yet implemented; the tab is present in the navigation.

---

## Architecture summary

Four flat layers with one-way dependencies. `app.py` owns every Streamlit call;
everything beneath it is plain Python that runs and tests without a Streamlit
runtime.

```
app.py                  layout, five tabs, form, session state, Plotly figures
   │
   ├── services/world_bank.py    HTTP, retry, timeout, caching, JSON → tidy frame,
   │                             the six-indicator registry
   ├── utils/calculations.py     year selection, change arithmetic, timeline, coverage
   ├── utils/formatting.py       numbers, values, years, flags, articles
   ├── utils/narratives.py       deterministic template sentences (no LLM)
   └── data/milestones.csv       curated timeline events
```

Key decisions:

- **One tidy DataFrame contract.** Every fetch normalizes to
  `indicator, country_code, country_name, year, value`. Charts, metrics,
  narratives and the timeline all read that one shape.
- **Fetch once, render five times.** All six indicators for both countries plus
  the world aggregate are retrieved on submit and held in `session_state`;
  switching tabs triggers no network activity.
- **The indicator registry is data.** Code, label, unit, decimals, direction,
  emoji and caveats live in one dict. Adding an indicator is one entry, not a
  new code path.
- **Missing data is a render state, not an exception.** Every chart, card and
  sentence has a defined empty state.

---

## Data source

All statistics come from the [World Bank Open Data](https://data.worldbank.org)
World Development Indicators, retrieved live through the public
[Indicators API](https://datahelpdesk.worldbank.org/knowledgebase/articles/889392).
The API is public and keyless — **no API key, account or secret is required**,
which is why this app deploys with nothing but a `requirements.txt`.

| Story beat | Indicator | Code | Better |
|---|---|---|---|
| 👥 How many of us | Population, total | `SP.POP.TOTL` | neutral |
| ❤️ How long we live | Life expectancy at birth | `SP.DYN.LE00.IN` | ↑ |
| 💰 What we produce | GDP per capita (constant 2015 US$) | `NY.GDP.PCAP.KD` | ↑ |
| 🌐 How connected | Individuals using the internet (%) | `IT.NET.USER.ZS` | ↑ |
| 🏙️ Where we live | Urban population (%) | `SP.URB.TOTL.IN.ZS` | neutral |
| 👶 Child survival | Under-5 mortality (per 1,000) | `SH.DYN.MORT` | ↓ |

Countries are batched into one request per indicator, so a full story costs six
HTTP calls rather than eighteen. Responses are cached for 24 hours, and the
country list for seven days.

### Why GDP is in constant 2015 US$

LifeLens uses **`NY.GDP.PCAP.KD` — GDP per capita in constant 2015 US$** — and
deliberately **not** `NY.GDP.PCAP.CD`, the current-dollar series.

Current-dollar GDP is measured in the prices of each year it describes. Comparing
a 1990 figure with a 2025 figure in current dollars therefore mixes two different
things: real growth in output, and three and a half decades of inflation. Over a
lifetime-length window that distortion is large enough to make the story simply
wrong — it would credit a country with "growth" that is partly just higher
prices.

Constant 2015 US$ re-expresses every year in the same 2015 price level, so a
change across the series reflects real change in output per person. This is the
correct choice for any lifetime comparison, and it is why India's rise reads as
**+369%** here rather than the larger, partly fictitious number current dollars
would produce.

---

## Local setup

Requires Python 3.9 or newer (3.11 recommended, matching the deployment target).

```bash
git clone https://github.com/YOUR_USERNAME/lifelens.git
cd lifelens

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
streamlit run app.py
```

The app opens at <http://localhost:8501>. No `.env` file, API key or
configuration step is needed.

---

## Running the tests

```bash
pip install pytest
python -m pytest tests/ -q          # 97 offline unit tests
```

The unit suite is fully offline and deterministic — it uses recorded API payload
shapes rather than live requests, including the error and empty-data responses,
so it runs fast and never depends on network conditions.

A separate live check reports real API coverage:

```bash
python tests/integration_check.py
```

It queries India, the United States and the world aggregate from 1990 onward and
prints, per indicator and country, the earliest and latest returned years, the
number of valid observations, and any missing-data issues.

---

## Deploying to Streamlit Community Cloud

The repository is deployment-ready as-is. No secrets are required.

1. Push this repository to GitHub.
2. Sign in at [share.streamlit.io](https://share.streamlit.io) with the same
   GitHub account.
3. Click **New app** and select the `lifelens` repository, the `main` branch,
   and `app.py` as the main file path.
4. Under **Advanced settings**, select **Python 3.11**.
5. Click **Deploy**. The first build takes a few minutes while dependencies
   install.
6. Paste the resulting URL into the **Live application** line at the top of this
   README.

Notes:
- `.streamlit/config.toml` is committed and supplies the theme.
- `.streamlit/secrets.toml` is git-ignored and is not needed by this app.
- A free-tier app sleeps after inactivity; the first visit after a sleep takes
  several seconds to wake.

---

## Data handling principles

1. **Never fabricate a value.** Null observations are dropped, never interpolated
   or forward-filled. A year the World Bank did not report does not appear, and
   the UI shows "Data unavailable" rather than a guess.
2. **"Latest available year", never "today".** The reporting lag differs per
   indicator, so every figure is labelled with the year it actually comes from.
3. **Partial rendering.** If one indicator fails, the other five still render and
   the failure is reported in an expander.
4. **Honest baselines.** When a series starts after the birth year, the baseline
   snaps forward to the first reported year and says so. Nothing is extrapolated
   backwards.
5. **No overstated claims.** Life expectancy at birth is presented as a period
   measure for babies born in a given year — never as a prediction of the user's
   own lifespan.
6. **The three-year rule on the timeline.** Where a timeline year has no
   observation, the nearest within three years is shown and labelled with its
   real year; beyond that, "Data unavailable".
7. **Milestones are curated, not generated.** Every discovery card comes from a
   row in `data/milestones.csv` with its own source link.

---

## Known limitations

**Data**
- Reporting lags vary: population, GDP and urban share currently report through
  2025; life expectancy and under-5 mortality through 2024. No indicator reports
  the current year.
- The world aggregate for internet usage begins in 2005, so that series starts
  partway through a 1990 lifetime.
- Some baselines are exactly zero (India's 1990 internet usage), making
  percentage change undefined; the app reports percentage-point change instead.
- Mid-series gaps exist and are left visible rather than connected across.
- Country coverage varies widely; low-reporting countries produce sparse charts.
- Figures reflect present-day borders, so countries that did not exist at a given
  birth year simply have shorter series.

**Application**
- Milestones are a single hand-curated list covering 1985–2024, not localized per
  country, and identical for every user.
- Ages for milestones and timeline points are computed from the birth *year*, so
  someone born late in a year may see an age one higher than they actually were
  at an event.
- The app commits to a light theme; the chart palette is validated specifically
  for the light surface.
- The World Bank names some countries its own way ("Somalia, Fed. Rep."), which
  can make them hard to find in the dropdown.

---

## Deferred features

Explicitly out of scope for this version, listed in DESIGN.md § 6:

parallel fetching with `ThreadPoolExecutor` · global rankings · CAGR ·
query-parameter sharing · offline snapshot fallback · choropleth maps ·
animated charts · PDF or PNG export · more than six indicators · LLM integration
· live Nobel Prize API integration on the timeline.

Nobel Prize data is deliberately absent rather than stubbed: there is no
placeholder card and no hard-coded prize information anywhere, and a test
enforces that.

---

## Screenshots

_Add final screenshots of the deployed app to `docs/images/` and uncomment the
matching row. Use screenshots of the running application — not prompt or
planning captures._

| View | Screenshot |
|---|---|
| Landing / input screen | _<!-- ![Landing](docs/images/landing-page.png) -->_ |
| 📖 My Story | _<!-- ![My Story](docs/images/my-story.png) -->_ |
| 📈 Lifetime in Data | _<!-- ![Lifetime in Data](docs/images/lifetime-data.png) -->_ |
| 🌍 My Two Worlds | _<!-- ![My Two Worlds](docs/images/two-worlds.png) -->_ |
| 🗓️ Timeline & Discoveries | _<!-- ![Timeline](docs/images/timeline.png) -->_ |

---

## Project structure

```
lifelens/
├── app.py                     # Streamlit entrypoint — all UI
├── requirements.txt
├── README.md
├── DESIGN.md                  # architecture and implementation plan
├── .gitignore
├── .streamlit/
│   └── config.toml            # theme (committed; secrets.toml is not)
├── services/
│   ├── __init__.py
│   └── world_bank.py          # API client, indicator registry, caching
├── utils/
│   ├── __init__.py
│   ├── calculations.py        # year selection, change arithmetic, timeline
│   ├── formatting.py          # numbers, values, years, flags
│   └── narratives.py          # deterministic sentence generation
├── data/
│   └── milestones.csv         # 34 curated events with source links
├── assets/
│   └── styles.css
└── tests/
    ├── test_world_bank.py
    ├── test_calculations.py
    ├── test_timeline.py
    └── integration_check.py   # live API check, not part of the unit suite
```

Screenshots added later live in `docs/images/`. Assignment evidence (prompt
captures, the wireframe document) is submitted separately and is intentionally
not part of this repository.

---

## Attribution

Data: [World Bank Open Data](https://data.worldbank.org) — World Development
Indicators, retrieved live via the public
[Indicators API](https://datahelpdesk.worldbank.org/knowledgebase/articles/889392).
Timeline entries in `data/milestones.csv` are hand-curated, each with its own
source link.
