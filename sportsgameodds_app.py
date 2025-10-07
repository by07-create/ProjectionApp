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
# HELPERS (UNCHANGED)
# ... [all existing helper functions remain unchanged] ...
# -----------------------------

# -----------------------------
# ROTOWIRE SALARY FETCH FUNCTION
# -----------------------------
def fetch_fanduel_salaries(slate_id: int):
    url = f"https://www.rotowire.com/daily/nfl/api/players.php?slateID={slate_id}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        players = requests.get(url, headers=headers, timeout=10).json()
        df = pd.DataFrame(players)
        df['name'] = df['firstName'] + ' ' + df['lastName']
        df['Salary'] = df['salary']
        df['ProjPoints'] = df['pts'].astype(float)
        salary_map = df.set_index('name')[['Salary','ProjPoints']].to_dict(orient='index')
        return salary_map
    except Exception as e:
        st.error(f"Error fetching Rotowire salary data: {e}")
        return {}

# -----------------------------
# STREAMLIT SETUP AND DATA FETCH (UNCHANGED)
# -----------------------------
# ... [unchanged API fetch and caching code] ...

# Fetch Rotowire salaries
slate_id_input = st.sidebar.text_input("Rotowire Slate ID", value="4105")
salary_data = fetch_fanduel_salaries(int(slate_id_input))

# -----------------------------
# TOP 150 LEADERBOARD (UPDATED WITH SALARY & PROJ POINTS)
# -----------------------------
players_all = sorted(set(r["Player"] for r in rows))
df_auto = []

for p in players_all:
    p_rows = [r for r in rows if r["Player"] == p]
    saved = next((x for x in st.session_state.projections if x.get("Player") == p), None)

    record = {"Player": p, "Position": (p_rows[0].get("Position","") if p_rows else "")}

    # Add salary and projected points if available
    if p in salary_data:
        record['Salary'] = salary_data[p]['Salary']
        record['ProjPoints'] = salary_data[p]['ProjPoints']
    else:
        record['Salary'] = None
        record['ProjPoints'] = None

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
cols = ["Player", "Total Points", "Position", "Salary", "ProjPoints"] + [s for s in STATS] + [f"{s}_prob" for s in STATS]
df_auto = df_auto[cols].sort_values("Total Points", ascending=False).head(150)
df_auto.insert(0, "Rank", range(1, len(df_auto) + 1))
st.subheader("Top 150 Projected Fantasy Leaders")
st.dataframe(df_auto)
