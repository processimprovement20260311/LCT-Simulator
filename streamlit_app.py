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
import os
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
            "hac_games","hac_draw"]}
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
    "HAL": {"OFF": 91, "DEF": 99, "SPT": 90}, "SRA": {"OFF": 79, "DEF": 66, "SPT": 77}, "AMB": {"OFF": 69, "DEF": 72, "SPT": 74},
    "CRT": {"OFF": 69, "DEF": 69, "SPT": 75}, "KEL": {"OFF": 72, "DEF": 66, "SPT": 76}, "KAT": {"OFF": 67, "DEF": 73, "SPT": 79},
    "RLY": {"OFF": 69, "DEF": 61, "SPT": 75}, "GRG": {"OFF": 69, "DEF": 67, "SPT": 69}
}

BASE_RATINGS = {team: Ratings(stats["OFF"], stats["DEF"], stats["SPT"], round((stats["OFF"]*3 + stats["DEF"]*3 + stats["SPT"]) / 7, 2)) for team, stats in RAW_RATINGS.items()}
TEAMS = list(BASE_RATINGS.keys())

# ============================================================
# LEAGUE PHASE SCHEDULE — governance-compliant
# ============================================================
MASTER_SCHEDULE_PATH = "master_schedule.json"

def _circle_method_rounds(teams):
    n = len(teams)
    assert n % 2 == 0
    arr = teams[:]
    rounds = []
    for r in range(n - 1):
        pairs = []
        for i in range(n // 2):
            pairs.append((arr[i], arr[n - 1 - i]))
        rounds.append(pairs)
        arr = [arr[0]] + [arr[-1]] + arr[1:-1]
    return rounds

def build_master_schedule(teams):
    base = _circle_method_rounds(teams)
    oriented = []
    for r_idx, pairs in enumerate(base):
        round_games = []
        for a, b in pairs:
            if (r_idx + teams.index(a)) % 2 == 0:
                round_games.append((a, b))
            else:
                round_games.append((b, a))
        oriented.append(round_games)
    return {"teams": teams, "base_rounds": oriented}

def load_or_build_master_schedule(teams):
    if os.path.exists(MASTER_SCHEDULE_PATH):
        try:
            with open(MASTER_SCHEDULE_PATH, "r") as f:
                data = json.load(f)
            if data.get("teams") == teams:
                return data
        except Exception:
            pass
    data = build_master_schedule(teams)
    with open(MASTER_SCHEDULE_PATH, "w") as f:
        json.dump(data, f, indent=2)
    return data

def build_league_season_schedule(master, season_num, defending_champ=None):
    teams = master["teams"]
    base = master["base_rounds"]

    nso_idx = (season_num - 1) % 31
    regular_round_indices = [(nso_idx + k) % 31 for k in range(1, 31)]

    schedule = []
    for r_num, base_idx in enumerate(regular_round_indices, start=1):
        for home, away in base[base_idx]:
            schedule.append({"round": r_num, "home": home, "away": away, "venue": "home"})

    nso_pairs = base[nso_idx]
    for home, away in nso_pairs:
        schedule.append({"round": 31, "home": home, "away": away, "venue": "neutral"})
        schedule.append({"round": 32, "home": away, "away": home, "venue": "neutral"})

    for r_num, base_idx in enumerate(regular_round_indices, start=33):
        for home, away in base[base_idx]:
            schedule.append({"round": r_num, "home": away, "away": home, "venue": "home"})

    if season_num >= 2 and defending_champ is not None:
        r1_games = [g for g in schedule if g["round"] == 1]
        champ_game = None
        for g in r1_games:
            if g["home"] == defending_champ or g["away"] == defending_champ:
                champ_game = g
                break
        if champ_game is not None and champ_game["away"] == defending_champ:
            opponent = champ_game["home"]
            champ_game["home"] = defending_champ
            champ_game["away"] = opponent
            for g in schedule:
                if g["round"] >= 33 and g["home"] == opponent and g["away"] == defending_champ:
                    g["home"] = defending_champ
                    g["away"] = opponent
                    break

    counts = {t: 0 for t in teams}
    for g in schedule:
        counts[g["home"]] += 1
        counts[g["away"]] += 1
    assert all(c == 62 for c in counts.values()), f"Bad game count: {counts}"
    assert len(schedule) == 992, f"Bad total games: {len(schedule)}"
    assert len(set(g["round"] for g in schedule)) == 62

    return schedule

def assign_league_dates(schedule):
    def daterange(start, end):
        d = start
        out = []
        while d <= end:
            out.append(d)
            d += timedelta(days=1)
        return out

    Y = 2026
    mar3 = date(Y, 3, 3)
    mar4 = date(Y, 3, 4)
    may22 = date(Y, 5, 22)
    may23 = date(Y, 5, 23)
    may26 = date(Y, 5, 26)
    jun1 = date(Y, 6, 1)
    aug26 = date(Y, 8, 26)

    r1 = [g for g in schedule if g["round"] == 1]
    r2_30 = [g for g in schedule if 2 <= g["round"] <= 30]
    r31_32 = [g for g in schedule if 31 <= g["round"] <= 32]
    r33_61 = [g for g in schedule if 33 <= g["round"] <= 61]
    r62 = [g for g in schedule if g["round"] == 62]

    opener = r1[0]
    opener["date"] = "Mar 03"
    remaining_r1 = r1[1:]

    window = daterange(mar4, may22)
    pool = remaining_r1 + r2_30
    used_on_day = {}
    day_idx = 0
    for g in pool:
        placed = False
        for attempt in range(len(window)):
            d = window[(day_idx + attempt) % len(window)]
            key = d.isoformat()
            if key not in used_on_day:
                used_on_day[key] = set()
            if g["home"] not in used_on_day[key] and g["away"] not in used_on_day[key]:
                g["date"] = d.strftime("%b %d")
                used_on_day[key].add(g["home"])
                used_on_day[key].add(g["away"])
                day_idx = (day_idx + attempt + 1) % len(window)
                placed = True
                break
        if not placed:
            g["date"] = window[day_idx % len(window)].strftime("%b %d")

    nso_days = daterange(may23, may26)
    for i, g in enumerate(r31_32):
        g["date"] = nso_days[i // 8].strftime("%b %d")

    window2 = daterange(jun1, aug26)
    used_on_day2 = {}
    day_idx = 0
    for g in r33_61:
        for attempt in range(len(window2)):
            d = window2[(day_idx + attempt) % len(window2)]
            key = d.isoformat()
            if key not in used_on_day2:
                used_on_day2[key] = set()
            if g["home"] not in used_on_day2[key] and g["away"] not in used_on_day2[key]:
                g["date"] = d.strftime("%b %d")
                used_on_day2[key].add(g["home"])
                used_on_day2[key].add(g["away"])
                day_idx = (day_idx + attempt + 1) % len(window2)
                break

    for g in r62:
        g["date"] = "Aug 27"

    return schedule

# ============================================================
# LEAGUE TABLE
# ============================================================
def compute_league_table(schedule, game_results, tiebreak_rng=None):
    if tiebreak_rng is None:
        tiebreak_rng = Xoshiro(0)
    table = {t: {"pts": 0, "gd": 0, "gf": 0, "w": 0, "d": 0, "l": 0,
                 "away_pts": 0, "h2h": {}} for t in TEAMS}

    for r in game_results:
        if r.get("competition") != "league":
            continue
        h, a = r["home_team"], r["away_team"]
        hs, as_ = r["home_score"], r["away_score"]
        table[h]["gf"] += hs
        table[h]["gd"] += hs - as_
        table[a]["gf"] += as_
        table[a]["gd"] += as_ - hs
        table[a]["away_pts"] += as_

        h2h = table[h]["h2h"].setdefault(a, {"pts": 0, "gd": 0})
        h2h["pts"] += 3 if hs > as_ else (1 if hs == as_ else 0)
        h2h["gd"] += hs - as_
        h2h2 = table[a]["h2h"].setdefault(h, {"pts": 0, "gd": 0})
        h2h2["pts"] += 3 if as_ > hs else (1 if hs == as_ else 0)
        h2h2["gd"] += as_ - hs

        if hs > as_:
            table[h]["pts"] += 3; table[h]["w"] += 1; table[a]["l"] += 1
        elif as_ > hs:
            table[a]["pts"] += 3; table[a]["w"] += 1; table[h]["l"] += 1
        else:
            table[h]["pts"] += 1; table[h]["d"] += 1
            table[a]["pts"] += 1; table[a]["d"] += 1

    by_pts = {}
    for t in TEAMS:
        by_pts.setdefault(table[t]["pts"], []).append(t)

    ranked = []
    for pts in sorted(by_pts.keys(), reverse=True):
        grp = by_pts[pts]
        if len(grp) == 1:
            ranked.extend(grp)
            continue

        def h2h_pts(team):
            return sum(table[team]["h2h"].get(o, {"pts": 0})["pts"] for o in grp if o != team)
        def h2h_gd(team):
            return sum(table[team]["h2h"].get(o, {"gd": 0})["gd"] for o in grp if o != team)

        grp.sort(key=lambda x: (-h2h_pts(x), -table[x]["gd"], -h2h_gd(x),
                                -table[x]["away_pts"]))
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
# MODE 10 — INCREMENTAL SEASON STATE MACHINE
# ============================================================
MODE10_KEY = "mode10_season"

def _m10_game_subs(run_seed, season, tag, idx):
    return SubStreams(subseed(run_seed, f"S{season}:{tag}:{idx}"))

def m10_new_state(run_seed, season_num=1, defending_champ=None):
    master = load_or_build_master_schedule(TEAMS)
    schedule = assign_league_dates(
        build_league_season_schedule(master, season_num, defending_champ=defending_champ)
    )
    return {
        "run_seed": run_seed,
        "season": season_num,
        "phase": "league",
        "weather": forced_weather_setting(),
        "schedule": schedule,
        "league_results": [],
        "league_next_round": 1,
        "cup": None,
        "soe": None,
        "playoffs": None,
        "champ_history": [],
    }

def _m10_weather(state):
    return state["weather"]

# ---------- LEAGUE ----------
def m10_step_league(state, upto_round):
    upto_round = int(upto_round)
    start = state["league_next_round"]
    if upto_round < start:
        return state

    existing = {(r["round_num"], r["home_team"], r["away_team"])
                for r in state["league_results"]}

    for idx, g in enumerate(state["schedule"]):
        rn = g["round"]
        if rn < start or rn > upto_round:
            continue
        key = (rn, g["home"], g["away"])
        if key in existing:
            continue
        game_subs = _m10_game_subs(state["run_seed"], state["season"], "L", idx)
        res = sim_game(
            g["home"], g["away"], g["venue"], False, game_subs,
            "league", f"R{rn}", g["date"], BASE_RATINGS, state["season"],
            forced_weather=_m10_weather(state),
        )
        res["round_num"] = rn
        state["league_results"].append(res)
        existing.add(key)

    state["league_next_round"] = upto_round + 1
    return state

def m10_league_table(state):
    ranked, table = compute_league_table(
        state["schedule"], state["league_results"], Xoshiro(0)
    )
    return ranked, table

# ---------- CUP ----------
def _m10_init_cup(state):
    t_list = TEAMS[:]
    draw_subs = SubStreams(subseed(state["run_seed"], f"S{state['season']}:cup_draw"))
    substream_shuffle(t_list, draw_subs)
    groups = [t_list[i*4:(i+1)*4] for i in range(8)]
    return {
        "groups": groups,
        "stage": "group",
        "group_results": [],
        "group_done": [False]*8,
        "winners": None,
        "runners": None,
        "seeds": None,
        "r16": None,
        "qf": None,
        "sf": None,
        "final": None,
    }

def m10_step_cup_group(state, group_idx):
    cup = state["cup"]
    if cup["group_done"][group_idx]:
        return state
    grp = cup["groups"][group_idx]
    base_idx = group_idx * 1000
    for i in range(4):
        for j in range(4):
            if i == j:
                continue
            idx = base_idx + i*4 + j
            subs = _m10_game_subs(state["run_seed"], state["season"], "CG", idx)
            r = sim_game(grp[i], grp[j], "home", False, subs, "cup_group", "G",
                         "TBD", BASE_RATINGS, state["season"],
                         forced_weather=_m10_weather(state))
            cup["group_results"].append(r)
    cup["group_done"][group_idx] = True
    return state

def m10_finalize_cup_groups(state):
    cup = state["cup"]
    if not all(cup["group_done"]):
        return state
    winners, runners = [], []
    for gi, grp in enumerate(cup["groups"]):
        pts = {x: 0 for x in grp}; gd = {x: 0 for x in grp}; gf = {x: 0 for x in grp}
        for r in cup["group_results"]:
            if r["home_team"] in grp and r["away_team"] in grp:
                hs, as_ = r["home_score"], r["away_score"]
                gf[r["home_team"]] += hs; gf[r["away_team"]] += as_
                gd[r["home_team"]] += hs - as_; gd[r["away_team"]] += as_ - hs
                if hs > as_: pts[r["home_team"]] += 3
                elif as_ > hs: pts[r["away_team"]] += 3
                else: pts[r["home_team"]] += 1; pts[r["away_team"]] += 1
        ordered = sorted(grp, key=lambda x: (-pts[x], -gd[x], -gf[x]))
        winners.append(ordered[0]); runners.append(ordered[1])
    cup["winners"] = winners
    cup["runners"] = runners
    cup["seeds"] = winners + runners
    cup["stage"] = "r16"
    return state

def _m10_play_bo7(state, hi, lo, tag, idx):
    wins = {hi: 0, lo: 0}
    series = []
    for g in range(7):
        subs = _m10_game_subs(state["run_seed"], state["season"], tag, idx*10+g)
        res = sim_game(hi, lo, "home", True, subs, "cup_knockout", "Bo7", "TBD",
                       BASE_RATINGS, state["season"],
                       forced_weather=_m10_weather(state))
        wins[res["winner"]] += 1
        series.append(res)
    winner = hi if wins[hi] > wins[lo] else lo
    return winner, f"{hi} vs {lo}", f"{wins[hi]}-{wins[lo]}", series

def m10_step_cup_r16(state):
    cup = state["cup"]
    if cup["r16"] is not None:
        return state
    seeds = cup["seeds"]
    out = []
    for i in range(8):
        w, m, s, games = _m10_play_bo7(state, seeds[i], seeds[15-i], "C16", i)
        out.append({"winner": w, "matchup": m, "series": s, "games": games})
    cup["r16"] = out
    cup["stage"] = "qf"
    return state

def m10_step_cup_qf(state):
    cup = state["cup"]
    if cup["qf"] is not None:
        return state
    w = [x["winner"] for x in cup["r16"]]
    pairs = [(0,7),(3,4),(1,6),(2,5)]
    out = []
    for k, (a, b) in enumerate(pairs):
        winner, m, s, games = _m10_play_bo7(state, w[a], w[b], "CQF", k)
        out.append({"winner": winner, "matchup": m, "series": s, "games": games})
    cup["qf"] = out
    cup["stage"] = "sf"
    return state

def m10_step_cup_sf(state):
    cup = state["cup"]
    if cup["sf"] is not None:
        return state
    w = [x["winner"] for x in cup["qf"]]
    out = []
    for k in range(2):
        winner, m, s, games = _m10_play_bo7(state, w[k*2], w[k*2+1], "CSF", k)
        out.append({"winner": winner, "matchup": m, "series": s, "games": games})
    cup["sf"] = out
    cup["stage"] = "final"
    return state

def m10_step_cup_final(state):
    cup = state["cup"]
    if cup["final"] is not None:
        return state
    hi = cup["sf"][0]["winner"]; lo = cup["sf"][1]["winner"]
    subs = _m10_game_subs(state["run_seed"], state["season"], "CFIN", 0)
    res = sim_game(hi, lo, "neutral", True, subs, "cup_final", "Final", "Oct 26",
                   BASE_RATINGS, state["season"],
                   forced_weather=_m10_weather(state))
    cup["final"] = res
    cup["stage"] = "done"
    return state

# ---------- SOE ----------
def _m10_init_soe(state):
    t = TEAMS[:]
    draw = SubStreams(subseed(state["run_seed"], f"S{state['season']}:soe_draw"))
    substream_shuffle(t, draw)
    return {
        "teams": t,
        "stage": "r32",
        "r32": None, "r16": None, "qf": None, "sf": None, "final": None,
    }

def _m10_soe_2leg(state, t1, t2, tag, idx):
    s1 = _m10_game_subs(state["run_seed"], state["season"], tag+"A", idx)
    s2 = _m10_game_subs(state["run_seed"], state["season"], tag+"B", idx)
    r1 = sim_game(t1, t2, "home", False, s1, "soe_r32", "L1", "TBD",
                  BASE_RATINGS, state["season"], forced_weather=_m10_weather(state))
    r2 = sim_game(t2, t1, "home", False, s2, "soe_r32", "L2", "TBD",
                  BASE_RATINGS, state["season"], forced_weather=_m10_weather(state))
    tie_subs = _m10_game_subs(state["run_seed"], state["season"], tag+"OT", idx)
    winner, went_ot, ot_game = resolve_soe_tie(t1, t2, r1, r2, tie_subs, "TBD")
    a1 = r1["home_score"] + r2["away_score"]
    a2 = r1["away_score"] + r2["home_score"]
    agg = f"{a1}-{a2}"
    if a1 == a2:
        agg += " (Away Pts)" if not went_ot else " (OT)"
    return winner, f"{t1} vs {t2}", agg, [r1, r2] + ([ot_game] if ot_game else [])

def m10_step_soe_r32(state):
    soe = state["soe"]
    if soe["r32"] is not None: return state
    t = soe["teams"]
    out = []
    for i in range(16):
        w, m, a, games = _m10_soe_2leg(state, t[i*2], t[i*2+1], "S32", i)
        out.append({"winner": w, "matchup": m, "aggregate": a, "games": games})
    soe["r32"] = out; soe["stage"] = "r16"
    return state

def m10_step_soe_r16(state):
    soe = state["soe"]
    if soe["r16"] is not None: return state
    w = [x["winner"] for x in soe["r32"]]
    out = []
    for i in range(8):
        winner, m, a, games = _m10_soe_2leg(state, w[i*2], w[i*2+1], "S16", i)
        out.append({"winner": winner, "matchup": m, "aggregate": a, "games": games})
    soe["r16"] = out; soe["stage"] = "qf"
    return state

def m10_step_soe_qf(state):
    soe = state["soe"]
    if soe["qf"] is not None: return state
    w = [x["winner"] for x in soe["r16"]]
    out = []
    for i in range(4):
        winner, m, a, games = _m10_soe_2leg(state, w[i*2], w[i*2+1], "SQF", i)
        out.append({"winner": winner, "matchup": m, "aggregate": a, "games": games})
    soe["qf"] = out; soe["stage"] = "sf"
    return state

def m10_step_soe_sf(state):
    soe = state["soe"]
    if soe["sf"] is not None: return state
    w = [x["winner"] for x in soe["qf"]]
    out = []
    for i in range(2):
        winner, m, a, games = _m10_soe_2leg(state, w[i*2], w[i*2+1], "SSF", i)
        out.append({"winner": winner, "matchup": m, "aggregate": a, "games": games})
    soe["sf"] = out; soe["stage"] = "final"
    return state

def m10_step_soe_final(state):
    soe = state["soe"]
    if soe["final"] is not None: return state
    hi = soe["sf"][0]["winner"]; lo = soe["sf"][1]["winner"]
    subs = _m10_game_subs(state["run_seed"], state["season"], "SFIN", 0)
    res = sim_game(hi, lo, "neutral", True, subs, "soe_final", "Final", "Nov 24",
                   BASE_RATINGS, state["season"], forced_weather=_m10_weather(state))
    soe["final"] = res; soe["stage"] = "done"
    return state

# ---------- PLAYOFFS ----------
def _m10_init_playoffs(state):
    ranked, _ = m10_league_table(state)
    league_champ = ranked[0]
    cup_champ = state["cup"]["final"]["winner"]
    soe_champ = state["soe"]["final"]["winner"]

    champs_in_order = []
    for c in [league_champ, cup_champ, soe_champ]:
        if c not in champs_in_order:
            champs_in_order.append(c)
    n_distinct = len(champs_in_order)
    fmt = "13-Team" if league_champ == cup_champ else "14-Team"

    seeds_dict = {i+1: c for i, c in enumerate(champs_in_order)}
    wc_seed = n_distinct + 1
    total = 14 if fmt == "14-Team" else 13
    league_slots = total - n_distinct - 1
    available = [x for x in ranked if x not in seeds_dict.values()]
    for i, x in enumerate(available[:league_slots]):
        seeds_dict[wc_seed + 1 + i] = x
    wc_pool = [x for x in ranked if x not in seeds_dict.values()]
    wc_subs = SubStreams(subseed(state["run_seed"], f"S{state['season']}:wc"))
    wc_choice = wc_pool[wc_subs.next_int(0, len(wc_pool)-1)]
    seeds_dict[wc_seed] = wc_choice

    return {
        "format": fmt,
        "ranked": ranked,
        "league_champ": league_champ,
        "cup_champ": cup_champ,
        "soe_champ": soe_champ,
        "seeds": seeds_dict,
        "stage": "wc",
        "wc": None, "div": None, "bc": None, "sb": None,
    }

def m10_step_playoffs_wc(state):
    p = state["playoffs"]
    if p["wc"] is not None: return state
    seeds = p["seeds"]
    pairs = ([(3+i, 14-i) for i in range(6)] if p["format"] == "14-Team"
             else [(2+i, 13-i) for i in range(6)])
    out = []
    for i, (hi, lo) in enumerate(pairs):
        subs = _m10_game_subs(state["run_seed"], state["season"], "PWC", i)
        r = sim_game(seeds[hi], seeds[lo], "home", True, subs, "playoff_wc",
                     "WC", "TBD", BASE_RATINGS, state["season"],
                     forced_weather=_m10_weather(state))
        out.append(r)
    p["wc"] = out; p["stage"] = "div"
    return state

def m10_step_playoffs_div(state):
    p = state["playoffs"]
    if p["div"] is not None: return state
    seeds = p["seeds"]
    wc_w = [r["winner"] for r in p["wc"]]
    seed_lk = {v: k for k, v in seeds.items()}
    out = []
    if p["format"] == "14-Team":
        div_teams = [seeds[1], seeds[2]] + wc_w
        srt = sorted(div_teams, key=lambda x: seed_lk[x])
        for i in range(4):
            subs = _m10_game_subs(state["run_seed"], state["season"], "PDIV", i)
            r = sim_game(srt[i], srt[7-i], "home", True, subs, "playoff_div",
                         "Div", "TBD", BASE_RATINGS, state["season"],
                         forced_weather=_m10_weather(state))
            out.append(r)
    else:
        srt = sorted(wc_w, key=lambda x: seed_lk[x])
        for i in range(3):
            subs = _m10_game_subs(state["run_seed"], state["season"], "PDIV", i)
            r = sim_game(srt[i], srt[5-i], "home", True, subs, "playoff_div",
                         "Div", "TBD", BASE_RATINGS, state["season"],
                         forced_weather=_m10_weather(state))
            out.append(r)
    p["div"] = out; p["stage"] = "bc"
    return state

def m10_step_playoffs_bc(state):
    p = state["playoffs"]
    if p["bc"] is not None: return state
    seeds = p["seeds"]
    seed_lk = {v: k for k, v in seeds.items()}
    div_w = [r["winner"] for r in p["div"]]
    if p["format"] == "13-Team":
        div_w = [seeds[1]] + div_w
    srt = sorted(div_w, key=lambda x: seed_lk[x])
    out = []
    for i, (a, b) in enumerate([(0,3),(1,2)]):
        subs = _m10_game_subs(state["run_seed"], state["season"], "PBC", i)
        r = sim_game(srt[a], srt[b], "home", True, subs, "playoff_bracket",
                     "BC", "TBD", BASE_RATINGS, state["season"],
                     forced_weather=_m10_weather(state))
        out.append(r)
    p["bc"] = out; p["stage"] = "sb"
    return state

def m10_step_playoffs_sb(state):
    p = state["playoffs"]
    if p["sb"] is not None: return state
    bc_w = [r["winner"] for r in p["bc"]]
    subs = _m10_game_subs(state["run_seed"], state["season"], "PSB", 0)
    r = sim_game(bc_w[0], bc_w[1], "neutral", True, subs, "super_bowl",
                 "SB", "Dec 23", BASE_RATINGS, state["season"],
                 forced_weather=_m10_weather(state))
    p["sb"] = r; p["stage"] = "done"
    return state

# ============================================================
# STREAMLIT UI
# ============================================================
st.set_page_config(page_title="LCT Simulator", page_icon="🏈", layout="wide")
st.title("🏈 LCT Simulation")
st.caption("V1.5 engine — Full App")

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

mode = st.selectbox("Competition mode", [
    "1 — Fixed Games", "2 — League Phase", "3 — Cup Phase",
    "4 — SOE Phase", "5 — Single Elimination",
    "6 — Full Season Simulation", "7 — Multi-Season Validator",
    "8 — HAC Mode", "9 — Standalone Playoffs",
    "10 — Incremental Season Simulation"
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
    elif mode_num == 10:
        st.info("Incremental mode: simulate one round, one group, or one knockout stage at a time. "
                "State persists across clicks. Use the Reset button to start over.")

# We only show the "Run Simulation" button for Modes 1-9
# Mode 10 has its own internal buttons to manage state.
if mode_num != 10:
    run_clicked = st.button("▶ Run Simulation", type="primary")
else:
    run_clicked = False

# ---------------------------------------------------------
# MODE 10 (INCREMENTAL SEASON) — Handled outside `run_clicked`
# ---------------------------------------------------------
if mode_num == 10:
    if MODE10_KEY not in st.session_state:
        st.session_state[MODE10_KEY] = None

    s = st.session_state[MODE10_KEY]

    if s is None:
        st.info("Start a new incremental season. State persists across button clicks — "
                "you can simulate one round, look at the table, then simulate more.")
        if st.button("▶ Start New Incremental Season", type="primary"):
            seed = (master_seed if master_seed is not None
                    else int(time.time() * 1000) & MASK64)
            st.session_state[MODE10_KEY] = m10_new_state(seed, season_num=1)
            st.rerun()
    else:
        st.caption(f"Seed: `{s['run_seed']}` · Season {s['season']} · "
                   f"Phase: **{s['phase']}** · Weather: `{s['weather']}`")

        if st.button("🔄 Reset season"):
            st.session_state[MODE10_KEY] = None
            st.rerun()

        st.markdown("---")

        # ---- LEAGUE PHASE ----
        if s["phase"] == "league":
            nxt = s["league_next_round"]
            st.subheader(f"League Phase — next round: {nxt} of 62")
            if nxt <= 62:
                target = st.slider("Simulate through round", nxt, 62,
                                   min(nxt + 4, 62))
                c1, c2 = st.columns(2)
                if c1.button(f"Simulate to R{target}"):
                    s = m10_step_league(s, target)
                    st.session_state[MODE10_KEY] = s
                    st.rerun()
                if c2.button("Finish league phase"):
                    s = m10_step_league(s, 62)
                    s["phase"] = "cup"
                    s["cup"] = _m10_init_cup(s)
                    st.session_state[MODE10_KEY] = s
                    st.rerun()

            ranked, table = m10_league_table(s)
            played = len(s["league_results"]) // 16
            st.write(f"**League standings after {played} rounds**")
            st.dataframe(pd.DataFrame([
                {"Pos": i+1, "Team": t,
                 "W": table[t]["w"], "D": table[t]["d"], "L": table[t]["l"],
                 "Pts": table[t]["pts"], "GD": table[t]["gd"],
                 "GF": table[t]["gf"]}
                for i, t in enumerate(ranked)
            ]), hide_index=True, use_container_width=True)

        # ---- CUP PHASE ----
        elif s["phase"] == "cup":
            cup = s["cup"]
            st.subheader(f"Cup Phase — stage: {cup['stage']}")

            if cup["stage"] == "group":
                done = sum(cup["group_done"])
                st.write(f"Groups simulated: **{done}/8**")
                gsel = st.selectbox(
                    "Simulate which group?",
                    [f"Group {chr(ord('A')+i)}" + (" ✓" if cup["group_done"][i] else "")
                     for i in range(8)],
                )
                gi = ord(gsel.split()[1][0]) - ord('A')
                c1, c2 = st.columns(2)
                if c1.button(f"Simulate {gsel.split()[0]} {gsel.split()[1]}") and not cup["group_done"][gi]:
                    s = m10_step_cup_group(s, gi)
                    st.session_state[MODE10_KEY] = s
                    st.rerun()
                if c2.button("Simulate all remaining groups"):
                    for k in range(8):
                        if not cup["group_done"][k]:
                            s = m10_step_cup_group(s, k)
                    st.session_state[MODE10_KEY] = s
                    st.rerun()
                if all(cup["group_done"]):
                    if st.button("✅ Finalize groups → Round of 16"):
                        s = m10_finalize_cup_groups(s)
                        st.session_state[MODE10_KEY] = s
                        st.rerun()

                for k, grp in enumerate(cup["groups"]):
                    if not cup["group_done"][k]:
                        continue
                    pts = {x:0 for x in grp}; gd = {x:0 for x in grp}; gf = {x:0 for x in grp}
                    for r in cup["group_results"]:
                        if r["home_team"] in grp and r["away_team"] in grp:
                            hs, as_ = r["home_score"], r["away_score"]
                            gf[r["home_team"]] += hs; gf[r["away_team"]] += as_
                            gd[r["home_team"]] += hs-as_; gd[r["away_team"]] += as_-hs
                            if hs > as_: pts[r["home_team"]] += 3
                            elif as_ > hs: pts[r["away_team"]] += 3
                            else: pts[r["home_team"]] += 1; pts[r["away_team"]] += 1
                    ordered = sorted(grp, key=lambda x: (-pts[x], -gd[x], -gf[x]))
                    st.write(f"**Group {chr(ord('A')+k)}**")
                    st.dataframe(pd.DataFrame([
                        {"Pos": i+1, "Team": t, "Pts": pts[t], "GD": gd[t], "GF": gf[t]}
                        for i, t in enumerate(ordered)
                    ]), hide_index=True, use_container_width=True)

            elif cup["stage"] == "r16":
                st.write("Groups complete. Simulate Round of 16 (Bo7).")
                if st.button("Simulate Round of 16"):
                    s = m10_step_cup_r16(s); st.session_state[MODE10_KEY] = s; st.rerun()
                st.dataframe(pd.DataFrame([
                    {"Matchup": w["matchup"], "Series": w["series"], "Winner": w["winner"]}
                    for w in (cup["r16"] or [])
                ]), hide_index=True, use_container_width=True)

            elif cup["stage"] == "qf":
                st.write("Round of 16 complete.")
                if st.button("Simulate Quarterfinals"):
                    s = m10_step_cup_qf(s); st.session_state[MODE10_KEY] = s; st.rerun()
                st.dataframe(pd.DataFrame([
                    {"Matchup": w["matchup"], "Series": w["series"], "Winner": w["winner"]}
                    for w in (cup["r16"] or [])
                ]), hide_index=True, use_container_width=True)

            elif cup["stage"] == "sf":
                st.write("Quarterfinals complete.")
                if st.button("Simulate Semifinals"):
                    s = m10_step_cup_sf(s); st.session_state[MODE10_KEY] = s; st.rerun()
                st.dataframe(pd.DataFrame([
                    {"Matchup": w["matchup"], "Series": w["series"], "Winner": w["winner"]}
                    for w in (cup["qf"] or [])
                ]), hide_index=True, use_container_width=True)

            elif cup["stage"] == "final":
                st.write("Semifinals complete.")
                if st.button("Simulate Cup Final"):
                    s = m10_step_cup_final(s); st.session_state[MODE10_KEY] = s; st.rerun()
                st.dataframe(pd.DataFrame([
                    {"Matchup": w["matchup"], "Series": w["series"], "Winner": w["winner"]}
                    for w in (cup["sf"] or [])
                ]), hide_index=True, use_container_width=True)

            elif cup["stage"] == "done":
                f = cup["final"]
                st.success(f"Cup Champion: **{f['winner']}**")
                st.write(f"Final: {f['away_team']} ({f['away_score']}) at "
                         f"{f['home_team']} ({f['home_score']})")
                if st.button("➡ Proceed to SOE Phase"):
                    s["phase"] = "soe"
                    s["soe"] = _m10_init_soe(s)
                    st.session_state[MODE10_KEY] = s
                    st.rerun()

        # ---- SOE PHASE ----
        elif s["phase"] == "soe":
            soe = s["soe"]
            st.subheader(f"SOE Phase — stage: {soe['stage']}")
            steps = [
                ("r32", "Simulate Round of 32", m10_step_soe_r32),
                ("r16", "Simulate Round of 16", m10_step_soe_r16),
                ("qf",  "Simulate Quarterfinals", m10_step_soe_qf),
                ("sf",  "Simulate Semifinals", m10_step_soe_sf),
                ("final", "Simulate SOE Final", m10_step_soe_final),
            ]
            stage_order = [x[0] for x in steps]
            cur_idx = stage_order.index(soe["stage"]) if soe["stage"] in stage_order else -1

            if soe["stage"] == "done":
                f = soe["final"]
                st.success(f"SOE Champion: **{f['winner']}**")
                if st.button("➡ Proceed to Playoffs"):
                    s["phase"] = "playoffs"
                    s["playoffs"] = _m10_init_playoffs(s)
                    st.session_state[MODE10_KEY] = s
                    st.rerun()
            else:
                label = steps[cur_idx][1]
                if st.button(label):
                    s = steps[cur_idx][2](s)
                    st.session_state[MODE10_KEY] = s
                    st.rerun()

            for key, label in [("r32","Round of 32"),("r16","Round of 16"),
                               ("qf","Quarterfinals"),("sf","Semifinals")]:
                data = soe.get(key)
                if data:
                    st.write(f"**{label}**")
                    st.dataframe(pd.DataFrame([
                        {"Matchup": w["matchup"], "Aggregate": w["aggregate"],
                         "Winner": w["winner"]}
                        for w in data
                    ]), hide_index=True, use_container_width=True)
            if soe["final"]:
                f = soe["final"]
                st.write("**Final**")
                st.dataframe(pd.DataFrame([{
                    "Away": f["away_team"], "Away Score": f["away_score"],
                    "Home": f["home_team"], "Home Score": f["home_score"],
                    "Winner": f["winner"]
                }]), hide_index=True, use_container_width=True)

        # ---- PLAYOFFS PHASE ----
        elif s["phase"] == "playoffs":
            p = s["playoffs"]
            st.subheader(f"Playoffs — {p['format']} — stage: {p['stage']}")

            if p["stage"] == "done":
                sb = p["sb"]
                st.success(f"Super Bowl Champion: **{sb['winner']}**")
                st.write(f"{sb['away_team']} ({sb['away_score']}) at "
                         f"{sb['home_team']} ({sb['home_score']})")

                if st.button("➡ Start Next Season"):
                    new_seed = SubStreams(subseed(s["run_seed"],
                                                  f"S{s['season']+1}:season_roll")).get("league_schedule").next_int(0, 2**31-1)
                    st.session_state[MODE10_KEY] = m10_new_state(
                        new_seed, season_num=s["season"]+1,
                        defending_champ=sb["winner"]
                    )
                    st.rerun()
            else:
                step_map = [
                    ("wc", "Simulate Wild Card", m10_step_playoffs_wc),
                    ("div", "Simulate Divisional Round", m10_step_playoffs_div),
                    ("bc", "Simulate Bracket Championship", m10_step_playoffs_bc),
                    ("sb", "Simulate Super Bowl", m10_step_playoffs_sb),
                ]
                for key, label, fn in step_map:
                    if p["stage"] == key:
                        if st.button(label):
                            s = fn(s)
                            st.session_state[MODE10_KEY] = s
                            st.rerun()
                        break

            st.write("**Seeding**")
            st.dataframe(pd.DataFrame([
                {"Seed": k, "Team": v} for k, v in sorted(p["seeds"].items())
            ]), hide_index=True, use_container_width=True)

            for key, label in [("wc","Wild Card"),("div","Divisional"),
                               ("bc","Bracket Championship"),("sb","Super Bowl")]:
                data = p.get(key)
                if not data: continue
                st.write(f"**{label}**")
                games = data if isinstance(data, list) else [data]
                st.dataframe(pd.DataFrame([{
                    "Away": g["away_team"], "Away Score": g["away_score"],
                    "Home": g["home_team"], "Home Score": g["home_score"],
                    "Winner": g["winner"]
                } for g in games]), hide_index=True, use_container_width=True)

# ---------------------------------------------------------
# MODES 1-9 (Original logic) — Handled inside `run_clicked`
# ---------------------------------------------------------
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
            master = load_or_build_master_schedule(TEAMS)
            schedule = build_league_season_schedule(master, season_num=1, defending_champ=None)
            schedule = assign_league_dates(schedule)

            results = []
            for g in schedule:
                results.append(sim_game(
                    g["home"], g["away"], g["venue"], False, subs,
                    "league", f"R{g['round']}", g["date"],
                    BASE_RATINGS, 1,
                    forced_weather=forced_weather_setting()
                ))

            ranked, table = compute_league_table(schedule, results, subs.get("tiebreaks"))
            st.success(f"League Champion: **{ranked[0]}**")

            df_rows = [
                {"Pos": i + 1, "Team": t,
                 "W": table[t]["w"], "D": table[t]["d"], "L": table[t]["l"],
                 "Pts": table[t]["pts"], "GD": table[t]["gd"], "GF": table[t]["gf"]}
                for i, t in enumerate(ranked)
            ]
            st.dataframe(pd.DataFrame(df_rows), hide_index=True, use_container_width=True)

            games_per_team = {t: 0 for t in TEAMS}
            for r in results:
                if r["competition"] == "league":
                    games_per_team[r["home_team"]] += 1
                    games_per_team[r["away_team"]] += 1
            st.caption(f"Games per team: {sorted(set(games_per_team.values()))} (expected [62]) — total games: {len(results)}")

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

            r16_w = []
            for i in range(8):
                hi, lo = seeds[i], seeds[15-i]
                w, hw, lw = play_bo7(hi, lo)
                r16_w.append((w, f"{hi} vs {lo}", f"{hw}-{lw}"))

            qf_w = []
            for a, b in [(0,7), (3,4), (1,6), (2,5)]:
                hi, lo = r16_w[a][0], r16_w[b][0]
                w, hw, lw = play_bo7(hi, lo)
                qf_w.append((w, f"{hi} vs {lo}", f"{hw}-{lw}"))

            sf_w = []
            for i in range(2):
                hi, lo = qf_w[i*2][0], qf_w[i*2+1][0]
                w, hw, lw = play_bo7(hi, lo)
                sf_w.append((w, f"{hi} vs {lo}", f"{hw}-{lw}"))

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

    # ---------------- MODE 5 (SINGLE ELIM) ----------------
    elif mode_num == 5:
        bracket = mode_params.get("bracket_teams", [])
        num_teams = len(bracket)
        if num_teams not in [2, 4, 8, 16, 32]:
            st.error(f"Error: You selected {num_teams} teams. The bracket must be 2, 4, 8, 16, or 32 teams.")
        else:
            with st.spinner(f"Simulating {num_teams}-Team Single Elimination Bracket..."):
                subs = SubStreams(run_seed)
                substream_shuffle(bracket, subs.get("playoff_games"))
                round_name = f"Round of {num_teams}" if num_teams > 2 else "Final"

                while len(bracket) > 1:
                    st.write(f"**{round_name}**")
                    next_round = []
                    rows = []
                    for i in range(0, len(bracket), 2):
                        t1, t2 = bracket[i], bracket[i+1]
                        res = sim_game(t1, t2, "home", True, subs, "playoff_wc", round_name, "TBD", BASE_RATINGS, 1, forced_weather=forced_weather_setting())
                        rows.append({"Matchup": f"{t1} vs {t2}", "Score": f"{res['home_score']}-{res['away_score']}", "Winner": res['winner']})
                        next_round.append(res["winner"])

                    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
                    bracket = next_round

                    if len(bracket) == 4: round_name = "Semifinals"
                    elif len(bracket) == 2: round_name = "Final"
                    elif len(bracket) > 4: round_name = f"Round of {len(bracket)}"

                st.success(f"Champion: **{bracket[0]}**")

    # ---------------- MODE 8 (HAC) ----------------
    elif mode_num == 8:
        hac_teams = mode_params.get("hac_teams", [])
        n = len(hac_teams)
        group_plan = {
            6:  (2, 3), 8:  (2, 4), 10: (2, 5),
            12: (4, 3), 14: (2, 7), 16: (4, 4),
        }
        if n not in group_plan:
            st.error(f"Error: You selected {n} teams. HAC Mode requires 6, 8, 10, 12, 14, or 16 teams.")
        else:
            with st.spinner(f"Simulating HAC Mode ({n} teams)..."):
                subs = SubStreams(run_seed)
                teams = hac_teams[:]
                substream_shuffle(teams, subs.get("hac_draw"))
                num_groups, gsize = group_plan[n]
                groups = [teams[i*gsize:(i+1)*gsize] for i in range(num_groups)]
                group_labels = [chr(ord('A') + i) for i in range(num_groups)]

                group_standings = []
                for gi, grp in enumerate(groups):
                    pts = {x: 0 for x in grp}
                    gd  = {x: 0 for x in grp}
                    gf  = {x: 0 for x in grp}
                    for i in range(len(grp)):
                        for j in range(len(grp)):
                            if i == j: continue
                            r = sim_game(grp[i], grp[j], "home", False, subs,
                                         "hac_group", f"Group {group_labels[gi]}",
                                         "TBD", BASE_RATINGS, 1,
                                         forced_weather=forced_weather_setting())
                            hs, as_ = r["home_score"], r["away_score"]
                            gf[grp[i]] += hs; gf[grp[j]] += as_
                            gd[grp[i]] += hs - as_; gd[grp[j]] += as_ - hs
                            if hs > as_: pts[grp[i]] += 3
                            elif as_ > hs: pts[grp[j]] += 3
                            else: pts[grp[i]] += 1; pts[grp[j]] += 1
                    ordered = sorted(grp, key=lambda x: (-pts[x], -gd[x], -gf[x]))
                    group_standings.append((group_labels[gi], ordered,
                                            {"pts": pts, "gd": gd, "gf": gf}))

                for label, ordered, tb in group_standings:
                    st.write(f"**Group {label}**")
                    rows = [{"Pos": i+1, "Team": t, "Pts": tb["pts"][t],
                             "GD": tb["gd"][t], "GF": tb["gf"][t]}
                            for i, t in enumerate(ordered)]
                    st.dataframe(pd.DataFrame(rows), hide_index=True,
                                 use_container_width=True)

                advance_per_group = 4 if n == 14 else 2
                advancers = []
                for rank in range(advance_per_group):
                    for label, ordered, tb in group_standings:
                        if rank < len(ordered):
                            advancers.append(ordered[rank])

                def sim_2leg(t1, t2):
                    r1 = sim_game(t1, t2, "home", False, subs, "hac_knockout",
                                  "L1", "TBD", BASE_RATINGS, 1,
                                  forced_weather=forced_weather_setting())
                    r2 = sim_game(t2, t1, "home", False, subs, "hac_knockout",
                                  "L2", "TBD", BASE_RATINGS, 1,
                                  forced_weather=forced_weather_setting())
                    winner, _, _ = resolve_soe_tie(t1, t2, r1, r2, subs, "TBD")
                    a1 = r1["home_score"] + r2["away_score"]
                    a2 = r1["away_score"] + r2["home_score"]
                    agg_str = f"{a1}-{a2}"
                    if a1 == a2: agg_str += " (Away Pts)"
                    return winner, f"{t1} vs {t2}", agg_str

                ko_rounds = []
                current = advancers
                if len(current) == 4:
                    round_name = "Semifinals"
                elif len(current) == 8:
                    round_name = "Quarterfinals"
                else:
                    round_name = f"Round of {len(current)}"

                while len(current) > 2:
                    pairings = []
                    lo, hi = 0, len(current) - 1
                    while lo < hi:
                        pairings.append((current[lo], current[hi]))
                        lo += 1; hi -= 1
                    results = []
                    next_round = []
                    for t1, t2 in pairings:
                        w, matchup, agg = sim_2leg(t1, t2)
                        results.append((w, matchup, agg))
                        next_round.append(w)
                    ko_rounds.append((round_name, results))
                    current = next_round
                    if len(current) == 2: round_name = "Final"
                    elif len(current) == 4: round_name = "Semifinals"
                    elif len(current) > 4: round_name = f"Round of {len(current)}"

                final_two = current
                hac_final = sim_game(final_two[0], final_two[1], "neutral", True,
                                     subs, "hac_final", "Final", "TBD",
                                     BASE_RATINGS, 1,
                                     forced_weather=forced_weather_setting())

                for rname, rdata in ko_rounds:
                    st.write(f"**{rname}**")
                    rows = [{"Matchup": m, "Winner": w, "Aggregate": s}
                            for (w, m, s) in rdata]
                    st.dataframe(pd.DataFrame(rows), hide_index=True,
                                 use_container_width=True)

                st.write("**Final**")
                st.dataframe(pd.DataFrame([{
                    "Away": hac_final["away_team"],
                    "Away Score": hac_final["away_score"],
                    "Home": hac_final["home_team"],
                    "Home Score": hac_final["home_score"],
                    "Winner": hac_final["winner"],
                }]), hide_index=True, use_container_width=True)

                st.success(f"HAC Champion: **{hac_final['winner']}**")

    # ---------------- MODE 9 (STANDALONE PLAYOFFS) ----------------
    elif mode_num == 9:
        p_teams = mode_params.get("playoff_teams", [])
        n = len(p_teams)

        if n not in [13, 14]:
            st.error(f"Error: You selected {n} teams. Standalone Playoffs requires exactly 13 or 14 teams.")
        elif len(set(p_teams)) != n:
            st.error("Error: Duplicate teams selected. Please ensure all teams are unique.")
        else:
            with st.spinner(f"Simulating Standalone Playoffs ({n}-Team format)..."):
                subs = SubStreams(run_seed)
                format_type = "14-Team" if n == 14 else "13-Team"

                raw_seeds = {i + 1: team for i, team in enumerate(p_teams)}
                seeds_dict = {}

                if format_type == "14-Team":
                    seeds_dict[1] = raw_seeds[1]
                    seeds_dict[2] = raw_seeds[2]
                    seeds_dict[3] = raw_seeds[3]
                    seeds_dict[4] = raw_seeds[14]
                    for i in range(4, 14):
                        seeds_dict[i + 1] = raw_seeds[i]
                else:
                    seeds_dict[1] = raw_seeds[1]
                    seeds_dict[2] = raw_seeds[2]
                    seeds_dict[3] = raw_seeds[13]
                    for i in range(3, 13):
                        seeds_dict[i + 1] = raw_seeds[i]

                st.write("### Seeding Verification")
                seed_rows = []
                for seed in sorted(seeds_dict.keys()):
                    team = seeds_dict[seed]
                    if format_type == "14-Team":
                        role = "Champion" if seed <= 3 else ("Wildcard" if seed == 4 else "At-Large")
                    else:
                        role = "Champion" if seed <= 2 else ("Wildcard" if seed == 3 else "At-Large")
                    seed_rows.append({"Seed": seed, "Team": team, "Role": role})

                st.dataframe(pd.DataFrame(seed_rows), hide_index=True, use_container_width=True)

                seed_lk = {v: k for k, v in seeds_dict.items()}

                if format_type == "14-Team":
                    wc_pairs = [(3, 14), (4, 13), (5, 12), (6, 11), (7, 10), (8, 9)]
                else:
                    wc_pairs = [(2, 13), (3, 12), (4, 11), (5, 10), (6, 9), (7, 8)]

                wc_games = [sim_game(seeds_dict[hi], seeds_dict[lo], "home", True, subs, "playoff_wc", "WC", "TBD", BASE_RATINGS, 1, forced_weather=forced_weather_setting()) for hi, lo in wc_pairs]
                wc_w = [r["winner"] for r in wc_games]

                div_games = []
                if format_type == "14-Team":
                    div_teams = [seeds_dict[1], seeds_dict[2]] + wc_w
                    div_srt = sorted(div_teams, key=lambda x: seed_lk[x])
                    for i in range(4):
                        div_games.append(sim_game(div_srt[i], div_srt[7-i], "home", True, subs, "playoff_div", "Div", "TBD", BASE_RATINGS, 1, forced_weather=forced_weather_setting()))
                else:
                    wc_srt = sorted(wc_w, key=lambda x: seed_lk[x])
                    for i in range(3):
                        div_games.append(sim_game(wc_srt[i], wc_srt[5-i], "home", True, subs, "playoff_div", "Div", "TBD", BASE_RATINGS, 1, forced_weather=forced_weather_setting()))

                div_w = [r["winner"] for r in div_games]
                if format_type == "13-Team":
                    div_w = [seeds_dict[1]] + div_w

                bc_srt = sorted(div_w, key=lambda x: seed_lk[x])
                bc_games = [sim_game(bc_srt[i[0]], bc_srt[i[1]], "home", True, subs, "playoff_bracket", "BC", "TBD", BASE_RATINGS, 1, forced_weather=forced_weather_setting()) for i in [(0, 3), (1, 2)]]
                bc_w = [r["winner"] for r in bc_games]

                sb = sim_game(bc_w[0], bc_w[1], "neutral", True, subs, "super_bowl", "SB", "Dec 23", BASE_RATINGS, 1, forced_weather=forced_weather_setting())
                sb_champ = sb["winner"]

                st.success(f"Super Bowl Champion: **{sb_champ}**")

                rounds = {
                    "Wild Card": wc_games,
                    "Divisional": div_games,
                    "Bracket Championship": bc_games,
                    "Super Bowl": [sb]
                }

                for rname, games in rounds.items():
                    st.write(f"**{rname}**")
                    game_rows = [{
                        "Away": g["away_team"], "Away Score": g["away_score"],
                        "Home": g["home_team"], "Home Score": g["home_score"],
                        "Winner": g["winner"]
                    } for g in games]
                    st.dataframe(pd.DataFrame(game_rows), hide_index=True, use_container_width=True)

                json_data = json.dumps({
                    "run_seed": run_seed,
                    "format": format_type,
                    "seeding": seed_rows,
                    "results": {k: v for k, v in rounds.items()}
                }, indent=2, default=str)
                st.download_button("⬇️ Download JSON Results", json_data, file_name=f"lct_playoffs_{run_seed}.json", mime="application/json")

    # ---------------- MODE 6 & 7 (SEASON RUNNER) ----------------
    elif mode_num in [6, 7]:
        seasons = 1 if mode_num == 6 else mode_params["num_seasons"]
        progress_bar = st.progress(0)
        status_text = st.empty()

        all_season_records = []
        season_champions = []
        stats = {t: {"LeagueWins": 0, "Top5Finishes": 0, "PlayoffApps": 0, "CupTitles": 0, "SOETitles": 0, "SBTitles": 0} for t in TEAMS}

        for season in range(1, seasons + 1):
            status_text.write(f"Simulating Season {season} of {seasons}...")
            season_seed = (run_seed + season) & MASK64
            subs = SubStreams(season_seed)

            master = load_or_build_master_schedule(TEAMS)
            defending_champ = season_champions[-1] if season > 1 else None
            schedule = build_league_season_schedule(
                master, season_num=season, defending_champ=defending_champ
            )
            schedule = assign_league_dates(schedule)

            results = []
            for g in schedule:
                results.append(sim_game(
                    g["home"], g["away"], g["venue"], False, subs,
                    "league", f"R{g['round']}", g["date"],
                    BASE_RATINGS, season,
                    forced_weather=forced_weather_setting()
                ))

            ranked, table = compute_league_table(schedule, results, subs.get("tiebreaks"))
            league_champ = ranked[0]
            stats[league_champ]["LeagueWins"] += 1
            for t in ranked[:5]: stats[t]["Top5Finishes"] += 1

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
            wc_games = [sim_game(seeds_dict[hi], seeds_dict[lo], "home", True, subs, "playoff_wc", "WC", "TBD", BASE_RATINGS, season, forced_weather=forced_weather_setting()) for hi, lo in wc_pairs]
            wc_w = [r["winner"] for r in wc_games]

            seed_lk = {v: k for k, v in seeds_dict.items()}
            div_games = []
            if format_type == "14-Team":
                div_teams = [seeds_dict[1], seeds_dict[2]] + wc_w
                div_srt = sorted(div_teams, key=lambda x: seed_lk[x])
                for i in range(4):
                    div_games.append(sim_game(div_srt[i], div_srt[7-i], "home", True, subs, "playoff_div", "Div", "TBD", BASE_RATINGS, season, forced_weather=forced_weather_setting()))
            else:
                wc_srt = sorted(wc_w, key=lambda x: seed_lk[x])
                for i in range(3):
                    div_games.append(sim_game(wc_srt[i], wc_srt[5-i], "home", True, subs, "playoff_div", "Div", "TBD", BASE_RATINGS, season, forced_weather=forced_weather_setting()))
            div_w = [r["winner"] for r in div_games]
            if format_type == "13-Team":
                div_w = [seeds_dict[1]] + div_w

            bc_srt = sorted(div_w, key=lambda x: seed_lk[x])
            bc_games = [sim_game(bc_srt[i[0]], bc_srt[i[1]], "home", True, subs, "playoff_bracket", "BC", "TBD", BASE_RATINGS, season, forced_weather=forced_weather_setting()) for i in [(0, 3), (1, 2)]]
            bc_w = [r["winner"] for r in bc_games]

            sb = sim_game(bc_w[0], bc_w[1], "neutral", True, subs, "super_bowl", "SB", "Dec 23", BASE_RATINGS, season, forced_weather=forced_weather_setting())
            sb_champ = sb["winner"]
            stats[sb_champ]["SBTitles"] += 1

            playoff_details = {
                "Format": format_type,
                "Wild Card": wc_games,
                "Divisional": div_games,
                "Bracket Championship": bc_games,
                "Super Bowl": [sb]
            }

            all_season_records.append({
                "Season": season, "Seed": season_seed,
                "League Champ": league_champ, "Cup Champ": cup_champ,
                "SOE Champ": soe_champ, "Super Bowl Champ": sb_champ,
                "Playoff Details": playoff_details
            })
            season_champions.append(sb_champ)
            progress_bar.progress(season / seasons)

        status_text.success("Simulation Complete!")

        st.subheader("Season Results")
        summary_rows = [{k: v for k, v in r.items() if k != "Playoff Details"} for r in all_season_records]
        st.dataframe(pd.DataFrame(summary_rows), hide_index=True, use_container_width=True)

        st.subheader("Playoff Results")
        for r in all_season_records:
            with st.expander(f"Season {r['Season']} Playoffs ({r['Playoff Details']['Format']})"):
                for rname, games in r["Playoff Details"].items():
                    if rname == "Format": continue
                    st.write(f"**{rname}**")
                    game_rows = [{
                        "Away": g["away_team"], "Away Score": g["away_score"],
                        "Home": g["home_team"], "Home Score": g["home_score"],
                        "Winner": g["winner"]
                    } for g in games]
                    st.dataframe(pd.DataFrame(game_rows), hide_index=True, use_container_width=True)

        if mode_num == 7:
            st.subheader("Franchise Totals")
            tally_rows = [{"Team": t, **s} for t, s in stats.items()]
            st.dataframe(pd.DataFrame(tally_rows), hide_index=True, use_container_width=True)

        json_data = json.dumps({"run_seed": run_seed, "results": summary_rows}, indent=2, default=str)
        st.download_button("⬇️ Download JSON Results", json_data, file_name=f"lct_results_{run_seed}.json", mime="application/json")
