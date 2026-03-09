#!/usr/bin/env python3
"""
Multi-Station Experiment Runner
================================
Runs the full 7-experiment matrix (A1-A3, B1-B3, D1) for each configured
ground station, writing logs to station-specific subdirectories.

Workflow per station:
  1. Kill any existing engine on the port
  2. Start engine with station lat/lon/name/log-dir
  3. Wait for TLE scan to complete (up to 60s)
  4. Run 7 experiments × RUN_DURATION_S each
  5. Stop engine
  6. Move to next station

Usage:
  python3 multi_station_runner.py
  python3 multi_station_runner.py --stations beijing la --duration 180
  python3 multi_station_runner.py --check-windows-only
"""

import argparse
import os
import pathlib
import signal
import subprocess
import sys
import time

import requests

# ── Station definitions ─────────────────────────────────────────────────────
STATION_REGISTRY = {
    "shenzhen": {
        "name":    "Shenzhen",
        "lat":     22.54,
        "lon":     114.05,
        "alt":     20,
        "log_dir": "logs/shenzhen",
        "port":    8000,
        "tz_label":"UTC+8",
    },
    "beijing": {
        "name":    "Beijing",
        "lat":     39.90,
        "lon":     116.40,
        "alt":     43,
        "log_dir": "logs/beijing",
        "port":    8000,   # sequential — reuse port after restart
        "tz_label":"UTC+8",
    },
    "la": {
        "name":    "Los Angeles",
        "lat":     34.05,
        "lon":     -118.25,
        "alt":     71,
        "log_dir": "logs/la",
        "port":    8000,
        "tz_label":"UTC-7",
    },
}

# ── Experiment matrix ────────────────────────────────────────────────────────
EXPERIMENT_MATRIX = [
    {"run_label": "A1", "edge_ai": True,  "real_measurement": False},
    {"run_label": "A2", "edge_ai": True,  "real_measurement": False},
    {"run_label": "A3", "edge_ai": True,  "real_measurement": False},
    {"run_label": "B1", "edge_ai": False, "real_measurement": False},
    {"run_label": "B2", "edge_ai": False, "real_measurement": False},
    {"run_label": "B3", "edge_ai": False, "real_measurement": False},
    {"run_label": "D1", "edge_ai": True,  "real_measurement": True},
]

PAUSE_BETWEEN_RUNS_S = 10
ENGINE_SCRIPT        = "sdgs_web_engine.py"
ENGINE_START_TIMEOUT = 75   # seconds — TLE scan can take ~30–60s


# ── Engine process management ────────────────────────────────────────────────
def kill_engine(port: int):
    """Kill any process currently listening on the given port."""
    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}"],
            capture_output=True, text=True
        )
        pids = result.stdout.strip().split()
        for pid in pids:
            try:
                os.kill(int(pid), signal.SIGTERM)
                print(f"  [mgr] Killed PID {pid} on port {port}")
            except Exception:
                pass
        if pids:
            time.sleep(3)
    except Exception as e:
        print(f"  [mgr] Warning: kill_engine failed: {e}")


def start_engine(station: dict) -> subprocess.Popen:
    """Launch engine subprocess for a specific station."""
    log_dir = pathlib.Path(station["log_dir"])
    log_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, ENGINE_SCRIPT,
        "--lat",          str(station["lat"]),
        "--lon",          str(station["lon"]),
        "--alt",          str(station["alt"]),
        "--station-name", station["name"],
        "--port",         str(station["port"]),
        "--log-dir",      str(log_dir),
    ]
    engine_log = open(f"engine_{station['name'].lower().replace(' ','_')}.log",
                      "w", buffering=1)
    proc = subprocess.Popen(cmd, stdout=engine_log, stderr=engine_log)
    print(f"  [mgr] Engine started (PID {proc.pid}) → {station['name']} "
          f"lat={station['lat']} lon={station['lon']}  log→{log_dir}")
    return proc


def wait_for_engine_ready(port: int, timeout: int = ENGINE_START_TIMEOUT) -> bool:
    """Poll /run/status until scan_complete == True."""
    base = f"http://localhost:{port}"
    deadline = time.time() + timeout
    print(f"  [mgr] Waiting for engine on port {port} (timeout={timeout}s)...",
          end="", flush=True)
    while time.time() < deadline:
        try:
            r = requests.get(f"{base}/run/status", timeout=3)
            if r.status_code == 200 and r.json().get("scan_complete"):
                print(f" ready! (satellites={r.json().get('scan_total','?')})")
                return True
        except Exception:
            pass
        time.sleep(3)
        print(".", end="", flush=True)
    print(" TIMEOUT")
    return False


# ── Run control ──────────────────────────────────────────────────────────────
def run_experiment(port: int, cfg: dict, duration: int) -> dict:
    base = f"http://localhost:{port}"
    label = cfg["run_label"]

    # Start
    try:
        r = requests.post(f"{base}/run/start", json=cfg, timeout=10)
        r.raise_for_status()
        run_id = r.json().get("run_id", "?")
        print(f"    ▶ {label} started  run_id={run_id}")
    except Exception as e:
        print(f"    ✗ {label} failed to start: {e}")
        return {"label": label, "status": "error"}

    # Wait with progress report
    deadline = time.time() + duration
    last_print = time.time()
    while time.time() < deadline:
        remaining = int(deadline - time.time())
        if time.time() - last_print >= 30:
            try:
                st = requests.get(f"{base}/run/status", timeout=5).json()
                phase = st.get("run_label", "?")
                rows  = st.get("rows", "?")
                print(f"      [{remaining:3d}s left]  rows={rows}")
            except Exception:
                pass
            last_print = time.time()
        time.sleep(5)

    # Stop
    try:
        st = requests.post(f"{base}/run/stop", timeout=10).json()
        rows = st.get("rows", "?")
        print(f"    ■ {label} stopped   rows={rows}")
        return {"label": label, "status": "ok", "rows": rows}
    except Exception as e:
        print(f"    ✗ {label} stop error: {e}")
        return {"label": label, "status": "stop_error"}


