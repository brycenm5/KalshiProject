import os
import json
import pandas as pd

FOLDER = os.path.dirname(os.path.abspath(__file__))
MARKETS_FILE = os.path.join(FOLDER, "polymarket_markets.csv")
YEARS = 5

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 30)
pd.set_option("display.max_colwidth", 60)
pd.set_option("display.float_format", lambda x: f"{x:,.3f}")

# Polymarket has tags instead of a single clean category. These are checked in
# order, so a market tagged both Sports and Politics lands in Sports.
CATEGORY_PRIORITY = ["sports", "esports", "politics", "elections", "crypto", "finance",
                     "economy", "business", "tech", "science", "climate & science",
                     "geopolitics", "world", "weather", "pop culture", "culture", "mentions"]
IGNORE_TAGS = {"all", "featured", "recurring", "hide from new", "trending", "new", "games", ""}


def header(text):
    print("\n" + "=" * 90)
    print(text)
    print("=" * 90)


def to_dt(col):
    try:
        return pd.to_datetime(col, utc=True, errors="coerce", format="mixed")
    except (TypeError, ValueError):
        return pd.to_datetime(col, utc=True, errors="coerce")


def pick_category(tags, fallback):
    labels = [t.strip() for t in str(tags).split("|")] if isinstance(tags, str) else []
    lowered = {t.lower(): t for t in labels}
    for key in CATEGORY_PRIORITY:
        if key in lowered:
            return lowered[key]
    for t in labels:
        if t.lower() not in IGNORE_TAGS:
            return t
    if isinstance(fallback, str) and fallback.strip():
        return fallback.strip()
    return "Unknown"


def winner(outcomes, prices):
    """Return the winning outcome (lowercase) for a resolved market, else None."""
    try:
        outs = json.loads(outcomes)
        pr = [float(p) for p in json.loads(prices)]
    except (TypeError, ValueError):
        return None
    if not outs or len(outs) != len(pr):
        return None
    i = max(range(len(pr)), key=lambda k: pr[k])
    if pr[i] < 0.99:
        return None
    return str(outs[i]).strip().lower()


df = pd.read_csv(MARKETS_FILE, low_memory=False)

df = df.drop_duplicates("id", keep="last")
df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)   # USD (USDC) traded
df["liquidity"] = pd.to_numeric(df["liquidity"], errors="coerce").fillna(0)
df["closed"] = df["closed"].astype(str).str.lower() == "true"

df["start_time"] = to_dt(df["start_date"]).fillna(to_dt(df["created_at"]))
df["close_time"] = to_dt(df["closed_time"]).fillna(to_dt(df["end_date"]))

df["event_key"] = df["event_slug"].fillna(df["slug"]).fillna(df["id"].astype(str))
df["title"] = df["question"].fillna(df["event_title"]).fillna(df["slug"])
df["series_title"] = df["series_title"].fillna(df["event_title"]).fillna(df["title"])
df["category"] = [pick_category(t, c) for t, c in zip(df["tags"], df["category"])]
df["yes_sub_title"] = ""

now = pd.Timestamp.now(tz="UTC")
start = now - pd.DateOffset(years=YEARS)
finished = df[df["closed"] & (df["close_time"] >= start) & (df["close_time"] <= now)].copy()
still_open = df[~df["closed"]].copy()

finished["result"] = [winner(o, p) for o, p in zip(finished["outcomes"], finished["outcome_prices"])]
finished["result"] = finished["result"].fillna("unresolved")
still_open["result"] = "open"

finished["year"] = finished["close_time"].dt.year
finished["month"] = finished["close_time"].dt.tz_localize(None).dt.to_period("M")
finished["day"] = finished["close_time"].dt.date
finished["days_open"] = (finished["close_time"] - finished["start_time"]).dt.total_seconds() / 86400


header("DATASET OVERVIEW")
print("Markets with trading volume in file:", len(df))
print("Markets that closed in the last {} years: {}".format(YEARS, len(finished)))
print("Markets still open:", len(still_open))
print("Window: {} to {}".format(start.date(), now.date()))
print("Earliest close in window:", finished["close_time"].min())
print("Total USD volume (closed markets): ${:,.0f}".format(finished["volume"].sum()))
print("Total USD volume (still open markets): ${:,.0f}".format(still_open["volume"].sum()))
print("Categories found:", finished["category"].nunique())
print("Note: volume is lifetime USD volume, counted in the month a market closed, not the month each trade happened.")
print("Note: the first and last years in the window are partial years.")


