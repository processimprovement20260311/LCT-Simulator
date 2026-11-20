# -*- coding: utf-8 -*-
"""
LCT Simulation — Streamlit Web App (Fully Wired)
Wraps the V1.5 engine in a Streamlit UI.
"""

import time
from dataclasses import dataclass
import hashlib
import json
import copy
from datetime import date, timedelta

import streamlit as st
import pandas as pd

# ============================================================
# ENGINE CORE
# ============================================================
Q_HALF = 12
MARQUEE_OPENER_DATE = "Mar 03"

@dataclass
class Ratings:
    OFF: float
    DEF: float
    SPT: float
    Overall: float

MASK64 = (1 << 64) - 1
def _rotl(x, k): return ((x << k) | (x >> (64 - k))) & MASK64

class Xoshiro:
    def __init__(self, seed):
        s, z = [], seed & MASK64
        for _ in range(4):
            z = (z + 0x9E3779B97F4A7C15) & MASK64
            x = z
            x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
            x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & MASK64
            x = x ^ (x >> 31); s.append(x & MASK64)
        self.s = s
    def _next64(self):
        s = self.s
        r = (_rotl((s[1]*5) & MASK64, 7) * 9) & MASK64
        t = (s[1] << 17) & MASK64
        s[2] ^= s[0]; s[3] ^= s[1]; s[1] ^= s[2]; s[0] ^= s[3]
        s[2] ^= t; s[3] = _rotl(s[3], 45)
        return r
    def next_float(self): return (self._next64() >> 11) / float(1 << 53)
    def next_int(self, lo, hi): return lo + int(self.next_float() * (hi - lo + 1))

def subseed(master, label):
    h = hashlib.sha256(f"{master}:{label}".encode()).digest()
    return int.from_bytes(h[:8], "big") & MASK64

class SubStreams:
    def __init__(self, seed):
        self.master = seed
        self.streams = {l: Xoshiro(subseed(seed, l)) for l in [
            "league_schedule","league_games","cup_draw","cup_games","soe_draw",
            "soe_games","playoff_games","tiebreaks","wildcard","overtime",
            "hac_games","hac_draw", "stats_gen"]} # Added stats_gen stream
    def get(self, l): return self.streams[l]

def substream_shuffle(seq, rng):
    for i in range(len(seq) - 1, 0, -1):
        j = rng.next_int(0, i)
        seq[i], seq[j] = seq[j], seq[i]
    return seq

COMP_STREAM_MAP = {
    "league":"league_games","cup_group":"cup_games","cup_knockout":"cup_games",
    "cup_final":"cup_games","soe_r32":"soe_games","soe_final":"soe_games",
    "playoff_wc":"playoff_games","playoff_div":"playoff_games",
    "playoff_bracket":"playoff_games","super_bowl":"playoff_games",
    "hac_group":"hac_games","hac_knockout":"hac_games","hac_final":"hac_games",
}

def clamp(x, lo, hi): return min(max(x, lo), hi)
def rhu(x): return int(x + 0.5) if x >= 0 else -int(-x + 0.5)

def weather_o_modifier(weather):
    return {"Clear": 0, "Extreme Rain": -15, "Heavy Snow": -25,
            "Heavy Winds": -15, "Extreme Cold": -20}[weather]

def apply_weather_to_ratings(weather, hr, ar):
    mod = weather_o_modifier(weather)
    h = Ratings(max(1, hr.OFF + mod), max(1, hr.DEF + mod), hr.SPT, max(1, hr.Overall + mod))
    a = Ratings(max(1, ar.OFF + mod), max(1, ar.DEF + mod), ar.SPT, max(1, ar.Overall + mod))
    return h, a

def is_marquee_game(comp_name, round_name, game_date, force_test_weather=False):
    if force_test_weather: return False
    if comp_name in {"cup_final", "soe_final", "super_bowl"}: return True
    if comp_name == "league" and game_date == MARQUEE_OPENER_DATE: return True
    return False

