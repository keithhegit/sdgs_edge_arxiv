#!/usr/bin/env python3
"""
Orbital Window Pre-Checker
===========================
Uses Skyfield + Starlink TLEs to scan the next N hours for each ground station
and report windows where stable NORMAL-phase data collection is feasible.

A "good window" requires:
  - At least 1 satellite above MIN_ELEVATION_DEG for MIN_STABLE_MINUTES consecutive minutes
  - (Proxy for: handover state machine can settle into NORMAL phase)

Usage:
  python3 check_orbital_window.py
  python3 check_orbital_window.py --hours 6 --min-elev 25 --min-stable 5

Output:
  Per-station table of good windows in the next N hours.
  Recommends the next available collection slot.
"""

import argparse
import math
from datetime import datetime, timezone, timedelta

from skyfield.api import load, wgs84, EarthSatellite

# ── Ground stations to check ───────────────────────────────────────────────
STATIONS = [
    {"name": "Shenzhen",    "lat": 22.54,  "lon": 114.05,  "alt": 20},
    {"name": "Beijing",     "lat": 39.90,  "lon": 116.40,  "alt": 43},
    {"name": "Tokyo",       "lat": 35.69,  "lon": 139.69,  "alt": 40},
    {"name": "Los Angeles", "lat": 34.05,  "lon": -118.25, "alt": 71},
]

# ── Parameters ─────────────────────────────────────────────────────────────
TLE_FILE        = "starlink.tle"
MIN_ELEVATION   = 25.0   # degrees
MIN_STABLE_SECS = 300    # 5 minutes of stable >25° coverage = 1 "good" window
SCAN_STEP_SEC   = 30     # resolution of the scan
MAX_SATS_SCAN   = 500    # use top-N by inclination proximity (speed optimisation)

def load_sats(tle_file: str) -> list:
    ts = load.timescale()
    try:
        sats = load.tle_file(tle_file)
        print(f"[check] Loaded {len(sats)} satellites from {tle_file}")
        return sats, ts
    except Exception as e:
        print(f"[check] ERROR loading TLE: {e}")
        return [], None

def count_visible(sats, gs_pos, t) -> int:
    """Count satellites above MIN_ELEVATION at time t."""
    count = 0
    for sat in sats:
        try:
            diff = sat - gs_pos
            alt, _, _ = diff.at(t).altaz()
            if alt.degrees >= MIN_ELEVATION:
                count += 1
        except Exception:
            pass
    return count

def scan_station(station: dict, sats: list, ts, now_utc: datetime,
                 hours: int, step_sec: int) -> list:
    """
    Returns list of windows: {"start": datetime, "end": datetime,
                               "duration_min": float, "max_visible": int}
    """
    gs_pos = wgs84.latlon(station["lat"], station["lon"],
                          elevation_m=station["alt"])

    windows = []
    in_window = False
    window_start = None
    max_vis = 0
    t_cursor = now_utc

    total_steps = int(hours * 3600 / step_sec)
    print(f"  [{station['name']}] Scanning {total_steps} steps × {step_sec}s "
          f"({hours}h ahead)...", flush=True)

    for i in range(total_steps):
        t_sf = ts.from_datetime(t_cursor.replace(tzinfo=timezone.utc))
        vis = count_visible(sats, gs_pos, t_sf)

        if vis > 0:
            if not in_window:
                in_window    = True
                window_start = t_cursor
                max_vis      = vis
            else:
                max_vis = max(max_vis, vis)
        else:
            if in_window:
                duration = (t_cursor - window_start).total_seconds()
                if duration >= MIN_STABLE_SECS:
                    windows.append({
                        "start":        window_start,
                        "end":          t_cursor,
                        "duration_min": round(duration / 60, 1),
                        "max_visible":  max_vis,
                    })
                in_window = False

        t_cursor += timedelta(seconds=step_sec)

    # close any open window
    if in_window:
        duration = (t_cursor - window_start).total_seconds()
        if duration >= MIN_STABLE_SECS:
            windows.append({
                "start":        window_start,
                "end":          t_cursor,
                "duration_min": round(duration / 60, 1),
                "max_visible":  max_vis,
            })

    return windows