header("1. IS THERE A SPIKE? YEARLY TOTALS")
yearly = finished.groupby("year").agg(
    markets=("id", "count"),
    events=("event_key", "nunique"),
    usd_volume=("volume", "sum"),
    avg_per_market=("volume", "mean"),
)
yearly["growth_vs_prior_year"] = yearly["usd_volume"].pct_change()
yearly["share_of_5yr_total"] = yearly["usd_volume"] / yearly["usd_volume"].sum()
print(yearly.round(3).to_string())

first = yearly["usd_volume"].iloc[0]
last = yearly["usd_volume"].iloc[-1]
print("\nLatest year vs first year: {:,.1f}x".format(last / first if first else float("nan")))


header("2. MONTH BY MONTH VOLUME AND SPIKE DETECTION")
monthly = finished.groupby("month").agg(
    markets=("id", "count"),
    usd_volume=("volume", "sum"),
)
monthly["mom_growth"] = monthly["usd_volume"].pct_change().replace([float("inf"), float("-inf")], float("nan"))
monthly["rolling_6mo_avg"] = monthly["usd_volume"].rolling(6, min_periods=3).mean().shift(1)
monthly["vs_6mo_avg"] = monthly["usd_volume"] / monthly["rolling_6mo_avg"]
print(monthly.round(3).to_string())

g = monthly["mom_growth"].dropna()
monthly["growth_z"] = (monthly["mom_growth"] - g.mean()) / g.std()
spikes = monthly[monthly["growth_z"] > 2].sort_values("growth_z", ascending=False)
print("\nSpike months (month-over-month growth more than 2 std above average):")
print(spikes[["markets", "usd_volume", "mom_growth", "vs_6mo_avg", "growth_z"]].round(2).to_string())

print("\nTop 10 biggest months ever by USD volume:")
print(monthly.sort_values("usd_volume", ascending=False)[["markets", "usd_volume"]].head(10).to_string())

for month in spikes.index[:5]:
    drivers = finished[finished["month"] == month].groupby(["category", "series_title"])["volume"].sum()
    print(f"\nWhat drove {month}:")
    print(drivers.sort_values(ascending=False).head(5).to_string())


header("3. CATEGORY MIX OVER TIME")
cat_year = finished.pivot_table(index="category", columns="year", values="volume", aggfunc="sum", fill_value=0)
cat_share = cat_year / cat_year.sum()
cat_share = cat_share.loc[cat_year.sum(axis=1).sort_values(ascending=False).index]
print("Share of each year's volume by category:")
print(cat_share.round(3).to_string())

print("\nUSD volume by category by year:")
print(cat_year.loc[cat_share.index].round(0).to_string())

first_year = cat_year.columns.min()
last_full = cat_year.columns.max()
growth = (cat_year[last_full] / cat_year[first_year].replace(0, float("nan"))).dropna().sort_values(ascending=False)
print(f"\nFastest growing categories ({first_year} to {last_full}):")
print(growth.head(10).round(1).to_string())


header("4. BIGGEST SINGLE MARKETS EVER")
everything = pd.concat([finished, still_open])
everything["state"] = everything["closed"].map({True: "closed", False: "open"})
top_markets = everything.sort_values("volume", ascending=False).head(25)
print(top_markets[["category", "title", "close_time", "result", "volume", "state"]].to_string(index=False))


header("5. BIGGEST EVENTS EVER (ALL MARKETS IN AN EVENT ADDED TOGETHER)")
events = everything.groupby("event_key").agg(
    category=("category", "first"),
    title=("event_title", "first"),
    markets=("id", "count"),
    usd_volume=("volume", "sum"),
    close=("close_time", "max"),
    state=("state", "first"),
).sort_values("usd_volume", ascending=False)
print(events.head(25).to_string())


header("6. BIGGEST GAMES AND SPORTS EVENTS EVER BET ON")
sports = everything[everything["category"].str.lower().isin(["sports", "esports"])]
sport_events = sports.groupby("event_key").agg(
    series=("series_title", "first"),
    title=("event_title", "first"),
    markets=("id", "count"),
    usd_volume=("volume", "sum"),
    close=("close_time", "max"),
).sort_values("usd_volume", ascending=False)
print(sport_events.head(25).to_string())

sport_series = sports.groupby("series_title").agg(events=("event_key", "nunique"), usd_volume=("volume", "sum"))
print("\nSports series by total volume (which leagues/sports people bet on most):")
print(sport_series.sort_values("usd_volume", ascending=False).head(20).to_string())

