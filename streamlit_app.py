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
            "soe_games","playoff_games","tiebreaks","wildcard","overtime"]}
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

def sim_game(home, away, venue, must_win, subs, comp_name, round_name,
             game_date, ratings, season_num, forced_weather=None, force_test_weather=False):
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

    return {"home_team": home, "away_team": away, "home_score": hs, "away_score": as_,
            "winner": winner, "went_to_overtime": ot, "weather": weather,
            "competition": comp_name, "round": round_name, "date": game_date}

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
    "6 — Full Season Simulation", "7 — Multi-Season Validator"
], index=5)
mode_num = int(mode.split(" ")[0])

col1, col2 = st.columns(2)
mode_params = {}

with col1:
    if mode_num == 1:
        mode_params["home_team"] = st.selectbox("Home team", TEAMS, index=TEAMS.index("JRD"))
        mode_params["away_team"] = st.selectbox("Away team", TEAMS, index=TEAMS.index("GRG"))
        mode_params["venue"] = st.radio("Venue", ["home", "neutral"], horizontal=True)
        mode_params["num_sims"] = st.number_input("Simulations", 1, 1000, 10)
    elif mode_num == 5:
        mode_params["bracket_teams"] = st.multiselect("Bracket teams (2/4/8/16/32)", TEAMS, default=["JRD", "TAY", "BRK", "ASH", "TRN", "HIL", "LXI", "KAT"])
    elif mode_num in [6, 7]:
        if mode_num == 7:
            mode_params["num_seasons"] = st.number_input("Seasons", 1, 50, 5)
        st.info("Mode 6/7 will simulate the entire League, Cup, SOE, and Playoffs. This may take 10-30 seconds depending on seasons.")

run_clicked = st.button("▶ Run Simulation", type="primary")

