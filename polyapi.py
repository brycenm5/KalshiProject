import os
import json
import time
import requests
import pandas as pd

BASE = "https://gamma-api.polymarket.com"
FOLDER = os.path.dirname(os.path.abspath(__file__))
MARKETS_FILE = os.path.join(FOLDER, "polymarket_markets.csv")
STATE_FILE = os.path.join(FOLDER, "polymarket_progress.json")

PAGE_SIZE = 100  # max allowed by the keyset endpoint

COLUMNS = ["id", "condition_id", "slug", "question",
           "event_id", "event_slug", "event_title",
           "series_slug", "series_title",
           "category", "tags",
           "outcomes", "outcome_prices",
           "closed", "active", "uma_resolution_status",
           "start_date", "end_date", "closed_time", "created_at",
           "volume", "liquidity", "last_trade_price",
           "sports_market_type", "game_id", "source"]

# Closed markets first (the history), then markets that are still open
PHASES = [
    ("closed", {"closed": "true"}),
    ("open", {"closed": "false"}),
]
PHASE_NAMES = [p[0] for p in PHASES]


def get(path, params):
    for attempt in range(8):
        try:
            r = requests.get(BASE + path, params=params, timeout=60)
        except requests.exceptions.RequestException:
            time.sleep(5)
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 * (attempt + 1))
            continue
        print("Request failed:", r.status_code, r.text[:200])
        return None
    return None


def load_state():
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(phase, cursor, pages):
    temp = STATE_FILE + ".tmp"
    with open(temp, "w") as f:
        json.dump({"phase": phase, "cursor": cursor, "pages": pages}, f)
    os.replace(temp, STATE_FILE)


def rows_in_file():
    if not os.path.exists(MARKETS_FILE):
        return 0
    with open(MARKETS_FILE, encoding="utf-8") as f:
        return max(0, sum(1 for _ in f) - 1)


def to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def flatten(m, source):
    events = m.get("events") or []
    ev = events[0] if events else {}
    series_list = ev.get("series") or []
    sr = series_list[0] if series_list else {}
    tags = [t.get("label") for t in (m.get("tags") or []) if t.get("label")]
    volume = m.get("volumeNum")
    if volume is None:
        volume = m.get("volume")
    return {
        "id": m.get("id"),
        "condition_id": m.get("conditionId"),
        "slug": m.get("slug"),
        "question": m.get("question"),
        "event_id": ev.get("id"),
        "event_slug": ev.get("slug"),
        "event_title": ev.get("title"),
        "series_slug": sr.get("slug"),
        "series_title": sr.get("title"),
        "category": m.get("category") or ev.get("category"),
        "tags": "|".join(tags),
        "outcomes": m.get("outcomes"),
        "outcome_prices": m.get("outcomePrices"),
        "closed": m.get("closed"),
        "active": m.get("active"),
        "uma_resolution_status": m.get("umaResolutionStatus"),
        "start_date": m.get("startDate"),
        "end_date": m.get("endDate"),
        "closed_time": m.get("closedTime"),
        "created_at": m.get("createdAt"),
        "volume": to_float(volume),
        "liquidity": to_float(m.get("liquidityNum")),
        "last_trade_price": m.get("lastTradePrice"),
        "sports_market_type": m.get("sportsMarketType"),
        "game_id": m.get("gameId"),
        "source": source,
    }


print("Saving files to:", FOLDER)

saved = rows_in_file()
state = load_state()
start_index = 0
cursor = ""
pages = 0

if state is None:
    if os.path.exists(MARKETS_FILE):
        print(f"\nFound {MARKETS_FILE} but no progress file, so I can't tell where it stopped.")
        print("Rename or delete polymarket_markets.csv to start a fresh download.")
        raise SystemExit
    print("Starting a fresh download")
else:
    if state["phase"] == "complete":
        print("Download already finished. Delete polymarket_progress.json and polymarket_markets.csv to start over.")
        raise SystemExit
    start_index = PHASE_NAMES.index(state["phase"])
    cursor = state["cursor"]
    pages = state["pages"]
    print(f"RESUMING: {saved:,} markets already saved. Picking up {state['phase']} at page {pages}.")

for i in range(start_index, len(PHASES)):
    name, extra = PHASES[i]
    print(f"\nPhase {i + 1} of {len(PHASES)}: {name}")
    while True:
        params = {"limit": PAGE_SIZE, "include_tag": "true"}
        params.update(extra)
        if cursor:
            params["after_cursor"] = cursor
        data = get("/markets/keyset", params)
        if data is None:
            print("Stopping. Run the script again to resume from where it left off.")
            raise SystemExit
        markets = data.get("markets", [])
        rows = []
        for m in markets:
            row = flatten(m, name)
            if row["volume"] <= 0:
                continue
            rows.append(row)
        if rows:
            df = pd.DataFrame(rows, columns=COLUMNS)
            df.to_csv(MARKETS_FILE, mode="a", header=not os.path.exists(MARKETS_FILE), index=False)
            saved += len(rows)
        cursor = data.get("next_cursor") or ""
        pages += 1
        save_state(name, cursor, pages)
        if pages % 25 == 0:
            print(f"  {name}: page {pages}, {saved:,} markets saved in total")
        if not cursor or len(markets) == 0:
            break
        time.sleep(0.1)
    print(f"  {name} finished after {pages} pages")
    cursor = ""
    pages = 0
    next_phase = PHASE_NAMES[i + 1] if i + 1 < len(PHASES) else "complete"
    save_state(next_phase, "", 0)

print(f"\nDone. {saved:,} markets saved to {MARKETS_FILE}")
