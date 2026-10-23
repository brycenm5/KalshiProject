import os
import time
import requests
import pandas as pd

BASE = "https://api.elections.kalshi.com/trade-api/v2"
FOLDER = os.path.dirname(os.path.abspath(__file__))
MARKETS_FILE = os.path.join(FOLDER, "kalshi_markets.csv")
SERIES_FILE = os.path.join(FOLDER, "kalshi_series.csv")
STATE_FILE = os.path.join(FOLDER, "kalshi_progress.txt")

KEEP = ["ticker", "event_ticker", "title", "yes_sub_title", "market_type", "status", "result",
        "open_time", "close_time", "settlement_ts", "volume_fp", "open_interest_fp",
        "last_price_dollars", "notional_value_dollars"]

PHASES = [
    ("historical", "/historical/markets", {}),
    ("settled", "/markets", {"status": "settled"}),
    ("closed", "/markets", {"status": "closed"}),
    ("open", "/markets", {"status": "open"}),
]


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
        parts = f.read().strip().split("|")
    if len(parts) != 3:
        return None
    return parts[0], parts[1], int(parts[2])


def save_state(phase, cursor, pages):
    temp = STATE_FILE + ".tmp"
    with open(temp, "w") as f:
        f.write(f"{phase}|{cursor}|{pages}")
    os.replace(temp, STATE_FILE)


def rows_in_file():
    if not os.path.exists(MARKETS_FILE):
        return 0
    with open(MARKETS_FILE, encoding="utf-8") as f:
        return max(0, sum(1 for line in f) - 1)


print("Saving files to:", FOLDER)

if not os.path.exists(SERIES_FILE):
    print("Downloading series list...")
    data = get("/series", {})
    if data:
        series = pd.DataFrame(data["series"])
        series = series[["ticker", "title", "category", "frequency"]]
        series.to_csv(SERIES_FILE, index=False)
        print("Series saved:", len(series))

phase_names = [p[0] for p in PHASES]
saved = rows_in_file()
state = load_state()
start_index = 0
cursor = ""
pages = 0

if state is None:
    if os.path.exists(MARKETS_FILE):
        print(f"\nFound {MARKETS_FILE} but no progress file, so I can't tell where it stopped.")
        print("Rename or delete kalshi_markets.csv to start a fresh download.")
        raise SystemExit
    print("Starting a fresh download")
else:
    done_phase, cursor, pages = state
    if done_phase == "complete":
        print("Download already finished. Delete kalshi_progress.txt and kalshi_markets.csv to start over.")
        raise SystemExit
    start_index = phase_names.index(done_phase)
    if cursor == "":
        start_index += 1
        pages = 0
    print(f"RESUMING: {saved:,} markets already saved. Picking up {done_phase} at page {pages}.")

for name, path, extra in PHASES[start_index:]:
    print(f"\nPhase {phase_names.index(name) + 1} of {len(PHASES)}: {name}")
    while True:
        params = {"limit": 1000, "mve_filter": "exclude"}
        params.update(extra)
        if cursor:
            params["cursor"] = cursor
        data = get(path, params)
        if data is None:
            print("Stopping. Run the script again to resume from where it left off.")
            raise SystemExit
        markets = data.get("markets", [])
        rows = []
        for m in markets:
            if float(m.get("volume_fp") or 0) <= 0:
                continue
            row = {k: m.get(k) for k in KEEP}
            row["source"] = name
            rows.append(row)
        if rows:
            df = pd.DataFrame(rows, columns=KEEP + ["source"])
            df.to_csv(MARKETS_FILE, mode="a", header=not os.path.exists(MARKETS_FILE), index=False)
            saved += len(rows)
        cursor = data.get("cursor", "")
        pages += 1
        save_state(name, cursor, pages)
        if pages % 25 == 0:
            print(f"  {name}: page {pages}, {saved:,} markets saved in total")
        if not cursor or len(markets) == 0:
            break
    print(f"  {name} finished after {pages} pages")
    cursor = ""
    pages = 0

save_state("complete", "", 0)
print(f"\nDone. {saved:,} markets saved to {MARKETS_FILE}")