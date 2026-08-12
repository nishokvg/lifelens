# LifeLens — The World Since You Were Born

**Design Document · Week 1 Vibe-Coding Assignment**

A Streamlit application. The user enters a name, date of birth, birth country and
current country. The app pulls historical development data from the World Bank
Indicators API and builds a personalized visual story of how the world — and the
user's two countries — changed during their lifetime.

**Stack:** Python · Streamlit · Pandas · Requests · Plotly · World Bank Indicators API
**Deploy target:** Streamlit Community Cloud

**Scope note:** this is a Week 1 assignment with a close deadline. The first
version is deliberately small, functional and polished. Section 6 lists what is
explicitly deferred, and that list is a commitment, not a wishlist.

---

## 1. Application Architecture

Four flat layers, one-way dependencies, no package nesting beyond one level.
`app.py` owns all Streamlit rendering; everything under `services/` and `utils/`
is plain Python that can be tested without Streamlit running.

```
┌──────────────────────────────────────────────────────────────┐
│  app.py                                                      │
│  Page config · sidebar form · session_state · 5 tabs         │
│  All st.* calls live here                                    │
└───────────────┬──────────────────────────────────────────────┘
                │
    ┌───────────┴────────────┬──────────────────────┐
    ▼                        ▼                      ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│ services/        │  │ utils/           │  │ data/            │
│ world_bank.py    │  │ calculations.py  │  │ milestones.csv   │
│                  │  │ formatting.py    │  │                  │
│ HTTP · retry ·   │  │ narratives.py    │  │ curated events   │
│ cache · parse →  │  │                  │  │                  │
│ tidy DataFrame   │  │ pure functions   │  │                  │
└──────────────────┘  └──────────────────┘  └──────────────────┘
```

### Module responsibilities

| Module | Owns | Never does |
|---|---|---|
| `app.py` | Layout, the five tabs, form, session state, Plotly figure construction | HTTP calls, math |
| `services/world_bank.py` | Country list, indicator fetch, retry, timeout, `@st.cache_data`, JSON → tidy DataFrame, the `INDICATORS` registry | Formatting, narrative text |
| `utils/calculations.py` | Value-at-birth, value-now, absolute and percent change, latest-available-year, coverage checks, quiz scoring | Any I/O |
| `utils/formatting.py` | Human numbers (1.4B, $2,340, 68.2%), dates, age, days alive | Any I/O |
| `utils/narratives.py` | Template sentences from computed metrics, name-optional phrasing | Any I/O |

### Key decisions

| Decision | Rationale |
|---|---|
| **Indicator registry as a dict in `world_bank.py`** | Code, label, unit, decimals, direction, narrative template in one place. Adding an indicator is one entry — no new branches anywhere. |
| **One tidy DataFrame contract** | Every fetch normalizes to `country_code, country_name, indicator, year, value`. Charts, metrics and the quiz all read that one shape. |
| **Tabs, not one long scroll** | Matches the approved wireframe. Each tab is an independent render function; a failure in one tab cannot blank the others. |
| **Fetch once, render five times** | All six indicators for both countries plus World are fetched on form submit and cached in `st.session_state`. Switching tabs triggers zero network activity. |
| **Missing data is a render state** | Not an exception. Every chart and stat has a defined empty state. |
| **No secrets, no database** | The World Bank API is keyless and public, so Streamlit Cloud deployment is `requirements.txt` plus a push. |

### Data flow

```
Sidebar form submit
   → validate inputs
   → resolve country names to ISO3 codes
   → world_bank.fetch_all(indicators=6, countries=[birth, current, WLD],
                          start=birth_year, end=current_year)
        one HTTP GET per indicator, countries batched  →  6 requests total
   → concat to tidy DataFrame (~6 × 3 × 36 ≈ 650 rows)
   → store in st.session_state["data"]
   → tabs read from session_state, compute metrics on demand, render
```

---

## 2. User Flow

### Sidebar (persistent, visible on every tab)