# ── Main orchestrator ─────────────────────────────────────────────────────────
def run_station(station: dict, duration: int) -> list:
    name = station["name"]
    port = station["port"]

    print(f"\n{'═'*68}")
    print(f"  STATION: {name}  ({station['lat']}°N, {station['lon']}°E)")
    print(f"  Log dir: {station['log_dir']}   Port: {port}")
    print(f"{'═'*68}")

    # Kill any existing engine
    kill_engine(port)

    # Start engine for this station
    proc = start_engine(station)

    # Wait for ready
    if not wait_for_engine_ready(port):
        print(f"  [mgr] Engine for {name} failed to start. Skipping.")
        proc.terminate()
        return []

    results = []
    total_start = time.time()

    for idx, cfg in enumerate(EXPERIMENT_MATRIX, 1):
        label = cfg["run_label"]
        mode  = "Edge-AI" if cfg["edge_ai"] else "Baseline"
        icmp  = " [+ICMP]" if cfg["real_measurement"] else ""
        print(f"\n  [{idx}/{len(EXPERIMENT_MATRIX)}] {label} ({mode}{icmp})")

        result = run_experiment(port, cfg, duration)
        results.append(result)

        if idx < len(EXPERIMENT_MATRIX):
            elapsed = int(time.time() - total_start)
            remaining_runs = len(EXPERIMENT_MATRIX) - idx
            est_left = remaining_runs * (duration + PAUSE_BETWEEN_RUNS_S)
            print(f"      Pause {PAUSE_BETWEEN_RUNS_S}s | "
                  f"elapsed={elapsed//60}m{elapsed%60:02d}s | "
                  f"est_left≈{est_left//60}m")
            time.sleep(PAUSE_BETWEEN_RUNS_S)

    elapsed = int(time.time() - total_start)
    ok_count = sum(1 for r in results if r.get("status") == "ok")
    print(f"\n  Station {name} complete: {ok_count}/{len(results)} runs OK  "
          f"({elapsed//60}m{elapsed%60:02d}s)")

    # Gracefully stop engine
    proc.terminate()
    time.sleep(2)
    return results


def main():
    parser = argparse.ArgumentParser(description="Multi-Station Experiment Runner")
    parser.add_argument(
        "--stations", nargs="+",
        default=["beijing", "la"],
        choices=list(STATION_REGISTRY.keys()),
        help="Stations to collect (shenzhen already done; default: beijing la)"
    )
    parser.add_argument(
        "--duration", type=int, default=180,
        help="Seconds per run (default 180)"
    )
    parser.add_argument(
        "--check-windows-only", action="store_true",
        help="Only run orbital window check, don't collect data"
    )
    args = parser.parse_args()

    if args.check_windows_only:
        print("Running orbital window pre-check...")
        os.system(f"{sys.executable} check_orbital_window.py --hours 6")
        return

    print("=" * 68)
    print("  SDGS Multi-Station Experiment Runner")
    print(f"  Stations: {', '.join(args.stations)}")
    print(f"  Duration per run: {args.duration}s")
    print(f"  Experiments per station: {len(EXPERIMENT_MATRIX)}")
    est_total = len(args.stations) * len(EXPERIMENT_MATRIX) * (args.duration + PAUSE_BETWEEN_RUNS_S)
    print(f"  Estimated total time: {est_total // 60}m")
    print("=" * 68)

    # Orbital window pre-check
    print("\n[Pre-check] Scanning orbital windows for selected stations...")
    check_cmd = (f"{sys.executable} check_orbital_window.py "
                 f"--hours 3 --min-stable 5")
    os.system(check_cmd)
    print("\n[Pre-check] Proceeding in 5s (Ctrl+C to abort)...")
    time.sleep(5)

    # Kill any running engine first
    kill_engine(8000)
    time.sleep(2)

    all_results = {}
    master_start = time.time()

    for station_key in args.stations:
        station = STATION_REGISTRY[station_key]
        results = run_station(station, args.duration)
        all_results[station_key] = results

    master_elapsed = int(time.time() - master_start)

    print(f"\n{'═'*68}")
    print("  MULTI-STATION COLLECTION COMPLETE")
    print(f"  Total elapsed: {master_elapsed//60}m{master_elapsed%60:02d}s")
    print(f"{'═'*68}")
    for key, results in all_results.items():
        st = STATION_REGISTRY[key]
        ok = sum(1 for r in results if r.get("status") == "ok")
        print(f"  {st['name']:15s}: {ok}/{len(results)} runs OK → {st['log_dir']}")

    print("\n  Next step:")
    print("    python3 post_process.py --multi-station")
    print("=" * 68)

    # Restore Shenzhen engine
    print("\n[mgr] Restoring default Shenzhen engine on port 8000...")
    shenzhen = STATION_REGISTRY["shenzhen"]
    proc = start_engine(shenzhen)
    if wait_for_engine_ready(8000, timeout=75):
        print("[mgr] Shenzhen engine restored. Dashboard available at http://localhost:8000")
    else:
        print("[mgr] Warning: Shenzhen engine may not have started correctly.")


if __name__ == "__main__":
    main()
