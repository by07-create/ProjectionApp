# -----------------------------
# TOP 150 LEADERBOARD (AUTO)
# -----------------------------
df_auto = []

for p in sorted(set(r["Player"] for r in rows)):
    player_rows = [r for r in rows if r["Player"] == p]
    player_data = {"Player": p}

    # Get projections for each stat using the same logic as above
    for stat in STATS:
        if stat == "Total Touchdowns":
            line_val, avg_prob = get_total_touchdowns_line_and_prob(player_rows)
            line_val = 0.5  # Always default projection to 0.5
        else:
            row = find_market(stat, player_rows)
            line_val = row["Line"] if row else 0.0
            avg_prob = row["AvgProb"] if row else 0.5

        player_data[stat] = line_val
        player_data[f"{stat}_prob"] = avg_prob

    # Use the same weighted calculation
    weighted_points = {
        stat: player_data[stat] * st.session_state[f"scoring__{stat}"] * player_data[f"{stat}_prob"]
        for stat in STATS
    }
    player_data["Projected Points"] = sum(weighted_points.values())

    df_auto.append(player_data)

# Reorder columns to match your requested layout
columns_order = [
    "Player", "Projected Points",
    "Pass Yards", "Pass TDs", "Rush Yards", "Rush TDs",
    "Receptions", "Receiving Yards", "Receiving TDs", "Total Touchdowns"
]

# Fill missing columns if needed
for c in columns_order:
    if c not in df_auto[0]:
        for player_data in df_auto:
            player_data[c] = 0.0

df_auto_top150 = (
    pd.DataFrame(df_auto)
    .sort_values("Projected Points", ascending=False)
    .head(150)
    .reset_index(drop=True)[columns_order]
)

st.subheader("Top 150 Projected Fantasy Players")
st.dataframe(df_auto_top150)