```
LifeLens
────────────────────────
Name (optional)   [Nishok            ]
Date of birth     [1990-06-25   ▾]
Birth country     [India         ▾]
Current country   [United States ▾]

      [ Generate my LifeLens ]

────────────────────────
Data: World Bank Indicators API
```

**Default demonstration values** — prefilled so the app is instantly
demonstrable, and used verbatim for submission screenshots:

| Field | Value |
|---|---|
| Name | Nishok |
| Date of birth | **June 25, 1990** |
| Birth country | India |
| Current country | United States |

**Name is optional.** When it is blank, narrative text uses neutral second-person
wording — "During your lifetime, …" rather than "Nishok, during your lifetime, …".
Every narrative template must read correctly both ways; this is covered by tests.

### Tab structure

The five tabs are fixed and must be preserved:

```
┌─────────────┬──────────────────┬───────────────┬──────────────────────┬───────────────┐
│  My Story   │ Lifetime in Data │ My Two Worlds │ Timeline &Discoveries│ Quiz & Share  │
└─────────────┴──────────────────┴───────────────┴──────────────────────┴───────────────┘
```

**1 · My Story**
Personalized opening — greeting, formatted birth date, age, days alive. A row of
headline stat cards: for each of ~4 indicators, the value in the birth year, the
latest value, and the change, colored by whether that direction is good. Two or
three generated narrative sentences underneath.

**2 · Lifetime in Data**
The six indicators, one block each: a Plotly line chart with birth country,
current country and World; a vertical marker at the birth year; a one-sentence
takeaway below. Each block carries a footnote with the indicator code and the
actual latest data year.

**3 · My Two Worlds**
Direct birth-country vs current-country comparison. A then-vs-now slope chart
across all six indicators (normalized, birth year = 100), plus a gap-over-time
chart for one or two headline indicators. When birth country equals current
country, this tab renders a friendly single-country variant instead of an empty
panel — never a blank tab.

**4 · Timeline & Discoveries**
Three stacked sections:

1. **A personal timeline** marking birth, ages 5, 10, 18, 21 and 30 — each shown
   only once reached — and ending on the latest available data year. Rendered as
   a static strip plus a selector; there is no animation.
2. **A selected-year statistic card** for whichever point is selected: the
   user's age, the calendar year, one chosen World Bank statistic, the year that
   value was *actually* observed, and a short deterministic interpretation.
   Where the exact year has no observation, the nearest within **three years**
   is used and labelled as such. Beyond three years the card reads
   "Data unavailable" — values are never interpolated.
3. **Discovery cards** loaded only from `data/milestones.csv`, filtered to the
   lifespan, with category filters, the user's age at each event, and a
   clickable source per row. A timeline year with no milestone renders nothing
   rather than an empty card.

**5 · Quiz & Share**
Three guess-before-reveal questions drawn from the fetched data, an answer
comparison showing guess vs actual, a simple score out of three, and a
downloadable plain-text LifeLens story.

### States

```
[Empty]    → app loads with defaults prefilled, tabs show a "Generate to begin" prompt
[Loading]  → st.spinner, staged status text per indicator
[Ready]    → all five tabs render from session_state
[Partial]  → some indicators failed; those blocks show an inline notice, the rest render
[Error]    → total API failure; clear message plus a Retry button, app still usable
```

---

## 3. Folder Structure

```
lifelens/                          ← git repository root
├── app.py                         ← Streamlit entrypoint, all UI
├── requirements.txt               ← pinned deps (Streamlit Cloud reads this)
├── README.md                      ← setup, live URL, screenshots
├── DESIGN.md                      ← this document
├── .gitignore
├── .streamlit/
│   └── config.toml                ← theme + server settings (committed)
├── services/
│   ├── __init__.py
│   └── world_bank.py              ← API client, INDICATORS registry, caching
├── utils/
│   ├── __init__.py
│   ├── calculations.py            ← deltas, coverage, quiz scoring
│   ├── formatting.py              ← number/date/age formatting
│   └── narratives.py              ← template sentences (name-optional)
├── data/
│   └── milestones.csv             ← curated events
├── assets/
│   └── styles.css                 ← minimal custom CSS
└── tests/
    ├── test_world_bank.py         ← parsing, error shapes, empty data
    ├── test_calculations.py       ← deltas, null baselines, single-point series
    └── test_timeline.py           ← timeline points, three-year rule, CSV integrity
```

