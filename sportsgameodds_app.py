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
LIMIT = 200
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
    "Receptions": ["receiving_receptions"],
    "Receiving Yards": ["Receiving Yards", "Rec Yds"],
    "Receiving TDs": ["Receiving TDs", "Rec Touchdowns"],
    "Total Touchdowns": ["Player Touchdowns", "Any Touchdowns", "Any TDs", "Touchdowns"]
}

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
    """Convert API playerID like 'JOSH_ALLEN_1_NFL' to 'Josh Allen'."""
    parts = str(pid).split("_")
    if len(parts) >= 2:
        return " ".join(parts[:-2]).title()
    return pid.title()

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

def average_odds(odds_list):
    probs = []
    for o in odds_list:
        if o in ["N/A", None, ""]:
            continue
        p = american_to_prob(o)
        if p is not None:
            probs.append(p)
    return sum(probs)/len(probs) if probs else 0.5

def normalize(s):
    return str(s).lower().strip()

def market_text_matches(aliases, market_text, market_raw):
    m_clean = ''.join([c for c in normalize(market_text) if c.isalpha()])
    raw_clean = ''.join([c for c in normalize(market_raw) if c.isalpha()])
    for a in aliases:
        a_clean = ''.join([c for c in normalize(a) if c.isalpha()])
        if a_clean and (a_clean in m_clean or a_clean in raw_clean):
            return True
    return False

def skip_home_away_all(market_raw):
    if not market_raw:
        return False
    mr = market_raw.lower()
    tokens = ["-home", "_home", "-away", "_away", "-all", "_all"]
    return any(t in mr for t in tokens)

def find_market(stat, player_rows):
    aliases = MARKET_MAP.get(stat, [stat])
    for r in player_rows:
        m = r.get("Market","")
        raw = r.get("MarketRaw","")
        if market_text_matches(aliases, m, raw):
            if skip_home_away_all(raw) and not any("any" in a.lower() or "player" in a.lower() for a in aliases):
                continue
            return r
    for r in player_rows:
        m = normalize(r.get("Market",""))
        for a in aliases:
            if normalize(a) in m:
                if skip_home_away_all(r.get("MarketRaw","")) and "any" not in a.lower() and "player" not in a.lower():
                    continue
                return r
    return None

def find_total_td_yes_row(player_rows):
    if not player_rows:
        return None
    for r in player_rows:
        if r.get("StatID") == "touchdowns" and str(r.get("SideID","")).lower() == "yes":
            return r
    for r in player_rows:
        raw = (r.get("MarketRaw") or "").lower()
        market = (r.get("Market") or "").lower()
        if ("yn-yes" in raw or "yn_yes" in raw or "yn-yes" in market or "yn_yes" in market):
            if "touchdown" in raw or "touchdown" in market or "touchdowns" in raw or "touchdowns" in market:
                return r
        if "anytime" in raw and "yes" in raw and "touchdown" in raw:
            return r
        if "anytime" in market and "yes" in market and "touchdown" in market:
            return r
        if "any touchdowns" in market and "yes" in market:
            return r
    return None

def get_total_touchdowns_line_and_prob_from_yes(player_rows):
    yes_row = find_total_td_yes_row(player_rows)
    if yes_row:
        prob_yes = yes_row.get("AvgProb", None)
        if prob_yes is None or prob_yes == 0:
            od_list = []
            for k in ["DraftKings","FanDuel","Caesars","ESPNBet","BetMGM"]:
                v = yes_row.get(k)
                if v not in [None, "N/A", ""]:
                    od_list.append(v)
            prob_yes = average_odds(od_list) if od_list else 0.5
        line_val = yes_row.get("Line", 0.5)
        try:
            return (float(line_val) if line_val not in [None, ""] else 0.5, float(prob_yes))
        except:
            return (0.5, float(prob_yes))
    td_row = find_market("Total Touchdowns", player_rows)
    if td_row:
        return (td_row.get("Line", 0.5), td_row.get("AvgProb", 0.5))
    return (0.5, 0.5)

