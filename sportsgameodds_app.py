import streamlit as st
import requests
import pandas as pd
import json
import os
from datetime import datetime
from dropbox import Dropbox, files

# -----------------------------
# CONFIG
# -----------------------------
API_KEY_PRIMARY = "4ab2006b05f90755906bd881ecfaee3a"
API_KEY_SECONDARY = "f5b3fb275ce1c78baa3bed7fab495f71"
BASE_URL = "https://api.sportsgameodds.com/v2/events"
LEAGUE_ID = "NFL"
LIMIT = 200  # increase in case you want more players
CACHE_FILE = "/odds_cache.json"
CACHE_MAX_AGE_MINUTES = 10080  # 7 days

DEFAULT_SCORING = {
    "Pass Yards": 0.04,
    "Pass TDs": 4,
    "Rush Yards": 0.1,
    "Rush TDs": 6,
    "Receptions": 1,
    "Receiving Yards": 0.1,
    "Receiving TDs": 6,
    "Total Touchdowns": 6
}
STATS = list(DEFAULT_SCORING.keys())

MARKET_MAP = {
    "Pass Yards": ["Passing Yards", "Pass Yds"],
    "Pass TDs": ["Passing TDs", "Pass Touchdowns", "Passing Touchdowns"],
    "Rush Yards": ["Rushing Yards", "Rush Yds"],
    "Rush TDs": ["Rushing TDs", "Rush Touchdowns"],
    "Receptions": ["Receptions"],
    "Receiving Yards": ["Receiving Yards", "Rec Yds"],
    "Receiving TDs": ["Receiving TDs", "Rec Touchdowns"],
    # numeric total TD aliases (YN yes/no markets handled specially)
    "Total Touchdowns": ["Player Touchdowns", "Any Touchdowns", "Any TDs", "Touchdowns"]
}

# Patterns to skip (alt/partials/home/away/etc)
SKIP_PATTERNS = [
    "alt", "alternate", "1h", "2h", "first half", "second half", "half",
    "quarter", "q1", "q2", "q3", "q4", "home", "away", "team", "team total", "home team",
    "away team", "odds to", "ou", "open", "alternate"
]

# -----------------------------
# DROPBOX CLIENT
# -----------------------------
DROPBOX_APP_KEY = os.environ.get("DROPBOX_APP_KEY")
DROPBOX_APP_SECRET = os.environ.get("DROPBOX_APP_SECRET")
DROPBOX_REFRESH_TOKEN = os.environ.get("DROPBOX_REFRESH_TOKEN")

if not DROPBOX_APP_KEY or not DROPBOX_APP_SECRET or not DROPBOX_REFRESH_TOKEN:
    st.error("Missing Dropbox credentials. Set DROPBOX_APP_KEY, DROPBOX_APP_SECRET, DROPBOX_REFRESH_TOKEN.")
    st.stop()

dbx = Dropbox(
    oauth2_refresh_token=DROPBOX_REFRESH_TOKEN,
    app_key=DROPBOX_APP_KEY,
    app_secret=DROPBOX_APP_SECRET
)

# -----------------------------
# HELPERS
# -----------------------------
def clean_player_name(pid):
    parts = pid.split("_")
    return " ".join(parts[:-2]).title() if len(parts) >= 2 else pid