_CURRENT_WEATHER = "Random"
def forced_weather_setting():
    return None if _CURRENT_WEATHER == "Random" else _CURRENT_WEATHER

# NEW: Function to generate realistic box score stats based on game outcome
def generate_box_score(home, away, hs, as_, hr, ar, weather, rng):
    # Base offensive volume
    base_plays = 65
    h_plays = base_plays + rng.next_int(-5, 10)
    a_plays = base_plays + rng.next_int(-5, 10)
    
    # Weather affects passing efficiency and total volume slightly
    weather_penalty = 0.85 if weather in ["Extreme Rain", "Heavy Snow", "Heavy Winds"] else 1.0
    
    # Distribute yards based on points scored, overall rating, and defense faced
    # Roughly 10-15 yards per point scored
    h_yards = int((hs * 12) + (hr.OFF * 1.5) - (ar.DEF * 1.0) + rng.next_int(-30, 30))
    a_yards = int((as_ * 12) + (ar.OFF * 1.5) - (hr.DEF * 1.0) + rng.next_int(-30, 30))
    
    h_yards = max(120, h_yards)
    a_yards = max(120, a_yards)
    
    # Split into Pass vs Rush
    h_pass_ratio = clamp(0.60 + ((hr.OFF - ar.DEF) / 200) + (rng.next_int(-10, 10)/100), 0.40, 0.80)
    a_pass_ratio = clamp(0.60 + ((ar.OFF - hr.DEF) / 200) + (rng.next_int(-10, 10)/100), 0.40, 0.80)
    
    h_pass = int(h_yards * h_pass_ratio * weather_penalty)
    h_rush = h_yards - h_pass
    a_pass = int(a_yards * a_pass_ratio * weather_penalty)
    a_rush = a_yards - a_pass
    
    # First downs (roughly 1 FD per 15-18 yards)
    h_fd = int(h_yards / 16) + rng.next_int(0, 3)
    a_fd = int(a_yards / 16) + rng.next_int(0, 3)
    
    # Turnovers (Inversely proportional to Overall rating)
    h_to = rng.next_int(0, 3) if rng.next_float() < (0.4 + (ar.DEF - hr.OFF)/100) else 0
    a_to = rng.next_int(0, 3) if rng.next_float() < (0.4 + (hr.DEF - ar.OFF)/100) else 0
    
    # Sacks
    h_sacks = rng.next_int(0, 4) + (1 if ar.DEF > 90 else 0)
    a_sacks = rng.next_int(0, 4) + (1 if hr.DEF > 90 else 0)
    
    # Time of possession (in seconds)
    total_time = 3600
    h_top = int(total_time * (0.5 + (h_yards - a_yards)/800) + rng.next_int(-120, 120))
    h_top = clamp(h_top, 1800, 3000)
    a_top = total_time - h_top
    
    def fmt_top(sec):
        m, s = divmod(sec, 60)
        return f"{m}:{s:02d}"

    return {
        "Home": {
            "Total Yards": h_yards,
            "Passing Yards": h_pass,
            "Rushing Yards": h_rush,
            "First Downs": h_fd,
            "Turnovers": h_to,
            "Sacks": h_sacks,
            "Time of Possession": fmt_top(h_top),
            "Plays": h_plays
        },
        "Away": {
            "Total Yards": a_yards,
            "Passing Yards": a_pass,
            "Rushing Yards": a_rush,
            "First Downs": a_fd,
            "Turnovers": a_to,
            "Sacks": a_sacks,
            "Time of Possession": fmt_top(a_top),
            "Plays": a_plays
        }
    }

