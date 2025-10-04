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
LIMIT = 20
CACHE_FILE = "/odds_cache.json"
CACHE_MAX_AGE_MINUTES = 30

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
    "Total Touchdowns": ["Player Touchdowns", "Any Touchdowns", "Any TDs"]
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
    parts = pid.split("_")
    return " ".join(parts[:-2]).title() if len(parts) >= 2 else pid

def fetch_api(api_key):
    headers = {"x-api-key": api_key}
    params = {"leagueID": LEAGUE_ID, "oddsAvailable": "true", "limit": LIMIT}
    try:
        r = requests.get(BASE_URL, headers=headers, params=params)
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
    probs = [american_to_prob(o) for o in odds_list if o not in ["N/A", None, ""]]
    probs = [p for p in probs if p is not None]
    return sum(probs)/len(probs) if probs else 0.5

def normalize(s):
    return str(s).lower().replace(" ", "")

def find_market(stat, player_rows):
    aliases = MARKET_MAP.get(stat, [stat])
    for r in player_rows:
        market_norm = normalize(r["Market"])
        for alias in aliases:
            # remove numbers to match markets like "Player Touchdowns 0.5"
            m_clean = ''.join([c for c in market_norm if not c.isdigit()])
            a_clean = ''.join([c for c in normalize(alias) if not c.isdigit()])
            if a_clean in m_clean:
                return r
    return None

def get_total_touchdowns(player_rows, position):
    """Calculate Total Touchdowns: QB = Rush + Rec, others = all TDs"""
    if not player_rows:
        return 0.0, 0.5
    if position == "QB":
        rush = find_market("Rush TDs", player_rows)
        rec = find_market("Receiving TDs", player_rows)
        line = sum([r["Line"] for r in [rush, rec] if r])
        prob_vals = [r["AvgProb"] for r in [rush, rec] if r]
        avg_prob = sum(prob_vals)/len(prob_vals) if prob_vals else 0.5
    else:
        td = find_market("Total Touchdowns", player_rows)
        if td:
            line = td["Line"]
            avg_prob = td["AvgProb"]
        else:
            line = 0.0
            avg_prob = 0.5
    return line, avg_prob

# -----------------------------
# STREAMLIT SETUP
# -----------------------------
st.title("NFL Player Prop Odds & Fantasy Projection")
if "projections" not in st.session_state:
    st.session_state.projections = []

# -----------------------------
# FETCH / CACHE DATA
# -----------------------------
use_cache = st.sidebar.checkbox("Load cached data instead of fetching API")
odds_data = []

if use_cache:
    odds_data = load_cache()
    if not odds_data:
        st.warning("No cached data found, please fetch from API.")
        st.stop()
else:
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Fetch Primary API"):
            odds_data = fetch_api(API_KEY_PRIMARY)
            if odds_data:
                save_cache(odds_data)
            else:
                st.warning("Primary API failed or rate-limited.")
                st.stop()
    with col2:
        if st.button("Fetch Secondary API"):
            odds_data = fetch_api(API_KEY_SECONDARY)
            if odds_data:
                save_cache(odds_data)
            else:
                st.warning("Secondary API failed or rate-limited.")
                st.stop()
    if not odds_data:
        st.info("Click a fetch button to load player prop data.")
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

        line = (odds_item.get("bookOverUnder") or odds_item.get("fairOverUnder") or
                odds_item.get("openBookOverUnder") or odds_item.get("openFairOverUnder") or "")
        market_name = odds_item.get("marketName") or "N/A"
        market_name = f"{market_name} {line}" if line else market_name

        odds_by_book = odds_item.get("byBookmaker") or {}
        all_odds = [
            odds_item.get('bookOdds','N/A'),
            odds_item.get('fairOdds','N/A'),
            odds_item.get('openBookOdds','N/A'),
            odds_item.get('openFairOdds','N/A'),
            odds_by_book.get("draftkings", {}).get("odds", "N/A"),
            odds_by_book.get("fanduel", {}).get("odds", "N/A"),
            odds_by_book.get("caesars", {}).get("odds", "N/A"),
            odds_by_book.get("espnbet", {}).get("odds", "N/A"),
            odds_by_book.get("betmgm", {}).get("odds", "N/A"),
        ]

        rows.append({
            "Player": player_info["name"],
            "Position": player_info["position"],
            "Market": market_name,
            "Line": float(line) if line not in ["", None] else 0.0,
            "AvgProb": average_odds(all_odds),
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
# DISPLAY TABLES
# -----------------------------
df_all = pd.DataFrame(rows).sort_values(["Player", "Market"])
df_display = df_all.drop(columns=["Position"], errors="ignore")
st.subheader("All Player Props")
st.dataframe(df_display)

player_rows = [r for r in rows if r["Player"] == selected_player]
df_odds = pd.DataFrame(player_rows).sort_values("Market")
df_odds_display = df_odds.drop(columns=["Position"], errors="ignore")
st.subheader(f"Prop Odds for {selected_player}")
st.dataframe(df_odds_display)

# -----------------------------
# FANTASY PROJECTION INPUTS
# -----------------------------
proj_cols = st.columns(2)
projected_stats = {}
projected_probs = {}
pos = player_rows[0].get("Position", "") if player_rows else ""

for stat in STATS:
    if stat == "Total Touchdowns":
        line_val, avg_prob = get_total_touchdowns(player_rows, pos)
    else:
        row = find_market(stat, player_rows)
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
    points_per_unit = st.session_state[f"scoring__{stat}"]
    weighted_points[stat] = projected_stats[stat] * points_per_unit * projected_probs[stat]

total_points = sum(weighted_points.values())
st.subheader(f"Projected Fantasy Points: {total_points:.2f}")
st.json(weighted_points)

# -----------------------------
# SAVE / REMOVE PROJECTIONS
# -----------------------------
if st.button("Save Projection"):
    st.session_state.projections.append({
        "Player": selected_player,
        **projected_stats,
        "Total Points": total_points
    })

if st.button("Clear Projection for Player"):
    st.session_state.projections = [p for p in st.session_state.projections if p["Player"] != selected_player]

if st.session_state.projections:
    st.subheader("Saved Player Projections")
    st.dataframe(pd.DataFrame(st.session_state.projections))

# -----------------------------
# TOP 100 LEADERBOARD
# -----------------------------
if st.session_state.projections:
    df_proj = pd.DataFrame(st.session_state.projections)
    df_top100 = df_proj.sort_values("Total Points", ascending=False).head(100)
    st.subheader("Top 100 Projected Fantasy Players")
    st.dataframe(df_top100)

# -----------------------------
# REFRESH DATA
# -----------------------------
if st.button("Refresh Data"):
    try:
        dbx.files_delete_v2(CACHE_FILE)
    except:
        pass
    st.experimental_rerun()