total_month = finished.groupby("month")["volume"].sum()
sports_month = finished[finished["category"].str.lower().isin(["sports", "esports"])].groupby("month")["volume"].sum()
print("\nSports share of monthly volume:")
print((sports_month / total_month).fillna(0).round(3).to_string())


header("7. BIGGEST SERIES OVERALL (RECURRING MARKET TYPES)")
top_series = everything.groupby(["series_title", "category"]).agg(
    events=("event_key", "nunique"),
    markets=("id", "count"),
    usd_volume=("volume", "sum"),
).sort_values("usd_volume", ascending=False)
print(top_series.head(25).to_string())


header("8. BUSIEST DAYS")
daily = finished.groupby("day").agg(markets=("id", "count"), usd_volume=("volume", "sum"))
print("Top 15 days by USD volume settling that day:")
top_days = daily.sort_values("usd_volume", ascending=False).head(15)
for day, row in top_days.iterrows():
    biggest = finished[finished["day"] == day].sort_values("volume", ascending=False).iloc[0]
    print(f"{day}  usd_volume={row['usd_volume']:>16,.0f}  markets={row['markets']:>6}  biggest: {biggest['title']}")


header("9. HOW MARKETS RESOLVED")
print("Resolution status of all closed markets (Yes/No, other outcome like a team name, or unresolved):")
finished["result_type"] = finished["result"].where(finished["result"].isin(["yes", "no", "unresolved"]), "other outcome")
print(finished["result_type"].value_counts().to_string())

resolved = finished[finished["result"].isin(["yes", "no"])]
res = resolved.groupby("category").agg(
    markets=("id", "count"),
    yes_rate=("result", lambda x: (x == "yes").mean()),
)
print("\nShare of Yes/No markets that resolved YES by category:")
print(res.sort_values("markets", ascending=False).round(3).to_string())

weighted = resolved.groupby("result")["volume"].sum()
print("\nUSD volume on markets that resolved YES vs NO:")
print(weighted.to_string())


header("10. HOW LONG MARKETS STAY OPEN")
life = finished[finished["days_open"] >= 0].groupby("category")["days_open"].agg(["count", "median", "mean", "max"])
print(life.sort_values("count", ascending=False).round(2).to_string())

print("\nVolume by how long the market was open:")
finished["life_bucket"] = pd.cut(finished["days_open"], bins=[-1, 1, 7, 30, 180, 100000],
                                 labels=["under 1 day", "1-7 days", "1-4 weeks", "1-6 months", "6+ months"])
print(finished.groupby("life_bucket", observed=True).agg(markets=("id", "count"), usd_volume=("volume", "sum")).to_string())


header("11. CONCENTRATION - DO A FEW MARKETS DOMINATE?")
sorted_vol = finished["volume"].sort_values(ascending=False)
total = sorted_vol.sum()
for pct in [0.001, 0.01, 0.1]:
    n = max(1, int(len(sorted_vol) * pct))
    print("Top {:.1%} of markets ({:,}) hold {:.1%} of all volume".format(pct, n, sorted_vol.head(n).sum() / total))
print("Median market volume: ${:,.0f}".format(finished["volume"].median()))
print("Mean market volume: ${:,.0f}".format(finished["volume"].mean()))

for year in sorted(finished["year"].dropna().unique()):
    v = finished[finished["year"] == year]["volume"].sort_values(ascending=False)
    n = max(1, int(len(v) * 0.01))
    print(f"{int(year)}: top 1% of markets hold {v.head(n).sum() / v.sum():.1%} of volume")


header("12. OUTLIER MARKETS WITHIN EACH CATEGORY (2 STD RULE ON VOLUME)")
finished["cat_mean"] = finished.groupby("category")["volume"].transform("mean")
finished["cat_std"] = finished.groupby("category")["volume"].transform("std")
finished["vol_z"] = (finished["volume"] - finished["cat_mean"]) / finished["cat_std"]
outliers = finished[finished["vol_z"] > 2]
print("Markets flagged:", len(outliers))
print(outliers.groupby("category").size().sort_values(ascending=False).to_string())
print("\nMost extreme outlier in each category:")
extreme = outliers.sort_values("vol_z", ascending=False).groupby("category").head(1)
print(extreme[["category", "title", "volume", "vol_z"]].round(1).to_string(index=False))