def sim_game(home, away, venue, must_win, subs, comp_name, round_name,
             game_date, ratings, season_num, forced_weather=None, force_test_weather=False, gen_stats=False):
    stream = COMP_STREAM_MAP.get(comp_name, "league_games")
    rng = subs.get(stream)

    if is_marquee_game(comp_name, round_name, game_date, force_test_weather):
        weather = "Clear"
    elif forced_weather:
        weather = forced_weather
    else:
        r = rng.next_float()
        weather = ("Clear" if r < 0.77 else "Extreme Rain" if r < 0.82 else
                   "Heavy Snow" if r < 0.87 else "Heavy Winds" if r < 0.92 else "Extreme Cold")

    hr_orig = ratings[home]; ar_orig = ratings[away]
    hr, ar = apply_weather_to_ratings(weather, hr_orig, ar_orig)

    hV=rng.next_int(25,75); hX=rng.next_int(30,70); hG=rng.next_int(20,80); hC=rng.next_int(30,70)
    aV=rng.next_int(25,75); aX=rng.next_int(30,70); aG=rng.next_int(20,80); aC=rng.next_int(30,70)
    hQ=rng.next_int(-Q_HALF,Q_HALF)+rng.next_int(-Q_HALF,Q_HALF)
    aQ=rng.next_int(-Q_HALF,Q_HALF)+rng.next_int(-Q_HALF,Q_HALF)

    hMb = clamp(50 + (hr.OFF - ar.DEF)/3, 30, 70)
    aMb = clamp(50 + (ar.OFF - hr.DEF)/3, 30, 70)
    hM = (hMb + hX)/2; aM = (aMb + aX)/2

    hP = 0.70*hr.Overall + 0.12*hV + 0.08*hM + 0.05*hG + 0.05*hC
    aP = 0.70*ar.Overall + 0.12*aV + 0.08*aM + 0.05*aG + 0.05*aC

    hBoost = 2 if venue == "home" else 0
    aBoost = 0

    hAdj = hP + hBoost; aAdj = aP + aBoost
    hs = rhu(max(24 + 0.4*(hAdj - 73) + hQ, 0))
    as_ = rhu(max(24 + 0.4*(aAdj - 73) + aQ, 0))

    winner = None; ot = False
    if hs > as_: winner = home
    elif as_ > hs: winner = away
    elif must_win:
        ot = True
        orng = subs.get("overtime")
        toss = orng.next_int(0, 1)
        possessor_home = (toss == 0)
        while True:
            adj = hAdj if possessor_home else aAdj
            eot = 24 + 0.4*((adj-5) - 73)
            pr = clamp(eot/48, 0, 1)
            if orng.next_float() < pr:
                t = orng.next_float()
                pts = 6 if t < 0.60 else (3 if t < 0.98 else 2)
                if possessor_home: hs += pts; winner = home
                else: as_ += pts; winner = away
                break
            possessor_home = not possessor_home

    result = {"home_team": home, "away_team": away, "home_score": hs, "away_score": as_,
            "winner": winner, "went_to_overtime": ot, "weather": weather,
            "competition": comp_name, "round": round_name, "date": game_date}
            
    # NEW: Generate stats if requested
    if gen_stats:
        stats_rng = subs.get("stats_gen")
        result["box_score"] = generate_box_score(home, away, hs, as_, hr, ar, weather, stats_rng)

    return result

