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
# SIDEBAR CONTROLS (ALWAYS VISIBLE)
# -----------------------------
st.sidebar.title("Controls")
slate_id_input = st.sidebar.text_input("Rotowire Slate ID", value="4105")
use_cache = st.sidebar.checkbox("Load cached data instead of fetching API", value=True)

# Always show fetch buttons in sidebar
col1, col2 = st.sidebar.columns(2)
odds_data = []

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

# -----------------------------
# LOAD CACHE IF SELECTED
# -----------------------------
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

if use_cache and not odds_data:
    odds_data = load_cache_from_dropbox()
    if not odds_data:
        st.warning("No cached data found in Dropbox (or cache expired). Please fetch from API.")

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
# The rest of your original app code remains exactly as-is
# including: player selection, projections, calculation, top 150 leaderboard
# -----------------------------
