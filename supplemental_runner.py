"""
Supplemental experiment runner — extends dataset to >10,000 NORMAL-phase samples per group.

Experiment plan:
  Group A (Edge-AI ON):   8 runs × 360s
  Group B (Baseline OFF): 8 runs × 360s
  Group D (AI ON + ICMP): 2 runs × 360s
  Total: 18 runs ≈ 111 minutes
"""

import time
import requests
import sys

BASE_URL = "http://localhost:8000"
RUN_DURATION_S = 360   # 6 minutes per run — longer window for NORMAL-phase coverage
PAUSE_BETWEEN_S = 10

MATRIX = (
    [{"run_label": f"A{i}", "edge_ai": True,  "real_measurement": False} for i in range(4, 12)] +  # A4–A11
    [{"run_label": f"B{i}", "edge_ai": False, "real_measurement": False} for i in range(4, 12)] +  # B4–B11
    [{"run_label": f"D{i}", "edge_ai": True,  "real_measurement": True}  for i in range(2, 4)]      # D2–D3
)

def wait_for_engine():
    for _ in range(30):
        try:
            r = requests.get(f"{BASE_URL}/run/status", timeout=5)
            if r.status_code == 200:
                data = r.json()
                if data.get("scan_complete"):
                    print(f"  Engine ready. Satellites scanned: {data.get('scan_total', '?')}")
                    return True
        except Exception:
            pass
        time.sleep(2)
    return False

def start_run(cfg):
    r = requests.post(f"{BASE_URL}/run/start", json=cfg, timeout=10)
    r.raise_for_status()
    return r.json()

def stop_run():
    r = requests.post(f"{BASE_URL}/run/stop", timeout=10)
    r.raise_for_status()
    return r.json()

def status():
    r = requests.get(f"{BASE_URL}/run/status", timeout=10)
    r.raise_for_status()
    return r.json()

def main():
    print("=" * 60)
    print("SDGS Supplemental Experiment Runner")
    print(f"  {len(MATRIX)} runs × {RUN_DURATION_S}s each")
    print(f"  Target: >10,000 NORMAL-phase samples per group")
    print("=" * 60)

    print("\nChecking engine readiness...")
    if not wait_for_engine():
        print("ERROR: Engine not ready after 60s. Exiting.")
        sys.exit(1)

    total_start = time.time()

    for idx, cfg in enumerate(MATRIX, 1):
        label = cfg["run_label"]
        mode  = "Edge-AI" if cfg["edge_ai"] else "Baseline"
        icmp  = " [+Pi ICMP]" if cfg["real_measurement"] else ""

        print(f"\n[{idx:02d}/{len(MATRIX)}] Starting {label} ({mode}{icmp})")
        try:
            resp = start_run(cfg)
            run_id = resp.get("run_id", "?")
            print(f"  run_id: {run_id}")
        except Exception as e:
            print(f"  ERROR starting run: {e}. Skipping.")
            continue

        # Progress bar
        deadline = time.time() + RUN_DURATION_S
        last_print = time.time()
        while time.time() < deadline:
            remaining = int(deadline - time.time())
            if time.time() - last_print >= 30:
                try:
                    st = status()
                    rows = st.get("rows", "?")
                    print(f"  [{remaining:3d}s left]  rows so far: {rows}")
                except Exception:
                    pass
                last_print = time.time()
            time.sleep(5)

        # Stop
        try:
            st = stop_run()
            print(f"  Run {label} COMPLETE — rows: {st.get('rows', '?')}")
        except Exception as e:
            print(f"  ERROR stopping run: {e}")

        # Pause between runs
        if idx < len(MATRIX):
            elapsed_total = int(time.time() - total_start)
            remaining_runs = len(MATRIX) - idx
            est_remaining = remaining_runs * (RUN_DURATION_S + PAUSE_BETWEEN_S)
            print(f"  Pause {PAUSE_BETWEEN_S}s | Elapsed: {elapsed_total//60}m{elapsed_total%60:02d}s"
                  f" | Est. remaining: {est_remaining//60}m")
            time.sleep(PAUSE_BETWEEN_S)

    elapsed = int(time.time() - total_start)
    print(f"\n{'='*60}")
    print(f"SUPPLEMENTAL RUNS COMPLETE")
    print(f"  Total elapsed: {elapsed//60}m{elapsed%60:02d}s")
    print(f"  Runs: {len(MATRIX)} × {RUN_DURATION_S}s")
    print(f"  Next: run  python3 post_process.py")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