RAW_RATINGS = {
    "STF": {"OFF": 91, "DEF": 99, "SPT": 91}, "BRK": {"OFF": 91, "DEF": 99, "SPT": 94}, "JRD": {"OFF": 97, "DEF": 92, "SPT": 95},
    "TAY": {"OFF": 97, "DEF": 92, "SPT": 95}, "BRE": {"OFF": 94, "DEF": 95, "SPT": 95}, "ASH": {"OFF": 96, "DEF": 94, "SPT": 91},
    "TRN": {"OFF": 93, "DEF": 94, "SPT": 94}, "RSN": {"OFF": 92, "DEF": 95, "SPT": 90}, "SZE": {"OFF": 89, "DEF": 91, "SPT": 90},
    "BRN": {"OFF": 87, "DEF": 92, "SPT": 90}, "AND": {"OFF": 86, "DEF": 91, "SPT": 81}, "ANG": {"OFF": 88, "DEF": 88, "SPT": 84},
    "HIL": {"OFF": 86, "DEF": 91, "SPT": 75}, "TOR": {"OFF": 89, "DEF": 80, "SPT": 90}, "CAS": {"OFF": 86, "DEF": 83, "SPT": 79},
    "ADR": {"OFF": 83, "DEF": 86, "SPT": 73}, "KRS": {"OFF": 78, "DEF": 89, "SPT": 80}, "OLV": {"OFF": 84, "DEF": 82, "SPT": 80},
    "GNN": {"OFF": 77, "DEF": 86, "SPT": 81}, "LXI": {"OFF": 81, "DEF": 76, "SPT": 79}, "SPH": {"OFF": 79, "DEF": 79, "SPT": 75},
    "NTL": {"OFF": 74, "DEF": 81, "SPT": 75}, "JLN": {"OFF": 82, "DEF": 69, "SPT": 82}, "RCH": {"OFF": 74, "DEF": 80, "SPT": 75},
    "HAL": {"OFF": 71, "DEF": 79, "SPT": 73}, "SRA": {"OFF": 79, "DEF": 66, "SPT": 77}, "AMB": {"OFF": 69, "DEF": 72, "SPT": 74},
    "CRT": {"OFF": 69, "DEF": 69, "SPT": 75}, "KEL": {"OFF": 72, "DEF": 66, "SPT": 76}, "KAT": {"OFF": 67, "DEF": 73, "SPT": 79},
    "RLY": {"OFF": 69, "DEF": 61, "SPT": 75}, "GRG": {"OFF": 69, "DEF": 67, "SPT": 69}
}

BASE_RATINGS = {team: Ratings(stats["OFF"], stats["DEF"], stats["SPT"], round((stats["OFF"]*3 + stats["DEF"]*3 + stats["SPT"]) / 7, 2)) for team, stats in RAW_RATINGS.items()}
TEAMS = list(BASE_RATINGS.keys())

def compute_league_table(schedule, game_results, tiebreak_rng=None):
    if tiebreak_rng is None: tiebreak_rng = Xoshiro(0)
    table = {t: {"pts":0,"gd":0,"gf":0,"w":0,"d":0,"l":0} for t in TEAMS}
    for r in game_results:
        if r.get("competition") != "league": continue
        h, a = r["home_team"], r["away_team"]
        table[h]["gf"] += r["home_score"]; table[h]["gd"] += r["home_score"] - r["away_score"]
        table[a]["gf"] += r["away_score"]; table[a]["gd"] += r["away_score"] - r["home_score"]
        if r["home_score"] > r["away_score"]: table[h]["pts"] += 3; table[h]["w"] += 1; table[a]["l"] += 1
        elif r["away_score"] > r["home_score"]: table[a]["pts"] += 3; table[a]["w"] += 1; table[h]["l"] += 1
        else: table[h]["pts"] += 1; table[h]["d"] += 1; table[a]["pts"] += 1; table[a]["d"] += 1
    by_pts = {}
    for t in TEAMS: by_pts.setdefault(table[t]["pts"], []).append(t)
    ranked = []
    for pts in sorted(by_pts.keys(), reverse=True):
        grp = by_pts[pts]
        if len(grp) == 1: ranked.extend(grp)
        else:
            grp.sort(key=lambda x: (table[x]["gd"], table[x]["gf"]), reverse=True)
            ranked.extend(grp)
    return ranked, table

def resolve_soe_tie(t1, t2, r1, r2, subs, leg2_date):
    a1 = r1["home_score"] + r2["away_score"]
    a2 = r1["away_score"] + r2["home_score"]
    if a1 > a2: return t1, False, None
    if a2 > a1: return t2, False, None
    t1_away = r2["away_score"]; t2_away = r1["away_score"]
    if t1_away > t2_away: return t1, False, None
    if t2_away > t1_away: return t2, False, None
    r2_ot = sim_game(t2, t1, "home", True, subs, "soe_r32", "L2-OT", leg2_date, BASE_RATINGS, 0, forced_weather=forced_weather_setting())
    return r2_ot["winner"], True, r2_ot

# ============================================================
# STREAMLIT UI
# ============================================================
st.set_page_config(page_title="LCT Simulator", page_icon="🏈", layout="wide")
st.title("🏈 LCT Simulation")
st.caption("V1.5 engine — Full App")