# -----------------------------
# STREAMLIT SETUP
# -----------------------------
st.set_page_config(layout="wide")
st.title("NFL Player Prop Odds & Fantasy Projection")

if "projections" not in st.session_state:
    st.session_state.projections = []

# -----------------------------
# FETCH / CACHE DATA
# -----------------------------
use_cache = st.sidebar.checkbox("Load cached data instead of fetching API")
odds_data = []

def load_cache_from_dropbox():
    try:
        _, res = dbx.files_download(CACHE_FILE)
        payload = json.loads(res.content.decode("utf-8"))
        ts = pd.to_datetime(payload.get("timestamp"))
        if (pd.Timestamp.now() - ts).total_seconds() < CACHE_MAX_AGE_MINUTES * 60:
            return payload.get("data", [])
        return []
    except Exception:
        return []

if use_cache:
    odds_data = load_cache_from_dropbox()
    if not odds_data:
        st.warning("No cached data found in Dropbox (or cache expired). Please fetch from API.")
else:
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Fetch Primary API"):
            data = fetch_api(API_KEY_PRIMARY)
            if data:
                odds_data = data
                payload = {"timestamp": datetime.now().isoformat(), "data": odds_data}
                try:
                    dbx.files_upload(json.dumps(payload, indent=2).encode("utf-8"), CACHE_FILE, mode=files.WriteMode.overwrite)
                    st.success("Saved cache to Dropbox.")
                except Exception as e:
                    st.error(f"Failed to save cache to Dropbox: {e}")
    with col2:
        if st.button("Fetch Secondary API"):
            data = fetch_api(API_KEY_SECONDARY)
            if data:
                odds_data = data
                payload = {"timestamp": datetime.now().isoformat(), "data": odds_data}
                try:
                    dbx.files_upload(json.dumps(payload, indent=2).encode("utf-8"), CACHE_FILE, mode=files.WriteMode.overwrite)
                    st.success("Saved cache to Dropbox.")
                except Exception as e:
                    st.error(f"Failed to save cache to Dropbox: {e}")

if not odds_data and use_cache:
    pass

if not odds_data:
    st.info("Use the sidebar buttons to fetch odds from API or uncheck 'Load cached data' and fetch.")
    st.stop()

# -----------------------------
# EXTRACT PLAYER PROPS
# -----------------------------
rows = []
for event in odds_data:
    odds_list_raw = event.get("odds") or []
    odds_list = list(odds_list_raw.values()) if isinstance(odds_list_raw, dict) else odds_list_raw
    players_list = event.get("players") or []

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
        line = (odds_item.get("bookOverUnder") or odds_item.get("fairOverUnder")
                or odds_item.get("openBookOverUnder") or odds_item.get("openFairOverUnder") or "")
        market_name = odds_item.get("marketName") or odds_item.get("market") or odds_item.get("statName") or "N/A"
        market_display = f"{market_name} {line}" if line else market_name

        odds_by_book = odds_item.get("byBookmaker") or {}
        all_odds = []
        def collect_od(v):
            if v is None: return
            if isinstance(v, dict):
                if "odds" in v and isinstance(v["odds"], (int,str)):
                    all_odds.append(v["odds"])
                else:
                    for val in v.values():
                        if isinstance(val,(int,str)):
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

        try:
            market_raw = odds_item.get("marketKey") or odds_item.get("market") or json.dumps(odds_item)
        except:
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
            "StatID": odds_item.get("statID"),
            "SideID": odds_item.get("sideID"),
        })

if not rows:
    st.warning("No player prop odds found in the returned data.")
    st.stop()

# -----------------------------
# SIDEBAR CONTROLS
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
# DISPLAY SELECTED PLAYER PROPS (CASE-INSENSITIVE FIX)
# -----------------------------
player_rows = [r for r in rows if r["Player"].lower() == selected_player.lower()]
df_odds = pd.DataFrame(player_rows).sort_values("Market")
df_odds_display = df_odds.drop(columns=["Position","MarketRaw"], errors="ignore")
st.subheader(f"Prop Odds for {selected_player}")
st.dataframe(df_odds_display)