def fetch_api(api_key):
    headers = {"x-api-key": api_key}
    params = {"leagueID": LEAGUE_ID, "oddsAvailable": "true", "limit": LIMIT}
    try:
        r = requests.get(BASE_URL, headers=headers, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        if not data.get("success", False):
            return None
        return data.get("data", [])
    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching odds: {e}")
        return None

def american_to_prob(odds):
    try:
        odds = int(odds)
        return 100 / (odds + 100) if odds > 0 else -odds / (-odds + 100)
    except:
        return None

def load_cache(max_age_minutes=CACHE_MAX_AGE_MINUTES):
    try:
        _, res = dbx.files_download(CACHE_FILE)
        payload = json.loads(res.content.decode("utf-8"))
        ts = pd.to_datetime(payload.get("timestamp"))
        if (pd.Timestamp.now() - ts).total_seconds() < max_age_minutes * 60:
            return payload.get("data", [])
    except:
        return []
    return []

def save_cache(data):
    payload = {"timestamp": datetime.now().isoformat(), "data": data}
    try:
        dbx.files_upload(
            json.dumps(payload, indent=2).encode("utf-8"),
            CACHE_FILE,
            mode=files.WriteMode.overwrite
        )
    except Exception as e:
        st.error(f"Failed to save cache to Dropbox: {e}")

def average_odds(odds_list):
    probs = []
    for o in odds_list:
        if o in ["N/A", None, ""]:
            continue
        p = american_to_prob(o)
        if p is not None:
            probs.append(p)
    return sum(probs)/len(probs) if probs else 0.5

def normalize(s: str):
    return (s or "").lower().strip()

def contains_skip_pattern(text: str):
    t = normalize(text)
    for p in SKIP_PATTERNS:
        if p in t:
            return True
    return False

def market_text_matches(aliases, market_text, market_raw):
    m_clean = ''.join([c for c in normalize(market_text) if c.isalpha()])
    raw_clean = ''.join([c for c in normalize(market_raw) if c.isalpha()])
    for a in aliases:
        a_clean = ''.join([c for c in normalize(a) if c.isalpha()])
        if a_clean and (a_clean in m_clean or a_clean in raw_clean):
            return True
    return False

def find_market(stat, player_rows):
    """Find a single, game-level matching market for stat (skip partials/alt/home/away)."""
    aliases = MARKET_MAP.get(stat, [stat])
    # first pass: precise textual match excluding skipped markets
    for r in player_rows:
        market = r.get("Market","") or ""
        raw = r.get("MarketRaw","") or ""
        if contains_skip_pattern(market) or contains_skip_pattern(raw):
            continue
        if market_text_matches(aliases, market, raw):
            return r
    # fallback: looser match but still exclude skip patterns
    for r in player_rows:
        market = r.get("Market","") or ""
        raw = r.get("MarketRaw","") or ""
        if contains_skip_pattern(market) or contains_skip_pattern(raw):
            continue
        m = normalize(market)
        for a in aliases:
            if normalize(a) in m:
                return r
    return None

def find_total_td_yes_row(player_rows):
    """Find the Y/N 'Yes' market for player total touchdowns if present (player-specific)."""
    if not player_rows:
        return None
    for r in player_rows:
        raw = (r.get("MarketRaw") or "").lower()
        market = (r.get("Market") or "").lower()
        # look for the API pattern that includes 'yn-yes' or 'yn_yes' or 'yn-yes' text
        if ("yn-yes" in raw or "yn_yes" in raw or "yn-yes" in market or "yn_yes" in market or (" yn " in raw and " yes" in raw)):
            if "touchdown" in raw or "touchdowns" in raw or "touchdown" in market or "touchdowns" in market:
                return r
        # textual matches like "anytime touchdowns yes"
        if "anytime" in raw and "yes" in raw and "touchdown" in raw:
            return r
        if "anytime" in market and "yes" in market and "touchdown" in market:
            return r
        if "any touchdowns" in market and "yes" in market:
            return r
    return None

def get_total_touchdowns_line_and_prob_from_yes(player_rows):
    """
    Return (line_val, prob_yes) for Total Touchdowns using the YES Y/N market when available.
    Projection value defaults to 0.5 (we keep that behavior) but probability uses the yes-market.
    """
    yes_row = find_total_td_yes_row(player_rows)
    if yes_row:
        prob_yes = yes_row.get("AvgProb", None)
        if prob_yes is None or prob_yes == 0:
            od_list = []
            for k in ["DraftKings","FanDuel","Caesars","ESPNBet","BetMGM"]:
                val = yes_row.get(k)
                if val not in [None, "N/A", ""]:
                    od_list.append(val)
            prob_yes = average_odds(od_list) if od_list else 0.5
        # numeric line if present; for Y/N often not meaningful — still capture if present
        line_val = yes_row.get("Line", 0.5)
        try:
            line_val = float(line_val) if line_val not in [None, ""] else 0.5
        except:
            line_val = 0.5
        return line_val, float(prob_yes)
    # fallback to numeric Player Touchdowns market
    td_row = find_market("Total Touchdowns", player_rows)
    if td_row:
        return td_row.get("Line", 0.5), td_row.get("AvgProb", 0.5)
    return 0.5, 0.5

# -----------------------------
# STREAMLIT SETUP
# -----------------------------
st.set_page_config(layout="wide")
st.title("NFL Player Prop Odds & Fantasy Projection")

# initialize saved projections storage (list of dicts), one per player
if "projections" not in st.session_state:
    st.session_state.projections = []  # each entry: dict with stat, stat_prob, Position, Player, Total Points

# -----------------------------
# FETCH / CACHE DATA (from Dropbox or API)
# -----------------------------
use_cache = st.sidebar.checkbox("Load cached data instead of fetching API", value=True)
odds_data = []
if use_cache:
    odds_data = load_cache()
    if not odds_data:
        st.sidebar.info("No cached data found — fetch from API below.")
else:
    st.sidebar.info("Will fetch from API when you click a Fetch button.")

col1, col2 = st.sidebar.columns(2)
with col1:
    if st.button("Fetch Primary API"):
        fetched = fetch_api(API_KEY_PRIMARY)
        if fetched:
            odds_data = fetched
            save_cache(odds_data)
            st.sidebar.success("Fetched primary API and saved to cache.")
        else:
            st.sidebar.warning("Primary API failed.")
with col2:
    if st.button("Fetch Secondary API"):
        fetched = fetch_api(API_KEY_SECONDARY)
        if fetched:
            odds_data = fetched
            save_cache(odds_data)
            st.sidebar.success("Fetched secondary API and saved to cache.")
        else:
            st.sidebar.warning("Secondary API failed.")

if not odds_data:
    # If still empty, try loading cache regardless
    odds_data = load_cache()
    if not odds_data:
        st.warning("No odds data available. Fetch API or ensure cache exists.")
        st.stop()

# -----------------------------
# PARSE / CLEAN ROWS (single canonical set)
# -----------------------------
rows = []
for event in odds_data:
    odds_list_raw = event.get("odds") or []
    odds_list = list(odds_list_raw.values()) if isinstance(odds_list_raw, dict) else odds_list_raw
    players_list = event.get("players") or []

    # build player map
    player_map = {}
    for p in players_list:
        if isinstance(p, dict):
            pid = p.get("playerID") or p.get("statEntityID")
            if pid:
                first = p.get("firstName")
                last = p.get("lastName")
                full_name = f"{first} {last}" if first and last else clean_player_name(pid)
                player_map[pid] = {"name": full_name, "position": p.get("position", "")}
        elif isinstance(p, str):
            player_map[p] = {"name": clean_player_name(p), "position": ""}

    for odds_item in odds_list:
        if not isinstance(odds_item, dict):
            continue
        pid = odds_item.get("playerID") or odds_item.get("statEntityID")
        if not pid:
            continue
        player_info = player_map.get(pid, {"name": clean_player_name(pid), "position": ""})

        # market info (prefer marketName, fallback to market/statName)
        line = (odds_item.get("bookOverUnder") or odds_item.get("fairOverUnder") or
                odds_item.get("openBookOverUnder") or odds_item.get("openFairOverUnder") or "")
        market_name = odds_item.get("marketName") or odds_item.get("market") or odds_item.get("statName") or ""
        market_display = f"{market_name} {line}" if line else market_name

        # collect odds for AvgProb
        odds_by_book = odds_item.get("byBookmaker") or {}
        all_odds = []
        def collect_od(v):
            if v is None:
                return
            if isinstance(v, dict):
                # common key 'odds' might be numeric or string
                if "odds" in v and isinstance(v["odds"], (int, str)):
                    all_odds.append(v["odds"])
                else:
                    for val in v.values():
                        if isinstance(val, (int, str)):
                            all_odds.append(val)
            else:
                all_odds.append(v)

        collect_od(odds_item.get('bookOdds','N/A'))
        collect_od(odds_item.get('fairOdds','N/A'))
        collect_od(odds_item.get('openBookOdds','N/A'))
        collect_od(odds_item.get('openFairOdds','N/A'))
        collect_od(odds_by_book.get("draftkings", {}).get("odds", "N/A"))
        collect_od(odds_by_book.get("fanduel", {}).get("odds", "N/A"))
        collect_od(odds_by_book.get("caesars", {}).get("odds", "N/A"))
        collect_od(odds_by_book.get("espnbet", {}).get("odds", "N/A"))
        collect_od(odds_by_book.get("betmgm", {}).get("odds", "N/A"))

        avg_prob = average_odds(all_odds)

        # market raw string for pattern detection (like 'touchdowns-<id>-game-yn-yes')
        try:
            market_raw = odds_item.get("marketKey") or odds_item.get("market") or json.dumps(odds_item)
        except Exception:
            market_raw = str(odds_item)

        rows.append({
            "Player": player_info["name"],
            "Position": player_info["position"],
            "Market": market_display,
            "MarketRaw": market_raw,
            "Line": float(line) if (line not in ["", None]) else 0.0,
            "AvgProb": avg_prob,
            "DraftKings": odds_by_book.get("draftkings", {}).get("odds", "N/A"),
            "FanDuel": odds_by_book.get("fanduel", {}).get("odds", "N/A"),
            "Caesars": odds_by_book.get("caesars", {}).get("odds", "N/A"),
            "ESPNBet": odds_by_book.get("espnbet", {}).get("odds", "N/A"),
            "BetMGM": odds_by_book.get("betmgm", {}).get("odds", "N/A"),
        })

if not rows:
    st.warning("No player prop odds found.")
    st.stop()

# -----------------------------
# SIDEBAR CONTROLS (scoring)
# -----------------------------
st.sidebar.title("Controls")
players = sorted(set(r["Player"] for r in rows))
selected_player = st.sidebar.selectbox("Select a player", players)

st.sidebar.subheader("Scoring Settings")
for stat in STATS:
    st.sidebar.number_input(
        stat + " (pts/unit)",
        value=float(DEFAULT_SCORING[stat]),
        step=0.01,
        key=f"scoring__{stat}"
    )

# -----------------------------
# DISPLAY SELECTED PLAYER PROPS
# -----------------------------
player_rows = [r for r in rows if r["Player"] == selected_player]
df_odds = pd.DataFrame(player_rows).sort_values("Market").reset_index(drop=True)
df_odds_display = df_odds.drop(columns=["Position","MarketRaw"], errors="ignore")
st.subheader(f"Prop Odds for {selected_player}")
st.dataframe(df_odds_display)

# -----------------------------
# FANTASY PROJECTION INPUTS (selected player)
# -----------------------------
proj_cols = st.columns(2)
projected_stats = {}
projected_probs = {}
pos = player_rows[0].get("Position", "") if player_rows else ""

for stat in STATS:
    if stat == "Total Touchdowns":
        # pull probability from Y/N YES market if present; keep projected numeric default 0.5
        _, yes_prob = get_total_touchdowns_line_and_prob_from_yes(player_rows)
        line_val = 0.5  # user-visible default projected count
        avg_prob = yes_prob if yes_prob is not None else 0.5
    else:
        row = find_market(stat, player_rows)
        line_val = row["Line"] if row else 0.0
        avg_prob = row["AvgProb"] if row else 0.5

    # projection input and probability input
    projected_stats[stat] = proj_cols[0].number_input(
        f"Projected {stat}",
        value=float(line_val),
        step=0.1,
        key=f"proj_stat__{stat}"
    )
    projected_probs[stat] = proj_cols[1].number_input(
        f"Probability for {stat}",
        value=float(avg_prob),
        step=0.01,
        key=f"proj_prob__{stat}"
    )

# -----------------------------
# CALCULATE PROJECTED FANTASY POINTS (selected player)
# -----------------------------
weighted_points = {}
for stat in STATS:
    pts_per_unit = st.session_state[f"scoring__{stat}"]
    weighted_points[stat] = projected_stats[stat] * pts_per_unit * projected_probs[stat]

total_points = sum(weighted_points.values())
st.subheader(f"Projected Fantasy Points: {total_points:.2f}")
st.json(weighted_points)

# -----------------------------
# SAVE / REMOVE PROJECTIONS (replace existing)
# -----------------------------
if st.button("Save Projection"):
    # remove any existing saved projection for that player, then append current
    st.session_state.projections = [p for p in st.session_state.projections if p.get("Player") != selected_player]
    record = {
        "Player": selected_player,
        "Position": pos or "",
    }
    for s in STATS:
        record[s] = float(projected_stats[s])
        record[f"{s}_prob"] = float(projected_probs[s])
    record["Total Points"] = float(total_points)
    st.session_state.projections.append(record)
    st.success(f"Saved projection for {selected_player}.")

if st.button("Clear Projection for Player"):
    st.session_state.projections = [p for p in st.session_state.projections if p.get("Player") != selected_player]
    st.info(f"Cleared saved projection for {selected_player}.")

# -----------------------------
# TOP 150 LEADERBOARD (mirror projection fields)
# -----------------------------
# For each player: prefer saved projection; otherwise build a projection using the same logic as the inputs
players_all = sorted(set(r["Player"] for r in rows))
df_auto = []
for p in players_all:
    p_rows = [r for r in rows if r["Player"] == p]
    saved = next((x for x in st.session_state.projections if x.get("Player") == p), None)

    rec = {"Player": p, "Position": (p_rows[0].get("Position","") if p_rows else "")}

    for stat in STATS:
        if saved:
            # mirror saved values (so Top150 reflects what you saved)
            val = saved.get(stat, None)
            prob = saved.get(f"{stat}_prob", None)
            # fallback to market-derived if missing in saved
            if val is None:
                if stat == "Total Touchdowns":
                    _, ap = get_total_touchdowns_line_and_prob_from_yes(p_rows)
                    val = 0.5
                    prob = ap
                else:
                    row = find_market(stat, p_rows)
                    val = row["Line"] if row else 0.0
                    prob = row["AvgProb"] if row else 0.5
        else:
            # no saved projection; compute same logic as inputs
            if stat == "Total Touchdowns":
                _, ap = get_total_touchdowns_line_and_prob_from_yes(p_rows)
                val = 0.5
                prob = ap
            else:
                row = find_market(stat, p_rows)
                val = row["Line"] if row else 0.0
                prob = row["AvgProb"] if row else 0.5

        # numeric coercion
        try:
            rec[stat] = float(val)
        except:
            rec[stat] = 0.0
        try:
            rec[f"{stat}_prob"] = float(prob)
        except:
            rec[f"{stat}_prob"] = 0.5

    # compute total projected points (mirror formula)
    total_pts = 0.0
    for stat in STATS:
        pts_per = st.session_state[f"scoring__{stat}"]
        total_pts += rec[stat] * pts_per * rec[f"{stat}_prob"]
    rec["Projected Points"] = total_pts
    df_auto.append(rec)

df_auto_top150 = pd.DataFrame(df_auto).sort_values("Projected Points", ascending=False).head(150).reset_index(drop=True)

# Ensure columns and ordering requested by you
cols_order = [
    "Player", "Projected Points",
    "Pass Yards", "Pass TDs", "Rush Yards", "Rush TDs",
    "Receptions", "Receiving Yards", "Receiving TDs", "Total Touchdowns"
]
# Add missing columns with zeros if necessary
for c in cols_order:
    if c not in df_auto_top150.columns:
        df_auto_top150[c] = 0.0

df_display_top = df_auto_top150[cols_order].copy()

st.subheader("Top 150 Projected Fantasy Players")

# Position filter
positions_present = sorted(set(df_auto_top150["Position"].fillna("").unique()))
positions_present = [p for p in positions_present if p]  # drop empty
pos_sel = st.multiselect("Filter positions (Top 150)", options=["All"] + positions_present, default=["All"])
if pos_sel and "All" not in pos_sel:
    df_display_top = df_auto_top150[df_auto_top150["Position"].isin(pos_sel)][cols_order].reset_index(drop=True)

st.dataframe(df_display_top)

# -----------------------------
# OPTIONAL: show saved projections (helpful)
# -----------------------------
if st.session_state.projections:
    st.subheader("Saved Projections (session)")
    df_saved = pd.DataFrame(st.session_state.projections).sort_values("Total Points", ascending=False).reset_index(drop=True)
    st.dataframe(df_saved)

# -----------------------------
# REFRESH DATA
# -----------------------------
if st.button("Refresh Data"):
    try:
        dbx.files_delete_v2(CACHE_FILE)
    except:
        pass
    st.experimental_rerun()
