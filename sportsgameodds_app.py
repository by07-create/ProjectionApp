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
    for t in tokens:
        if mr.endswith(t):
            return True
    return False

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
for game in odds_data:
    for prop in game.get("PlayerProps", []):
        player_name = clean_player_name(prop.get("PlayerID") or prop.get("Player"))
        row = {
            "Player": player_name,
            "Position": prop.get("Position", ""),
            "Market": prop.get("Market", ""),
            "MarketRaw": prop.get("MarketRaw", ""),
            "Line": prop.get("Line"),
            "AvgProb": prop.get("AvgProb"),
            "DraftKings": prop.get("DraftKings"),
            "FanDuel": prop.get("FanDuel"),
            "Caesars": prop.get("Caesars"),
            "ESPNBet": prop.get("ESPNBet"),
            "BetMGM": prop.get("BetMGM"),
            "StatID": prop.get("StatID"),
            "SideID": prop.get("SideID")
        }
        rows.append(row)

# -----------------------------
# ROTOWIRE FETCH (AUTO SLATE)
# -----------------------------
try:
    slate_resp = requests.get("https://www.rotowire.com/daily/tables/slates-nfl.php", timeout=15)
    slate_resp.raise_for_status()
    slate_json = slate_resp.json()
    latest_slate = slate_json[0]["id"] if slate_json else 8739
    st.info(f"Using latest Rotowire slate ID: {latest_slate}")
except Exception:
    st.warning("Couldn't detect latest slate ID — using fallback 8739")
    latest_slate = 8739

rotowire_url = f"https://www.rotowire.com/daily/nfl/api/players.php?slateID={latest_slate}"
try:
    rw_resp = requests.get(rotowire_url, timeout=15)
    rw_resp.raise_for_status()
    rotowire_data = rw_resp.json()
except Exception as e:
    st.warning(f"Failed to load Rotowire data: {e}")
    rotowire_data = []

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
# DISPLAY SELECTED PLAYER PROPS
# -----------------------------
player_rows = [r for r in rows if r["Player"] == selected_player]
df_odds = pd.DataFrame(player_rows).sort_values("Market")
df_odds_display = df_odds.drop(columns=["Position","MarketRaw"], errors="ignore")
st.subheader(f"Prop Odds for {selected_player}")
st.dataframe(df_odds_display)

# -----------------------------
# FANTASY PROJECTION INPUTS
# -----------------------------
st.subheader("Player Projections")
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

    col_label, col_proj, col_prob = st.columns([2, 2, 2])
    with col_label:
        st.markdown(f"**{stat}**")
    with col_proj:
        projected_stats[stat] = st.number_input(
            f"proj_{stat}",
            value=float(line_val),
            step=0.1,
            format="%.2f",
            key=f"proj_stat__{stat}"
        )
    with col_prob:
        pct_default = float(avg_prob) * 100.0
        pct_input = st.number_input(
            f"prob_{stat}",
            value=round(pct_default, 2),
            step=0.1,
            format="%.2f",
            key=f"proj_prob__{stat}_pct"
        )
        projected_probs[stat] = float(pct_input) / 100.0

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
    st.session_state.projections = [p for p in st.session_state.projections if p.get("Player") != selected_player]
    proj_entry = {
        "Player": selected_player,
        "Position": player_rows[0]["Position"] if player_rows else "",
        "ProjectedPoints": total_points,
        "RotowirePoints": rotowire_map.get(selected_player, {}).get("proj_pts"),
        "Salary": rotowire_map.get(selected_player, {}).get("salary"),
        "Value": (total_points / rotowire_map.get(selected_player, {}).get("salary",1) * 100) if total_points else 0
    }
    st.session_state.projections.append(proj_entry)
    st.success("Saved.")

if st.button("Clear Projections"):
    st.session_state.projections = []

# -----------------------------
# CALCULATE TOP 150
# -----------------------------
players_all = list({r["Player"] for r in rows})
df_auto = []

for p in players_all:
    p_rows = [r for r in rows if r["Player"] == p]
    weighted_points = {}
    row_data = {"Player": p, "Position": p_rows[0]["Position"] if p_rows else ""}

    # Build player_stat_row_map for this player
    player_stat_row_map = {}
    for stat in STATS:
        if stat == "Total Touchdowns":
            player_stat_row_map[stat] = get_total_touchdowns_line_and_prob_from_yes(p_rows)
        else:
            player_stat_row_map[stat] = find_market(stat, p_rows)

    for stat in STATS:
        row = player_stat_row_map[stat]
        if stat == "Total Touchdowns":
            line_val = 0.5
            avg_prob = row[1] if row else 0.5
        else:
            line_val = row["Line"] if row else 0.0
            avg_prob = row["AvgProb"] if row else 0.5
        pts_per_unit = DEFAULT_SCORING[stat]
        weighted_points[stat] = line_val * pts_per_unit * avg_prob
        row_data[stat] = line_val
        row_data[f"{stat}_prob"] = avg_prob

    total_points = sum(weighted_points.values())
    row_data["Total Points"] = total_points
    row_data["Salary"] = rotowire_map.get(p, {}).get("salary")
    row_data["RotowirePoints"] = rotowire_map.get(p, {}).get("proj_pts")
    row_data["Value"] = (total_points / row_data["Salary"] * 100) if total_points and row_data["Salary"] else 0

    df_auto.append(row_data)

df_auto = pd.DataFrame(df_auto)
cols = ["Player", "Position", "Total Points", "Salary", "RotowirePoints", "Value"] + \
       [s for s in STATS] + [f"{s}_prob" for s in STATS]
df_auto = df_auto[cols].sort_values("Total Points", ascending=False).head(150)
df_auto.insert(0, "Rank", range(1, len(df_auto)+1))
st.subheader("Top 150 Player Projections")
st.dataframe(df_auto)