# -----------------------------
# FANTASY PROJECTION INPUTS (CASE-INSENSITIVE FIX)
# -----------------------------
proj_cols = st.columns(2)
projected_stats = {}
projected_probs = {}

player_stat_row_map = {}
for stat in STATS:
    if stat == "Total Touchdowns":
        player_stat_row_map[stat] = get_total_touchdowns_line_and_prob_from_yes(player_rows)
    else:
        player_stat_row_map[stat] = find_market(stat, player_rows)

for stat in STATS:
    row = player_stat_row_map[stat]
    if stat == "Total Touchdowns":
        line_val = 0.5
        avg_prob = row[1] if row else 0.5
    else:
        line_val = row["Line"] if row else 0.0
        avg_prob = row["AvgProb"] if row else 0.5

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
# CALCULATE PROJECTED FANTASY POINTS
# -----------------------------
weighted_points = {}
for stat in STATS:
    pts_per_unit = st.session_state[f"scoring__{stat}"]
    weighted_points[stat] = projected_stats[stat] * pts_per_unit * projected_probs[stat]

total_points = sum(weighted_points.values())
st.subheader(f"Projected Fantasy Points: {total_points:.2f}")
st.json(weighted_points)

# -----------------------------
# SAVE / CLEAR PROJECTION
# -----------------------------
if st.button("Save Projection"):
    st.session_state.projections = [p for p in st.session_state.projections if p.get("Player").lower() != selected_player.lower()]
    save_record = {
        "Player": selected_player,
        **{s: projected_stats[s] for s in STATS},
        **{f"{s}_prob": projected_probs[s] for s in STATS},
        "Position": (player_rows[0].get("Position","") if player_rows else ""),
        "Total Points": total_points
    }
    st.session_state.projections.append(save_record)
    st.success(f"Saved projection for {selected_player}.")

if st.button("Clear Projection for Player"):
    st.session_state.projections = [p for p in st.session_state.projections if p.get("Player").lower() != selected_player.lower()]
    st.info(f"Cleared saved projection for {selected_player}.")

# -----------------------------
# TOP 150 LEADERBOARD
# -----------------------------
players_all = sorted(set(r["Player"] for r in rows))
df_auto = []

for p in players_all:
    p_rows = [r for r in rows if r["Player"].lower() == p.lower()]
    saved = next((x for x in st.session_state.projections if x.get("Player").lower() == p.lower()), None)

    record = {"Player": p, "Position": (p_rows[0].get("Position","") if p_rows else "")}

    stat_row_map = {}
    for stat in STATS:
        if saved:
            val = saved.get(stat, None)
            prob = saved.get(f"{stat}_prob", None)
            if val is None:
                if stat == "Total Touchdowns":
                    _, prob = get_total_touchdowns_line_and_prob_from_yes(p_rows)
                    val = 0.5
                else:
                    stat_row_map[stat] = find_market(stat, p_rows)
                    val = stat_row_map[stat]["Line"] if stat_row_map[stat] else 0.0
                    prob = stat_row_map[stat]["AvgProb"] if stat_row_map[stat] else 0.5
            record[stat] = val
            record[f"{stat}_prob"] = prob
        else:
            if stat == "Total Touchdowns":
                val = 0.5
                _, prob = get_total_touchdowns_line_and_prob_from_yes(p_rows)
            else:
                stat_row_map[stat] = find_market(stat, p_rows)
                val = stat_row_map[stat]["Line"] if stat_row_map[stat] else 0.0
                prob = stat_row_map[stat]["AvgProb"] if stat_row_map[stat] else 0.5
            record[stat] = val
            record[f"{stat}_prob"] = prob

    total_pts = sum(record[stat] * st.session_state[f"scoring__{stat}"] * record[f"{stat}_prob"] for stat in STATS)
    record["Total Points"] = total_pts
    df_auto.append(record)

df_auto = pd.DataFrame(df_auto)
cols = ["Player", "Total Points", "Position"] + [s for s in STATS] + [f"{s}_prob" for s in STATS]
df_auto = df_auto[cols].sort_values("Total Points", ascending=False).head(150)
st.subheader("Top 150 Projected Fantasy Leaders")
st.dataframe(df_auto)