def local_time(utc_dt: datetime, offset_hours: float) -> str:
    local = utc_dt + timedelta(hours=offset_hours)
    return local.strftime("%H:%M")

STATION_UTC_OFFSET = {
    "Shenzhen":    8,
    "Beijing":     8,
    "Tokyo":       9,
    "Los Angeles": -7,   # PDT (UTC-7) in March
}

def main():
    global MIN_ELEVATION, MIN_STABLE_SECS, SCAN_STEP_SEC

    parser = argparse.ArgumentParser()
    parser.add_argument("--hours",      type=float, default=4.0)
    parser.add_argument("--min-elev",   type=float, default=MIN_ELEVATION)
    parser.add_argument("--min-stable", type=float, default=MIN_STABLE_SECS/60,
                        help="Minimum stable coverage (minutes)")
    parser.add_argument("--step",       type=int,   default=SCAN_STEP_SEC)
    args = parser.parse_args()

    MIN_ELEVATION   = args.min_elev
    MIN_STABLE_SECS = args.min_stable * 60
    SCAN_STEP_SEC   = args.step

    sats, ts = load_sats(TLE_FILE)
    if not sats:
        print("Cannot load TLEs — is starlink.tle present?")
        return

    # Use a representative subset for speed (first MAX_SATS_SCAN)
    sats_subset = sats[:MAX_SATS_SCAN]
    print(f"[check] Using {len(sats_subset)} satellites for scan "
          f"(of {len(sats)} total)\n")

    now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
    print(f"[check] Scan start (UTC): {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[check] Scan window: {args.hours}h ahead\n")
    print("=" * 72)

    all_windows = {}
    for station in STATIONS:
        tz_off = STATION_UTC_OFFSET.get(station["name"], 0)
        windows = scan_station(station, sats_subset, ts, now_utc,
                               args.hours, SCAN_STEP_SEC)
        all_windows[station["name"]] = windows

        local_now = now_utc + timedelta(hours=tz_off)
        tz_label  = f"UTC{tz_off:+d}"

        print(f"\n{'─'*72}")
        print(f"  {station['name']:15s} "
              f"({station['lat']:.1f}°N, {station['lon']:.1f}°E)  "
              f"Local now: {local_now.strftime('%H:%M')} {tz_label}")
        print(f"{'─'*72}")

        if not windows:
            print(f"  ⚠  No stable windows found in next {args.hours:.0f}h — "
                  f"try scheduling outside this period.")
        else:
            for w in windows:
                local_s = local_time(w["start"], tz_off)
                local_e = local_time(w["end"],   tz_off)
                utc_s   = w["start"].strftime("%H:%M")
                utc_e   = w["end"].strftime("%H:%M")
                qual    = "✅ EXCELLENT" if w["max_visible"] >= 3 else \
                          "✅ GOOD"      if w["max_visible"] >= 2 else \
                          "⚡ MARGINAL"
                print(f"  {qual:14s}  "
                      f"UTC {utc_s}–{utc_e}  "
                      f"Local {local_s}–{local_e}  "
                      f"dur={w['duration_min']}min  "
                      f"max_vis={w['max_visible']}")

    # Cross-station recommendation
    print(f"\n{'='*72}")
    print("  RECOMMENDATION — Next available collection slot per station")
    print(f"{'='*72}")
    for station in STATIONS:
        name     = station["name"]
        tz_off   = STATION_UTC_OFFSET.get(name, 0)
        tz_label = f"UTC{tz_off:+d}"
        wins     = all_windows.get(name, [])
        if wins:
            best = max(wins, key=lambda w: w["duration_min"])
            ls   = local_time(best["start"], tz_off)
            le   = local_time(best["end"],   tz_off)
            us   = best["start"].strftime("%H:%M")
            ue   = best["end"].strftime("%H:%M")
            print(f"  {name:15s}: best window  UTC {us}–{ue}  "
                  f"({tz_label} {ls}–{le})  "
                  f"{best['duration_min']}min  "
                  f"(max {best['max_visible']} sats visible)")
        else:
            print(f"  {name:15s}: ⚠ no window in scan range")

    print(f"\n[check] Tip: Run experiment_runner.py within a ✅ window above.")
    print(f"[check] Suggested run duration: 180s; each 7-run set ≈ 24 min.\n")

if __name__ == "__main__":
    main()