Assignment evidence (prompt captures, the wireframe document) is kept **outside**
the repository: it is submission documentation, not application runtime code.

`requirements.txt` — pinned so Cloud rebuilds are reproducible:

```
streamlit~=1.40
pandas~=2.2
requests~=2.32
plotly~=5.24
```

Python version is selected in the Streamlit Cloud **Advanced settings** dropdown
at deploy time (there is no `runtime.txt` mechanism). Target 3.11.

---

## 4. API Strategy

**Base:** `https://api.worldbank.org/v2` — public, keyless, no auth.

### 4.1 Endpoints

| Purpose | Request |
|---|---|
| Country list | `GET /country?format=json&per_page=400` |
| Indicator series | `GET /country/{IND;USA;WLD}/indicator/{CODE}?format=json&date={birth_year}:{this_year}&per_page=1000` |

**Country batching is the whole optimization.** Semicolon-joined ISO3 codes fetch
birth country, current country and `WLD` (the World aggregate) in one request.
Six indicators means **six HTTP calls per story**, not eighteen.

Always pass an explicit `per_page`. The default page size is 50 and would
silently truncate a 36-year series across three countries. Over-request instead
of paginating.

### 4.2 Response shape and the one critical gotcha

The API returns a **two-element array** — `[metadata, rows]`:

```json
[ {"page":1,"pages":1,"per_page":1000,"total":108},
  [ {"indicator":{"id":"SP.DYN.LE00.IN","value":"Life expectancy at birth…"},
     "country":{"id":"IN","value":"India"},
     "countryiso3code":"IND","date":"1990","value":57.9} , … ] ]
```

**Gotcha:** the World Bank returns **HTTP 200 with an error body** for bad
parameters — `[{"message":[{"id":"120","key":"Invalid value", …}]}]`, a
one-element array. `raise_for_status()` will not catch this. The parser branches
on structure, not status code:

| Payload shape | Meaning |
|---|---|
| `len == 2` and `payload[1]` is a list | Success |
| `len == 2` and `payload[1]` is `None` | Valid query, zero observations |
| `"message"` in `payload[0]` | API-level error |
| anything else | Unexpected — log, return empty frame |

### 4.3 Client policy (MVP)

- **Module-level `requests.Session`** reused across calls.
- **Explicit timeout** on every request — `timeout=(5, 15)`. A hung request on
  Streamlit Cloud looks like a broken app.
- **Simple retry** — a small loop, 2 retries with a short sleep, on timeouts,
  connection errors and 5xx/429. No `urllib3.Retry` configuration needed at this
  size.
- **Sequential fetches** with a progress indicator. Six requests is fast enough;
  threading is deferred (§6).