# Sidebar
with st.sidebar:
    st.header("Settings")
    _CURRENT_WEATHER = st.selectbox(
        "Weather",
        ["Random", "Clear", "Extreme Rain", "Heavy Snow", "Heavy Winds", "Extreme Cold"],
        index=0,
    )
    seed_input = st.text_input("Master seed (blank = auto)", value="")
    
    with st.expander("📊 Current ratings database"):
        df = pd.DataFrame([{"Team": k, "OFF": v.OFF, "DEF": v.DEF, "SPT": v.SPT, "Overall": v.Overall} for k, v in BASE_RATINGS.items()])
        st.dataframe(df, hide_index=True, use_container_width=True)

master_seed = None
if seed_input.strip():
    try: master_seed = int(seed_input.strip())
    except ValueError: st.sidebar.error("Seed must be an integer."); st.stop()

# Main UI
mode = st.selectbox("Competition mode", [
    "1 — Fixed Games", "2 — League Phase", "3 — Cup Phase", 
    "4 — SOE Phase", "5 — Single Elimination", 
    "6 — Full Season Simulation", "7 — Multi-Season Validator",
    "8 — HAC Mode", "9 — Standalone Playoffs"
], index=5)
mode_num = int(mode.split(" ")[0])

col1, col2 = st.columns(2)
mode_params = {}

with col1:
    if mode_num == 1:
        mode_params["home_team"] = st.selectbox("Home team", TEAMS, index=TEAMS.index("JRD"))
        mode_params["away_team"] = st.selectbox("Away team", TEAMS, index=TEAMS.index("GRG"))
        mode_params["venue"] = st.radio("Venue", ["home", "neutral"], horizontal=True)
        mode_params["num_sims"] = st.number_input("Simulations", 1, 100, 1)
        mode_params["allow_draws"] = st.checkbox("Allow draws (uncheck for OT resolution)", value=False)
        mode_params["force_clear"] = st.checkbox(
            "Force clear weather", value=False,
            help="Uncheck to sample weather from the engine's distribution.",
        )
    elif mode_num == 5:
        mode_params["bracket_teams"] = st.multiselect("Bracket teams (2/4/8/16/32)", TEAMS, default=["JRD", "TAY", "BRK", "ASH", "TRN", "HIL", "LXI", "KAT"])
    elif mode_num == 8:
        mode_params["hac_teams"] = st.multiselect(
            "HAC teams (6, 8, 10, 12, 14, or 16)",
            TEAMS,
            default=["JRD","TAY","BRK","ASH","STF","TRN","BRE","RSN"],
        )
        st.info("Groups: 6→2×3, 8→2×4, 10→2×5, 12→4×3, 14→2×7, 16→4×4. "
                "Advancers: top 2 per group, except 14 teams → top 4 per group. "
                "Knockout is two-legged; final is single leg.")
    elif mode_num == 9:
        mode_params["playoff_teams"] = st.multiselect(
            "Select 13 or 14 teams (Selection order = Seed 1, 2, 3... Wildcard last)",
            TEAMS,
            default=["JRD", "TAY", "BRK", "ASH", "TRN", "HIL", "LXI", "KAT", "STF", "BRE", "RSN", "SZE", "BRN", "AND"]
        )
        st.info("First 3 picks = Seeds 1-3. If 14 teams are selected, the 14th pick is the Wildcard (Seed 4). If 13 teams are selected, no Wildcard.")
    elif mode_num in [6, 7]:
        if mode_num == 7:
            mode_params["num_seasons"] = st.number_input("Seasons", 1, 50, 5)
        st.info("Mode 6/7 will simulate the entire League, Cup, SOE, and Playoffs. This may take 10-30 seconds depending on seasons.")

run_clicked = st.button("▶ Run Simulation", type="primary")

