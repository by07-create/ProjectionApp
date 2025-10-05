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
    # Note: Total Touchdowns will try numeric markets AND Y/N 'Any Touchdowns' yes/no markets
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
    return " ".join(parts[:-2]).title() if len(parts) >= 2 else str(pid)

def fetch_api(api_key):
    headers = {"x-api-key": api_key}
    params = {"leagueID": LEAGUE_ID, "oddsAvailable": "true", "limit": LIMIT}
    try:
        r = requests.get(BASE_URL, headers=headers, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        if not data.get("success", False):
            return None
        return data.get("data", [])
    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching odds: {e}")
        return None

def parse_american_to_int(odds):
    """Try to coerce various odds formats to integer american odds string like -260 or 150"""
    if odds is None:
        return None
    if isinstance(odds, int):
        return odds
    if isinstance(odds, float):
        return int(odds)
    s = str(odds).strip()
    # remove surrounding text
    # handle values like '-260', '+150', '150', 'EVEN', 'N/A', '—'
    if s.upper() in ["N/A", "", "—", "-", "EVEN", "PK"]:
        return None
    # sometimes wrapped in JSON like '{"odds": "-260"}' but we receive raw values
    # strip non-digit except leading minus
    cleaned = ""
    for i, ch in enumerate(s):
        if ch.isdigit() or (ch == "-" and i == 0) or (ch == "+" and i == 0):
            cleaned += ch
    if cleaned in ["", "+", "-"]:
        return None
    try:
        return int(cleaned)
    except:
        return None

def american_to_prob(odds):
    """Convert american odds to implied probability (0-1)."""
    val = parse_american_to_int(odds)
    if val is None:
        return None
    try:
        if val > 0:
            return 100.0 / (val + 100.0)
        else:
            return float(-val) / (-val + 100.0)
    except:
        return None

def average_odds(odds_list):
    """Return average implied probability from a list of American odds values (strings/ints)."""
    probs = []
    for o in odds_list:
        p = american_to_prob(o)
        if p is not None:
            probs.append(p)
    return sum(probs) / len(probs) if probs else 0.5

def normalize(s):
    return str(s).lower().replace(" ", "")

def market_text_matches(aliases, market_text, market_raw):
    """Return True if any alias matches market_text or market_raw (cleaned)."""
    m_clean = ''.join([c for c in normalize(market_text) if c.isalpha()])
    raw_clean = ''.join([c for c in normalize(market_raw) if c.isalpha()])
    for a in aliases:
        a_clean = ''.join([c for c in normalize(a) if c.isalpha()])
        if a_clean and (a_clean in m_clean or a_clean in raw_clean):
            return True
    return False

def find_market(stat, player_rows):
    """
    Find the first market row that matches the stat aliases.
    We intentionally return the first reliable match to avoid duplicates.
    player_rows is a list of dicts built from API/cache.
    """
    aliases = MARKET_MAP.get(stat, [stat])
    # first pass: precise textual match
    for r in player_rows:
        if market_text_matches(aliases, r.get("Market",""), r.get("MarketRaw","")):
            return r
    # fallback: substring match in Market field only
    for r in player_rows:
        m = normalize(r.get("Market",""))
        for a in aliases:
            if normalize(a) in m:
                return r
    return None

def find_total_td_yes_row(player_rows):
    """
    Find the 'Any/Player Touchdowns Yes' Y/N market row if present.
    Matches patterns like marketKey containing 'yn-yes' or MarketRaw containing 'yn-yes',
    or textual market containing 'anytime'+'yes' and 'touchdown'.
    """
    if not player_rows:
        return None
    for r in player_rows:
        raw = (r.get("MarketRaw") or "").lower()
        market = (r.get("Market") or "").lower()
        if ("yn-yes" in raw or "yn_yes" in raw or "yn-yes" in market or "yn_yes" in market) and ("touchdown" in raw or "touchdown" in market or "touchdowns" in raw or "touchdowns" in market):
            return r
        if "anytime" in raw and "yes" in raw and "touchdown" in raw:
            return r
        if "anytime" in market and "yes" in market and "touchdown" in market:
            return r
        if "any touchdowns" in market and "yes" in market:
            return r
    return None

def get_total_touchdowns_line_and_prob_from_yes(player_rows):
    """
    Return (line_val, prob_yes) using the YES market when available,
    else fallback to a numeric Player Touchdowns market, else defaults (0.5, 0.5).
    """
    yes_row = find_total_td_yes_row(player_rows)
    if yes_row:
        # Use AvgProb if present, otherwise compute from available book odds
        prob_yes = yes_row.get("AvgProb", None)
        if prob_yes is None or prob_yes == 0:
            od_list = []
            for k in ["DraftKings","FanDuel","Caesars","ESPNBet","BetMGM"]:
                val = yes_row.get(k)
                if val not in [None, "N/A", ""]:
                    od_list.append(val)
            prob_yes = average_odds(od_list) if od_list else 0.5
        line_val = yes_row.get("Line", 0.5)
        try:
            return (float(line_val) if line_val not in [None, ""] else 0.5, float(prob_yes))
        except:
            return (0.5, float(prob_yes))
    # fallback numeric market
    td_row = find_market("Total Touchdowns", player_rows)
    if td_row:
        return (td_row.get("Line", 0.5), td_row.get("AvgProb", 0.5))
    # last resort
    return (0.5, 0.5)

# -----------------------------
# STREAMLIT SETUP
# -----------------------------
st.set_page_config(layout="wide")
st.title("NFL Player Prop Odds & Fantasy Projection")
if "projections" not in st.session_state:
    st.session_state.projections = []  # list of saved dicts

# -----------------------------
# FETCH / CACHE DATA
# -----------------------------
use_cache = st.sidebar.checkbox("Load cached data instead of fetching API")
odds_data = []

if use_cache:
    odds_data = load_cache := (lambda : (lambda res: res)( (lambda: ( (lambda: (lambda: None)() )() ) ) ) )()  # placeholder to keep structure
# Replace the above weird placeholder with a real call; simpler: call load_cache normally
# (we keep code concise and explicit below)
def _load_cache_real(max_age_minutes=CACHE_MAX_AGE_MINUTES):
    try:
        _, res = dbx.files_download(CACHE_FILE)
        payload = json.loads(res.content.decode("utf-8"))
        ts = pd.to_datetime(payload.get("timestamp"))
        if (pd.Timestamp.now() - ts).total_seconds() < max_age_minutes * 60:
            return payload.get("data", [])
    except Exception:
        return []
    return []

if use_cache:
    odds_data = _load_cache_real()
else:
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Fetch Primary API"):
            odds_data = fetch_api(API_KEY_PRIMARY)
            if odds_data:
                try:
                    save_cache(odds_data)
                except Exception:
                    pass
            else:
                st.warning("Primary API failed or rate-limited.")
    with col2:
        if st.button("Fetch Secondary API"):
            odds_data = fetch_api(API_KEY_SECONDARY)
            if odds_data:
                try:
                    save_cache(odds_data)
                except Exception:
                    pass
            else:
                st.warning("Secondary API failed or rate-limited.")
    # if still empty after page load, attempt to load cache automatically
    if not odds_data:
        odds_data = _load_cache_real()

if not odds_data:
    st.warning("No odds data available (API or cache). Click fetch or check cache.")
    st.stop()

# -----------------------------
# EXTRACT PLAYER PROPS
# -----------------------------
rows = []
for event in odds_data:
    odds_list_raw = event.get("odds") or []
    odds_list = list(odds_list_raw.values()) if isinstance(odds_list_raw, dict) else odds_list_raw
    players_list = event.get("players") or []

    # Build player map
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

        # Market line/name
        line = (odds_item.get("bookOverUnder") or odds_item.get("fairOverUnder") or
                odds_item.get("openBookOverUnder") or odds_item.get("openFairOverUnder") or "")
        market_name = odds_item.get("marketName") or odds_item.get("market") or odds_item.get("statName") or "N/A"
        market_name = f"{market_name} {line}" if line else market_name

        odds_by_book = odds_item.get("byBookmaker") or {}
        all_odds = []
        def collect_od(v):
            if v is None:
                return
            if isinstance(v, dict):
                # prefer 'odds' key
                if "odds" in v and isinstance(v["odds"], (int,str)):
                    all_odds.append(v["odds"])
                else:
                    for val in v.values():
                        if isinstance(val, (int,str)):
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

        # MarketRaw: keep the marketKey / market / odds_item structure to inspect Y/N markets
        market_raw = ""
        try:
            market_raw = odds_item.get("marketKey") or odds_item.get("market") or json.dumps(odds_item)
        except Exception:
            market_raw = str(odds_item)

        rows.append({
            "Player": player_info["name"],
            "Position": player_info["position"],
            "Market": market_name,
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
    st.warning("No player prop rows extracted.")
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
proj_cols = st.columns(2)
projected_stats = {}
projected_probs = {}

for stat in STATS:
    if stat == "Total Touchdowns":
        # pull YES Y/N probability when available; projection default = 0.5
        line_val, yes_prob = get_total_touchdowns_line_and_prob_from_yes(player_rows)
        proj_val = 0.5  # default projection value
        proj_prob = yes_prob if yes_prob is not None else 0.5
    else:
        row = find_market(stat, player_rows)
        proj_val = row["Line"] if row else 0.0
        proj_prob = row["AvgProb"] if row else 0.5

    projected_stats[stat] = proj_cols[0].number_input(
        f"Projected {stat}",
        value=float(proj_val),
        step=0.1,
        key=f"proj_stat__{stat}"
    )
    projected_probs[stat] = proj_cols[1].number_input(
        f"Probability for {stat}",
        value=float(proj_prob),
        step=0.01,
        key=f"proj_prob__{stat}"
    )

# -----------------------------
# CALCULATE PROJECTED FANTASY POINTS (for selected player)
# -----------------------------
weighted_points = {}
for stat in STATS:
    points_per_unit = st.session_state.get(f"scoring__{stat}", DEFAULT_SCORING[stat])
    weighted_points[stat] = projected_stats[stat] * points_per_unit * projected_probs[stat]

total_points = sum(weighted_points.values())
st.subheader(f"Projected Fantasy Points: {total_points:.2f}")
st.json(weighted_points)

# -----------------------------
# SAVE / REMOVE PROJECTIONS (replace existing)
# -----------------------------
if st.button("Save Projection"):
    # replace existing saved record for this player
    st.session_state.projections = [p for p in st.session_state.projections if p.get("Player") != selected_player]
    st.session_state.projections.append({
        "Player": selected_player,
        **{s: projected_stats[s] for s in STATS},
        **{f"{s}_prob": projected_probs[s] for s in STATS},
        "Position": (player_rows[0].get("Position","") if player_rows else ""),
        "Total Points": total_points
    })

if st.button("Clear Projection for Player"):
    st.session_state.projections = [p for p in st.session_state.projections if p["Player"] != selected_player]

# -----------------------------
# TOP 150 LEADERBOARD (MIRROR PROJECTION FIELDS)
# -----------------------------
# For each player: prefer saved projection values; otherwise mirror inputs using market-derived defaults
players_all = sorted(set(r["Player"] for r in rows))
df_auto = []

for p in players_all:
    p_rows = [r for r in rows if r["Player"] == p]
    saved = next((x for x in st.session_state.projections if x.get("Player") == p), None)

    record = {"Player": p, "Position": (p_rows[0].get("Position","") if p_rows else "")}

    # For each stat: if saved, use saved projection & prob; else use same logic as the input fields above
    for stat in STATS:
        if saved:
            # saved may contain stat and stat_prob; fallback to market-derived when missing
            val = saved.get(stat, None)
            prob = saved.get(f"{stat}_prob", None)
            if val is None:
                if stat == "Total Touchdowns":
                    lv, ap = get_total_touchdowns_line_and_prob_from_yes(p_rows)
                    val = 0.5
                    prob = ap
                else:
                    row = find_market(stat, p_rows)
                    val = row["Line"] if row else 0.0
                    prob = row["AvgProb"] if row else 0.5
        else:
            if stat == "Total Touchdowns":
                lv, ap = get_total_touchdowns_line_and_prob_from_yes(p_rows)
                val = 0.5
                prob = ap
            else:
                row = find_market(stat, p_rows)
                val = row["Line"] if row else 0.0
                prob = row["AvgProb"] if row else 0.5

        # coerce to numeric
        try:
            record[stat] = float(val)
        except:
            record[stat] = 0.0
        try:
            record[f"{stat}_prob"] = float(prob)
        except:
            record[f"{stat}_prob"] = 0.5

    # compute projected points exactly the same way as the single-player calculation
    total_pts = 0.0
    for stat in STATS:
        pts_per = st.session_state.get(f"scoring__{stat}", DEFAULT_SCORING[stat])
        total_pts += record[stat] * pts_per * record[f"{stat}_prob"]
    record["Projected Points"] = total_pts

    df_auto.append(record)

# Build DataFrame and select requested column order
df_auto_top150 = pd.DataFrame(df_auto).sort_values("Projected Points", ascending=False).head(150).reset_index(drop=True)

# required order: Player, Projected Points, Pass Yds, Pass TDs, Rush Yds, Rush TDs, Receptions, Receiving Yds, Receiving TDs, Total TDs
cols_order = [
    "Player", "Projected Points",
    "Pass Yards", "Pass TDs", "Rush Yards", "Rush TDs",
    "Receptions", "Receiving Yards", "Receiving TDs", "Total Touchdowns"
]

# ensure the columns exist
for c in cols_order:
    if c not in df_auto_top150.columns:
        df_auto_top150[c] = 0.0

df_display_top = df_auto_top150[cols_order].copy()

st.subheader("Top 150 Projected Fantasy Players")
# Position filter
positions_present = sorted(set(df_auto_top150["Position"].fillna("").unique()))
positions_present = [p for p in positions_present if p]
pos_sel = st.multiselect("Filter positions (Top 150)", options=["All"] + positions_present, default=["All"])
if pos_sel and "All" not in pos_sel:
    df_display_top = df_auto_top150[df_auto_top150["Position"].isin(pos_sel)][cols_order].reset_index(drop=True)

st.dataframe(df_display_top)

# -----------------------------
# REFRESH DATA
# -----------------------------
if st.button("Refresh Data"):
    try:
        dbx.files_delete_v2(CACHE_FILE)
    except Exception:
        pass
    st.experimental_rerun()