- **Two exception types** — `WorldBankError` (we failed) and a plain empty
  DataFrame (the data doesn't exist). The UI treats these differently.

### 4.4 Caching

| Layer | Mechanism | TTL | Key |
|---|---|---|---|
| Country list | `@st.cache_data` | 7 days | none |
| Indicator series | `@st.cache_data` | 24 h | `(code, tuple(sorted(countries)), start, end)` |
| Fetched story data | `st.session_state` | session | — |

Sort and tuple-ify the country list before it reaches the cache key, so
`("IND","USA")` and `("USA","IND")` resolve to one entry. Caching plus
session_state means tab switching is instant and re-running a previous query
costs zero requests.

### 4.5 Indicator registry — the six MVP indicators

| Story beat | Code | Unit | Better | Note |
|---|---|---|---|---|
| How many of us | `SP.POP.TOTL` | people | — | most complete series |
| How long we live | `SP.DYN.LE00.IN` | years | ↑ | ~2-year reporting lag |
| What we produce | `NY.GDP.PCAP.KD` | constant 2015 US$ | ↑ | constant dollars |
| How connected | `IT.NET.USER.ZS` | % | ↑ | series begins ~1990 |
| Where we live | `SP.URB.TOTL.IN.ZS` | % | — | neutral direction |
| Child survival | `SH.DYN.MORT` | per 1,000 | ↓ | direction flips |

Registry fields per entry: `code, label, beat, unit, decimals, better_direction,
narrative_template, source_note`.

`NY.GDP.PCAP.KD` (constant 2015 US$) is used deliberately rather than
`NY.GDP.PCAP.CD` (current US$). Comparing current-dollar GDP across a 36-year
lifetime conflates inflation with growth and would make the story factually
wrong.

`SH.DYN.MORT` (under-5 mortality) and `SP.DYN.IMRT.IN` (infant mortality) are
different indicators — the registry uses under-5 as specified.

### 4.6 Milestones data

`data/milestones.csv` is curated by hand and version-controlled, not fetched.
Schema:

```csv
year,category,title,description,source_url
```

- `year` — integer
- `category` — one of `Technology`, `Science`, `Health`, `Society`, `Space`
- `title` — short headline
- `description` — one or two sentences
- `source_url` — a citation link per row

Target roughly 25–35 rows spanning 1985 to the present, so any plausible birth
year yields a populated timeline. Loaded once with `@st.cache_data`, filtered to
`birth_year <= year <= current_year`.

---

## 5. MVP Features

Scoped as "complete and defensible", not "impressive but half-working".

1. **Sidebar input form** — name (optional), DOB, birth country, current country,
   prefilled with the demonstration defaults. Country dropdowns populated live
   from the API, aggregates filtered out.
2. **Validation with inline feedback** — no future dates, no implausible age,
   both countries resolvable to ISO3.
3. **Live World Bank fetch** — six indicators, countries batched, timeout, retry,
   cached.
4. **Five-tab navigation** exactly as specified, each tab independently rendered.
5. **My Story** — personalized (or neutral) opening, age and days alive, headline
   stat cards with direction-aware coloring.
6. **Lifetime in Data** — six line charts, three series each, birth-year marker,
   one generated sentence per chart.
7. **My Two Worlds** — slope chart plus gap chart; graceful single-country
   variant when the two countries match.
8. **Timeline & Discoveries** — personal timeline of reached milestone ages
   ending on the latest data year; a selected-year statistic card with the real
   observation year and a deterministic interpretation; discovery cards from
   `milestones.csv` with age-at-event, category filters and source links.
9. **Quiz & Share** — three guess-before-reveal questions, guess-vs-actual
   comparison, score out of three, downloadable plain-text story.
10. **Actual data-year labeling** — every "today" figure is labeled with the year
    it actually comes from, never assumed to be the current year.
11. **Partial rendering** — one failed indicator degrades one block, not the app.
12. **Per-indicator footnotes** with code, latest available year and World Bank
    attribution.
13. **Deployed to Streamlit Community Cloud** with the public URL in the README.

### Definition of done

The app must survive all of these without a traceback:

- Name left **blank** → neutral wording throughout
- Born **1960** → series that begin after the birth year
- Born **last month** → fewer than two data points
- Birth country **==** current country → My Two Worlds still renders
- One indicator forced to fail → the other five still render

---

## 6. Optional Features — Deferred Until After the MVP

These are explicitly **out of scope for Week 1**. Listing them here is a
commitment to not start them early.

**Deferred (specified):**
ThreadPoolExecutor parallel fetching · global rankings · CAGR ·
query-parameter sharing · offline snapshot fallback · choropleth maps ·
animated charts · PDF or PNG export · more than six indicators ·
LLM integration.

**Possible later additions, in rough value order:**

1. **Live Nobel Prize API integration** on Timeline & Discoveries — laureates and
   citations for each year of the user's lifetime, from the public
   [Nobel Prize API](https://api.nobelprize.org/). Deferred until the complete
   MVP is working, tested and deployed. Until then there is deliberately **no
   placeholder card and no hard-coded prize data** anywhere in the app; a test
   (`test_no_nobel_prize_placeholder_data`) enforces this.
2. A third comparison country — the fetch layer already supports N countries, so
   it is a UI change only.
3. User-selectable indicators from a larger registry.
4. Region aggregates (`SAS`, `EUU`, `OED`) as extra comparison lines.
5. Light/dark theme toggle.
6. A second data source for indicators the World Bank covers poorly.

---

## 7. Error and Missing-Data Scenarios

The section that separates a working assignment from a demo that breaks on
stage. All of these are handled explicitly.

### 7.1 Network and transport

| # | Scenario | Handling |
|---|---|---|
| 1 | Connection timeout / DNS failure | Explicit `timeout=(5,15)`, 2 retries with short sleep, then a clear error state with a Retry button |
| 2 | HTTP 5xx / 429 | Same retry path |
| 3 | **Partial failure** — 4 of 6 indicators succeed | Render the 4. Failed blocks show an inline notice. Never fail the whole story for one bad request. |
| 4 | Slow cold start (Cloud app was asleep) | Spinner with per-indicator status text so the user sees motion |

### 7.2 API-level errors

| # | Scenario | Handling |
|---|---|---|
| 5 | **HTTP 200 with an error body** | Structural parse (§4.2), not `raise_for_status`. This will happen with a typo'd indicator code. |
| 6 | Invalid country code | Prevented at the source — the dropdown only offers codes the API returned |
| 7 | Unexpected JSON shape after an API change | Defensive parse returns an empty frame plus a logged warning; the app still renders |

### 7.3 Missing and sparse data — the common case

| # | Scenario | Handling |
|---|---|---|
| 8 | `payload[1] is None` — valid query, zero rows | Empty DataFrame; that indicator renders its empty state |
| 9 | All values null for one country | Drop that series, keep the others, footnote which country is missing |
| 10 | **Reporting lag** — the last 1–3 years are null | Compute a **per-indicator latest available year**. Never assume "now" is the current year. Label every "today" value with its real year. |
| 11 | Gaps mid-series | Plot with `connectgaps=True` and markers on real observations. Never interpolate silently into a number the user will read as fact. |
| 12 | **Birth year precedes coverage** (born 1990, internet data starts 1990 — tight; born 1960, it does not exist) | Snap the baseline to the first available year and say so: "Internet data begins in 1990." Never extrapolate backwards. |
| 13 | **Born within the last 1–2 years** | Fewer than two points means no trend — show current values with an explanatory line |
| 14 | Baseline is 0 or null, so percent change explodes | `calculations.py` returns `None`; the narrative switches to an absolute-change template ("rose from near zero to 68%") |

### 7.4 Country identity

| # | Scenario | Handling |
|---|---|---|
| 15 | Country did not exist at the birth year (South Sudan, post-Soviet states) | The series simply starts later; detect the truncation and annotate it |
| 16 | User picks an **aggregate** ("World", "Euro area") | Filtered from the dropdown via the country list's `region.id == "NA"` marker. `WLD` is used internally only. |
| 17 | Birth country **==** current country | My Two Worlds renders a single-country variant; no empty panel |

### 7.5 Input validation

| # | Scenario | Handling |
|---|---|---|
| 18 | Future DOB | Blocked by `max_value=today` on `date_input`, re-checked before fetch |
| 19 | Implausible age (> 120) | Inline error, form not submitted |
| 20 | **Empty name** | Valid by design — narratives switch to neutral "During your lifetime" wording |
| 21 | Name containing markdown or HTML | Escaped before interpolation |

### 7.6 Local data and rendering

| # | Scenario | Handling |
|---|---|---|
| 22 | `milestones.csv` missing or malformed | Timeline tab shows a friendly notice; the other four tabs are unaffected. Required columns are validated on load and a bad file degrades to an empty frame rather than an exception |
| 23 | No milestones fall inside the lifespan | Empty state explaining the range covered |
| 23a | Timeline year has no observation | Nearest within **three years** is used and labelled with its real year; beyond that, "Data unavailable". Never interpolated |
| 23b | Timeline year has no milestone | The timeline renders normally; no empty discovery card is drawn |
| 23c | User has not reached a milestone age | That age is omitted from the timeline entirely (a 12-year-old sees no "Age 18") |
| 24 | Plotly figure with an all-NaN series | Detected before building; render a placeholder card, not a blank axis box |
| 25 | Mixed scales in one chart (population vs percent) | Never mix units on one axis — index to birth year = 100, or use separate charts |
| 26 | Quiz answered before data is fetched | Tabs guard on `session_state`, showing the "Generate to begin" prompt |
| 27 | Narrow viewport | `use_container_width=True` on every chart; stat cards stack |
| 28 | Works locally, fails on Cloud | Pinned requirements, no absolute local paths, `data/` and `assets/` referenced relative to `app.py` |

---

## 8. Implementation Order

Eight phases. **Every phase ends with a runnable app** — there is never a state
where nothing works.

### Phase 0 — Skeleton and deploy pipeline *(first, not last)*
`app.py` rendering only the title, subtitle and a setup-successful message, plus
`requirements.txt`, `.streamlit/config.toml`, `.gitignore`. Push to GitHub and
deploy to Streamlit Community Cloud immediately.
→ *Why first:* deployment problems are environment problems. Finding them on day
one with ten lines of code costs minutes; finding them the night before
submission costs the assignment.

### Phase 1 — API client
`services/world_bank.py`: session, timeout, retry, structural error parsing,
country list, single-indicator fetch, JSON → tidy DataFrame, `@st.cache_data`.
Save real responses into `tests/` fixtures — including an error response and an
empty-data response, obtained by hitting the API with a bad code on purpose.
→ **Checkpoint:** app shows a working country dropdown and a raw DataFrame.

### Phase 2 — Form, session state and tab shell
Sidebar form with demonstration defaults, validation, and all five tabs present
as stubs with their "Generate to begin" prompts. Fetch-once-into-session_state
wiring.
→ **Checkpoint:** navigation is complete and clickable end to end.

### Phase 3 — Lifetime in Data
The full six-indicator registry, the shared Plotly styling helper, the line chart
with the birth-year marker, and the per-chart footnote with the real data year.
→ **Checkpoint:** the first vertical slice — real input, real API, real charts.
**This is the moment the app exists.**

### Phase 4 — Calculations and narratives
`utils/calculations.py` and `utils/formatting.py` and `utils/narratives.py`, then
the My Story tab: stat cards and generated sentences, correct with a name and
without one. Unit tests for null baselines and single-point series.
→ **Checkpoint:** My Story and Lifetime in Data are both complete.

### Phase 5 — My Two Worlds
Slope chart, gap chart, and the same-country variant.
→ **Checkpoint:** three of five tabs complete.

### Phase 6 — Timeline & Discoveries
Curate `data/milestones.csv` (34 rows), load with column validation, filter to
lifespan, render the personal timeline, the selected-year statistic card (with
the three-year nearest-observation rule) and the category-filtered discovery
cards.
→ **Checkpoint:** four of five tabs complete.

### Phase 7 — Quiz & Share
Three guess-before-reveal questions generated from the fetched data, comparison,
score, and the plain-text story download.
→ **Checkpoint:** feature-complete.

### Phase 8 — Hardening, polish, submit
Walk section 7 top to bottom. Run every Definition-of-Done input from section 5.
Kill the network mid-load. Point one registry entry at a bogus code and confirm
partial rendering. Then the CSS pass, README with the live URL, screenshots into
`docs/images/`, and a final verification of the deployed app from a
logged-out browser (which catches private-repo mistakes).

---

**The two things that most often sink a project like this:** deploying only at
the end, and treating missing data as an exception rather than a render state.
Phase 0 fixes the first. Phase 8 — with time actually budgeted for it — fixes the
second.