if run_clicked:
    run_seed = (master_seed if master_seed is not None else int(time.time() * 1000) & MASK64)
    st.info(f"Master seed: `{run_seed}`" + (" (reproducible)" if master_seed is not None else " (auto)"))

    # ---------------- MODE 1 (MATCHUP STATISTICS & BOX SCORES) ----------------
    if mode_num == 1:
        home = mode_params["home_team"]
        away = mode_params["away_team"]
        venue = mode_params["venue"]
        n = int(mode_params["num_sims"])
        must_win = not mode_params["allow_draws"]
        force_clear = mode_params["force_clear"]

        with st.spinner(f"Simulating {n} games..."):
            games = []
            for i in range(1, n + 1):
                subs = SubStreams((run_seed + i) & MASK64)
                games.append(sim_game(
                    home, away, venue, must_win, subs,
                    "league", "R1", "Mar 03", BASE_RATINGS, 1,
                    forced_weather=forced_weather_setting(),
                    force_test_weather=force_clear,
                    gen_stats=True  # <-- Tell engine to generate box score
                ))
            df = pd.DataFrame(games)

        st.success(f"Ran {n} simulations: **{home}** vs **{away}** ({venue}).")

        # ---------- Aggregate Metrics ----------
        if n > 1:
            hw = (df["winner"] == home).sum()
            aw = (df["winner"] == away).sum()
            draws = df["winner"].isna().sum()
            ot_rate = df["went_to_overtime"].mean()

            m1, m2, m3, m4 = st.columns(4)
            m1.metric(f"{home} win %", f"{hw/n:.1%}", help=f"{hw} wins")
            m2.metric(f"{away} win %", f"{aw/n:.1%}", help=f"{aw} wins")
            m3.metric("Draw %", f"{draws/n:.1%}", help=f"{draws} draws")
            m4.metric("OT rate", f"{ot_rate:.1%}")

        st.divider()

        # ---------- Individual Game Box Scores ----------
        st.subheader("Box Scores")
        
        for i, game in enumerate(games):
            # Header
            winner_text = game['winner'] if game['winner'] else "Tie"
            st.markdown(f"### Game {i+1} &nbsp;&nbsp;|&nbsp;&nbsp; **{game['away_team']}** ({game['away_score']}) @ **{game['home_team']}** ({game['home_score']}) &nbsp;&nbsp;|&nbsp;&nbsp; Weather: {game['weather']}")
            if game['went_to_overtime']:
                st.caption("*(Went to Overtime)*")
            
            # Box Score Table
            bs = game["box_score"]
            box_rows = []
            for stat in ["Total Yards", "Passing Yards", "Rushing Yards", "First Downs", "Turnovers", "Sacks", "Time of Possession", "Plays"]:
                box_rows.append({
                    "Stat": stat,
                    game["away_team"]: bs["Away"][stat],
                    game["home_team"]: bs["Home"][stat]
                })
            st.dataframe(pd.DataFrame(box_rows), hide_index=True, use_container_width=True)
            
            # If we're showing multiple games, don't show all full box scores, just show the first 5 to prevent extreme scrolling
            if i >= 4 and n > 5:
                st.info(f"... and {n - 5} more games. See the Game Log below for summary stats.")
                break

        # ---------- Game Log Summary ----------
        with st.expander("Show full game log"):
            log_df = pd.DataFrame(games)
            log_df["Total Yards (Home)"] = log_df["box_score"].apply(lambda x: x["Home"]["Total Yards"])
            log_df["Total Yards (Away)"] = log_df["box_score"].apply(lambda x: x["Away"]["Total Yards"])
            
            display_df = log_df[["home_team", "home_score", "away_score", "away_team", "winner", "weather", "Total Yards (Home)", "Total Yards (Away)"]].copy()
            display_df.insert(0, "Game", range(1, len(display_df) + 1))
            display_df["winner"] = display_df["winner"].fillna("Draw")
            display_df = display_df.rename(columns={
                "home_team": "Home", "home_score": "Home Score",
                "away_team": "Away", "away_score": "Away Score",
                "winner": "Winner", "weather": "Weather"
            })
            st.dataframe(display_df, hide_index=True, use_container_width=True)