if run_clicked:
    run_seed = (master_seed if master_seed is not None else int(time.time() * 1000) & MASK64)
    st.info(f"Master seed: `{run_seed}`" + (" (reproducible)" if master_seed is not None else " (auto)"))

    # ---------------- MODE 1 ----------------
    if mode_num == 1:
        with st.spinner(f"Simulating {mode_params['num_sims']} games..."):
            rows = []
            for i in range(1, mode_params["num_sims"] + 1):
                game_seed = (run_seed + i) & MASK64
                subs = SubStreams(game_seed)
                res = sim_game(mode_params["home_team"], mode_params["away_team"], mode_params["venue"], False, subs, "league", "R1", "Mar 03", BASE_RATINGS, 1, forced_weather=forced_weather_setting(), force_test_weather=True)
                rows.append({"Game": i, "Home": res["home_team"], "Away": res["away_team"], "Score": f"{res['home_score']}–{res['away_score']}", "Winner": res["winner"] or "Draw", "Weather": res["weather"]})
        st.success(f"Ran {mode_params['num_sims']} games.")
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    # ---------------- MODE 2 (STANDALONE LEAGUE) ----------------
    elif mode_num == 2:
        with st.spinner("Simulating League Phase..."):
            subs = SubStreams(run_seed)
            schedule = [{"round": r, "home": TEAMS[i], "away": TEAMS[(i + r) % len(TEAMS)]} for r in range(1, 32) for i in range(len(TEAMS))]
            schedule += [{"round": g["round"] + 31, "home": g["away"], "away": g["home"]} for g in schedule[:]]
            
            results = []
            for g in schedule:
                results.append(sim_game(g["home"], g["away"], "home", False, subs, "league", f"R{g['round']}", "TBD", BASE_RATINGS, 1, forced_weather=forced_weather_setting()))
            
            ranked, table = compute_league_table(schedule, results, subs.get("tiebreaks"))
            st.success(f"League Champion: **{ranked[0]}**")
            
            df_rows = [{"Pos": i+1, "Team": t, "W": table[t]["w"], "D": table[t]["d"], "L": table[t]["l"], "Pts": table[t]["pts"], "GD": table[t]["gd"]} for i, t in enumerate(ranked)]
            st.dataframe(pd.DataFrame(df_rows), hide_index=True, use_container_width=True)

    # ---------------- MODE 3 (STANDALONE CUP) ----------------
    elif mode_num == 3:
        with st.spinner("Simulating Cup..."):
            subs = SubStreams(run_seed)
            t_list = TEAMS[:]; substream_shuffle(t_list, subs.get("cup_draw"))
            groups = [t_list[i*4:(i+1)*4] for i in range(8)]
            winners, runners = [], []
            for grp in groups:
                g_pts = {x: 0 for x in grp}; g_gd = {x: 0 for x in grp}; g_gf = {x: 0 for x in grp}
                for i in range(4):
                    for j in range(4):
                        if i != j:
                            r = sim_game(grp[i], grp[j], "home", False, subs, "cup_group", "G", "TBD", BASE_RATINGS, 1, forced_weather=forced_weather_setting())
                            hs, as_ = r["home_score"], r["away_score"]
                            g_gf[grp[i]] += hs; g_gf[grp[j]] += as_
                            g_gd[grp[i]] += hs - as_; g_gd[grp[j]] += as_ - hs
                            if hs > as_: g_pts[grp[i]] += 3
                            elif as_ > hs: g_pts[grp[j]] += 3
                            else: g_pts[grp[i]] += 1; g_pts[grp[j]] += 1
                ordered = sorted(grp, key=lambda x: (-g_pts[x], -g_gd[x], -g_gf[x]))
                winners.append(ordered[0]); runners.append(ordered[1])
            
            seeds = winners + runners
            
            def play_bo7(hi, lo):
                wins = {hi:0, lo:0}
                for g in range(7):
                    res = sim_game(hi, lo, "home", True, subs, "cup_knockout", "Bo7", "TBD", BASE_RATINGS, 1, forced_weather=forced_weather_setting())
                    wins[res["winner"]] += 1
                return hi if wins[hi] > wins[lo] else lo, wins[hi], wins[lo]

            # Round of 16
            r16_w = []
            for i in range(8):
                hi, lo = seeds[i], seeds[15-i]
                w, hw, lw = play_bo7(hi, lo)
                r16_w.append((w, f"{hi} vs {lo}", f"{hw}-{lw}"))
            
            # Quarterfinals (Fixed tracking!)
            qf_w = []
            for a, b in [(0,7), (3,4), (1,6), (2,5)]:
                hi, lo = r16_w[a][0], r16_w[b][0]
                w, hw, lw = play_bo7(hi, lo)
                qf_w.append((w, f"{hi} vs {lo}", f"{hw}-{lw}"))
            
            # Semifinals (Fixed tracking!)
            sf_w = []
            for i in range(2):
                hi, lo = qf_w[i*2][0], qf_w[i*2+1][0]
                w, hw, lw = play_bo7(hi, lo)
                sf_w.append((w, f"{hi} vs {lo}", f"{hw}-{lw}"))
            
            # Final
            cup_final = sim_game(sf_w[0][0], sf_w[1][0], "neutral", True, subs, "cup_final", "Final", "Oct 26", BASE_RATINGS, 1, forced_weather=forced_weather_setting())
            
            st.success(f"Cup Champion: **{cup_final['winner']}**")
            st.write(f"Final: {cup_final['away_team']} ({cup_final['away_score']}) at {cup_final['home_team']} ({cup_final['home_score']})")
            
            rounds = {"Round of 16": r16_w, "Quarterfinals": qf_w, "Semifinals": sf_w}
            for rname, rdata in rounds.items():
                st.write(f"**{rname}**")
                rows = [{"Matchup": m, "Winner": w, "Series": s} for (w, m, s) in rdata]
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    # ---------------- MODE 4 (STANDALONE SOE) ----------------
    elif mode_num == 4:
        with st.spinner("Simulating SOE..."):
            subs = SubStreams(run_seed)
            t = TEAMS[:]; substream_shuffle(t, subs.get("soe_draw"))
            
            def sim_2leg(t1, t2):
                r1 = sim_game(t1, t2, "home", False, subs, "soe_r32", "L1", "TBD", BASE_RATINGS, 1, forced_weather=forced_weather_setting())
                r2 = sim_game(t2, t1, "home", False, subs, "soe_r32", "L2", "TBD", BASE_RATINGS, 1, forced_weather=forced_weather_setting())
                winner, _, _ = resolve_soe_tie(t1, t2, r1, r2, subs, "TBD")
                
                # Format the two-legged score for display
                a1 = r1["home_score"] + r2["away_score"]
                a2 = r1["away_score"] + r2["home_score"]
                agg_str = f"{a1}-{a2}"
                if a1 == a2: agg_str += " (Away Pts)"
                
                return winner, f"{t1} vs {t2}", agg_str

            r32_w = [sim_2leg(t[i*2], t[i*2+1]) for i in range(16)]
            r16_w = [sim_2leg(r32_w[i*2][0], r32_w[i*2+1][0]) for i in range(8)]
            qf_w = [sim_2leg(r16_w[i*2][0], r16_w[i*2+1][0]) for i in range(4)]
            sf_w = [sim_2leg(qf_w[i*2][0], qf_w[i*2+1][0]) for i in range(2)]
            
            soe_final = sim_game(sf_w[0][0], sf_w[1][0], "neutral", True, subs, "soe_final", "Final", "Nov 24", BASE_RATINGS, 1, forced_weather=forced_weather_setting())
            
            st.success(f"SOE Champion: **{soe_final['winner']}**")
            st.write(f"Final: {soe_final['away_team']} ({soe_final['away_score']}) at {soe_final['home_team']} ({soe_final['home_score']})")
            
            rounds = {"Round of 32": r32_w, "Round of 16": r16_w, "Quarterfinals": qf_w, "Semifinals": sf_w}
            for rname, rdata in rounds.items():
                st.write(f"**{rname}**")
                rows = [{"Matchup": m, "Winner": w, "Aggregate": s} for (w, m, s) in rdata]
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    # ---------------- MODE 6 & 7 (SEASON RUNNER) ----------------
    elif mode_num in [6, 7]:
        seasons = 1 if mode_num == 6 else mode_params["num_seasons"]
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        all_season_records = []
        stats = {t: {"LeagueWins": 0, "Top5Finishes": 0, "PlayoffApps": 0, "CupTitles": 0, "SOETitles": 0, "SBTitles": 0} for t in TEAMS}
        
        for season in range(1, seasons + 1):
            status_text.write(f"Simulating Season {season} of {seasons}...")
            season_seed = (run_seed + season) & MASK64
            subs = SubStreams(season_seed)
            
            # 1. League
            schedule = [{"round": r, "home": TEAMS[i], "away": TEAMS[(i + r) % len(TEAMS)]} for r in range(1, 32) for i in range(len(TEAMS))]
            schedule += [{"round": g["round"] + 31, "home": g["away"], "away": g["home"]} for g in schedule[:]]
            
            results = []
            for g in schedule:
                results.append(sim_game(g["home"], g["away"], "home", False, subs, "league", f"R{g['round']}", "TBD", BASE_RATINGS, season, forced_weather=forced_weather_setting()))
            
            ranked, table = compute_league_table(schedule, results, subs.get("tiebreaks"))
            league_champ = ranked[0]
            stats[league_champ]["LeagueWins"] += 1
            for t in ranked[:5]: stats[t]["Top5Finishes"] += 1

            # 2. Cup
            t_list = TEAMS[:]
            substream_shuffle(t_list, subs.get("cup_draw"))
            groups = [t_list[i*4:(i+1)*4] for i in range(8)]
            winners, runners = [], []
            for grp in groups:
                g_pts = {x: 0 for x in grp}; g_gd = {x: 0 for x in grp}; g_gf = {x: 0 for x in grp}
                for i in range(4):
                    for j in range(4):
                        if i != j:
                            r = sim_game(grp[i], grp[j], "home", False, subs, "cup_group", "G", "TBD", BASE_RATINGS, season, forced_weather=forced_weather_setting())
                            hs, as_ = r["home_score"], r["away_score"]
                            g_gf[grp[i]] += hs; g_gf[grp[j]] += as_
                            g_gd[grp[i]] += hs - as_; g_gd[grp[j]] += as_ - hs
                            if hs > as_: g_pts[grp[i]] += 3
                            elif as_ > hs: g_pts[grp[j]] += 3
                            else: g_pts[grp[i]] += 1; g_pts[grp[j]] += 1
                ordered = sorted(grp, key=lambda x: (-g_pts[x], -g_gd[x], -g_gf[x]))
                winners.append(ordered[0]); runners.append(ordered[1])
            
            seeds = winners + runners
            
            def play_bo7(hi, lo):
                wins = {hi:0, lo:0}
                for g in range(7):
                    res = sim_game(hi, lo, "home", True, subs, "cup_knockout", "Bo7", "TBD", BASE_RATINGS, season, forced_weather=forced_weather_setting())
                    wins[res["winner"]] += 1
                return hi if wins[hi] > wins[lo] else lo

            r16_w = [play_bo7(seeds[i], seeds[15-i]) for i in range(8)]
            qf_w = [play_bo7(r16_w[a], r16_w[b]) for a, b in [(0,7),(3,4),(1,6),(2,5)]]
            sf_w = [play_bo7(qf_w[0], qf_w[1]), play_bo7(qf_w[2], qf_w[3])]
            cup_final = sim_game(sf_w[0], sf_w[1], "neutral", True, subs, "cup_final", "Final", "Oct 26", BASE_RATINGS, season, forced_weather=forced_weather_setting())
            cup_champ = cup_final["winner"]
            stats[cup_champ]["CupTitles"] += 1

            # 3. SOE
            t = TEAMS[:]
            substream_shuffle(t, subs.get("soe_draw"))
            def sim_2leg(t1, t2):
                r1 = sim_game(t1, t2, "home", False, subs, "soe_r32", "L1", "TBD", BASE_RATINGS, season, forced_weather=forced_weather_setting())
                r2 = sim_game(t2, t1, "home", False, subs, "soe_r32", "L2", "TBD", BASE_RATINGS, season, forced_weather=forced_weather_setting())
                winner, _, _ = resolve_soe_tie(t1, t2, r1, r2, subs, "TBD")
                return winner
            r32_w = [sim_2leg(t[i*2], t[i*2+1]) for i in range(16)]
            r16_w = [sim_2leg(r32_w[i*2], r32_w[i*2+1]) for i in range(8)]
            qf_w = [sim_2leg(r16_w[i*2], r16_w[i*2+1]) for i in range(4)]
            sf_w = [sim_2leg(qf_w[i*2], qf_w[i*2+1]) for i in range(2)]
            soe_final = sim_game(sf_w[0], sf_w[1], "neutral", True, subs, "soe_final", "Final", "Nov 24", BASE_RATINGS, season, forced_weather=forced_weather_setting())
            soe_champ = soe_final["winner"]
            stats[soe_champ]["SOETitles"] += 1

            # 4. Playoffs
            champions_in_order = []
            for c in [league_champ, cup_champ, soe_champ]:
                if c not in champions_in_order: champions_in_order.append(c)
            n_distinct = len(champions_in_order)
            format_type = "13-Team" if league_champ == cup_champ else "14-Team"
            
            seeds_dict = {i + 1: c for i, c in enumerate(champions_in_order)}
            wc_seed = n_distinct + 1
            total_seeds = 14 if format_type == "14-Team" else 13
            league_slots = total_seeds - n_distinct - 1
            available = [x for x in ranked if x not in seeds_dict.values()]
            for i, x in enumerate(available[:league_slots]): seeds_dict[wc_seed + 1 + i] = x
            wc_pool = [x for x in ranked if x not in seeds_dict.values()]
            wc_choice = wc_pool[subs.get("wildcard").next_int(0, len(wc_pool) - 1)]
            seeds_dict[wc_seed] = wc_choice
            for x in seeds_dict.values(): stats[x]["PlayoffApps"] += 1

            wc_pairs = [(3 + i, 14 - i) for i in range(6)] if format_type == "14-Team" else [(2 + i, 13 - i) for i in range(6)]
            wc_w = [sim_game(seeds_dict[hi], seeds_dict[lo], "home", True, subs, "playoff_wc", "WC", "TBD", BASE_RATINGS, season, forced_weather=forced_weather_setting())["winner"] for hi, lo in wc_pairs]
            
            seed_lk = {v: k for k, v in seeds_dict.items()}
            div_w = []
            if format_type == "14-Team":
                div_teams = [seeds_dict[1], seeds_dict[2]] + wc_w
                div_srt = sorted(div_teams, key=lambda x: seed_lk[x])
                for i in range(4):
                    div_w.append(sim_game(div_srt[i], div_srt[7-i], "home", True, subs, "playoff_div", "Div", "TBD", BASE_RATINGS, season, forced_weather=forced_weather_setting())["winner"])
            else:
                wc_srt = sorted(wc_w, key=lambda x: seed_lk[x])
                for i in range(3):
                    div_w.append(sim_game(wc_srt[i], wc_srt[5-i], "home", True, subs, "playoff_div", "Div", "TBD", BASE_RATINGS, season, forced_weather=forced_weather_setting())["winner"])
                div_w = [seeds_dict[1]] + div_w
            
            bc_srt = sorted(div_w, key=lambda x: seed_lk[x])
            bc_w = [sim_game(bc_srt[i[0]], bc_srt[i[1]], "home", True, subs, "playoff_bracket", "BC", "TBD", BASE_RATINGS, season, forced_weather=forced_weather_setting())["winner"] for i in [(0, 3), (1, 2)]]
            
            sb = sim_game(bc_w[0], bc_w[1], "neutral", True, subs, "super_bowl", "SB", "Dec 23", BASE_RATINGS, season, forced_weather=forced_weather_setting())
            sb_champ = sb["winner"]
            stats[sb_champ]["SBTitles"] += 1
            
            all_season_records.append({
                "Season": season, "Seed": season_seed,
                "League Champ": league_champ, "Cup Champ": cup_champ,
                "SOE Champ": soe_champ, "Super Bowl Champ": sb_champ
            })
            progress_bar.progress(season / seasons)

        status_text.success("Simulation Complete!")
        
        # Show Season Summaries
        st.subheader("Season Results")
        st.dataframe(pd.DataFrame(all_season_records), hide_index=True, use_container_width=True)
        
        # Show Final Tally (Mode 7)
        if mode_num == 7:
            st.subheader("Franchise Totals")
            tally_rows = [{"Team": t, **s} for t, s in stats.items()]
            st.dataframe(pd.DataFrame(tally_rows), hide_index=True, use_container_width=True)

        # Download button
        json_data = json.dumps({"run_seed": run_seed, "results": all_season_records}, indent=2)
        st.download_button("⬇️ Download JSON Results", json_data, file_name=f"lct_results_{run_seed}.json", mime="application/json")
