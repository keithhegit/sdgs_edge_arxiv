#!/usr/bin/env python3
"""
SDGS HIL Post-Processor
========================
读取 logs/ 目录下的所有 run 遥测 CSV，生成：

  dataset/appendix_table1.csv   — Throughput / Latency / Handover (mean±std)
  dataset/appendix_table2.csv   — Residual TA / CFO (P50/P95/P99)
  dataset/appendix_fidelity.csv — Sim vs Real Pi 注入保真度 (D1 run)
  dataset/appendix_summary.md   — Markdown 可读汇总（直接粘贴到论文 Appendix）
  dataset/README_dataset.md     — 数据集说明文档

用法:
  python3 post_process.py [--log-dir logs] [--out-dir dataset]
"""

import argparse
import csv
import json
import math
import pathlib
import sys
from collections import defaultdict


# ── 统计工具 ──────────────────────────────────────────────────────
def percentile(data: list, p: float) -> float:
    if not data:
        return float("nan")
    s = sorted(data)
    idx = (len(s) - 1) * p / 100
    lo, hi = int(idx), min(int(idx) + 1, len(s) - 1)
    frac = idx - lo
    return s[lo] + frac * (s[hi] - s[lo])


def mean(data: list) -> float:
    return sum(data) / len(data) if data else float("nan")


def std(data: list) -> float:
    if len(data) < 2:
        return float("nan")
    m = mean(data)
    return math.sqrt(sum((x - m) ** 2 for x in data) / (len(data) - 1))


def fmt(v, decimals=2) -> str:
    if math.isnan(v):
        return "N/A"
    return f"{v:.{decimals}f}"


# ── 加载所有 telemetry CSV ──────────────────────────────────────────
def load_runs(log_dir: pathlib.Path) -> dict[str, list[dict]]:
    """
    返回 {run_id: [row, ...]}
    每 row 是 CSV 行 dict，数值字段已转为 float。
    """
    NUM_FIELDS = {
        "elapsed_s", "elevation_deg", "slant_range_km",
        "sat_lat", "sat_lon", "propagation_one_way_ms",
        "rtt_model_ms", "jitter_ms", "loss_pct", "throughput_mbps",
        "residual_ta_us", "residual_cfo_hz", "edge_ai_enabled",
    }
    runs = {}
    for run_dir in sorted(log_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        csv_path = run_dir / "telemetry.csv"
        if not csv_path.exists():
            continue
        rows = []
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                for field in NUM_FIELDS:
                    if field in row and row[field] not in ("", None):
                        try:
                            row[field] = float(row[field])
                        except ValueError:
                            row[field] = float("nan")
                rows.append(row)
        if rows:
            runs[run_dir.name] = rows
            print(f"[PostProc] Loaded {run_dir.name}: {len(rows)} rows")
    return runs


# ── 从事件日志中统计越区切换 ──────────────────────────────────────
def count_handovers(log_dir: pathlib.Path, run_id: str) -> dict:
    events_path = log_dir / run_id / "events.jsonl"
    if not events_path.exists():
        return {"attempted": 0, "successful": 0, "success_rate_pct": float("nan")}

    attempted = 0
    successful = 0
    switching_ts = None

    with open(events_path) as f:
        for line in f:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("event") == "HANDOVER_PHASE_CHANGE":
                if ev.get("to_phase") == "SWITCHING":
                    attempted += 1
                    switching_ts = ev.get("elapsed_s", 0)
                elif ev.get("to_phase") == "NORMAL" and switching_ts is not None:
                    successful += 1
                    switching_ts = None

    rate = (successful / attempted * 100) if attempted > 0 else float("nan")
    return {
        "attempted":        attempted,
        "successful":       successful,
        "success_rate_pct": rate,
    }


# ── 主分析函数 ──────────────────────────────────────────────────────
def analyse(runs: dict, log_dir: pathlib.Path) -> dict:
    """
    按 run_label 分组（A=Edge-AI ON, B=Baseline, D=Fidelity）。
    返回结构化分析结果。
    """
    groups = defaultdict(lambda: {
        "throughput": [], "latency": [], "tail_latency_95": [],
        "ta_closed": [], "cfo_closed": [],
        "ta_open": [],   "cfo_open": [],
        "handover_rates": [],
    })
    fidelity_rows = []

    for run_id, rows in runs.items():
        # 从 meta.json 取 run_label
        meta_path = log_dir / run_id / "meta.json"
        run_label = run_id.split("_")[-1] if "_" in run_id else run_id
        if meta_path.exists():
            with open(meta_path) as f:
                meta = json.load(f)
            run_label = meta.get("run_label", run_label)

        group = run_label[0].upper()   # "A", "B", "D"

        # 过滤有效时段（排除越区切换的 warning/danger 噪声点对于 baseline 无效）
        valid_rows = [r for r in rows
                      if r.get("handover_phase") == "NORMAL"]
        if not valid_rows:
            valid_rows = rows   # fallback

        tput  = [r["throughput_mbps"] for r in valid_rows
                 if not math.isnan(r.get("throughput_mbps", float("nan")))]
        rtt   = [r["rtt_model_ms"] for r in valid_rows
                 if not math.isnan(r.get("rtt_model_ms", float("nan")))]

        # Edge-AI ON → closed-loop TA/CFO
        ai_on_rows  = [r for r in valid_rows if r.get("edge_ai_enabled") == 1]
        ai_off_rows = [r for r in valid_rows if r.get("edge_ai_enabled") == 0]

        ta_cl  = [r["residual_ta_us"]  for r in ai_on_rows
                  if not math.isnan(r.get("residual_ta_us", float("nan")))]
        cfo_cl = [r["residual_cfo_hz"] for r in ai_on_rows
                  if not math.isnan(r.get("residual_cfo_hz", float("nan")))]
        ta_ol  = [r["residual_ta_us"]  for r in ai_off_rows
                  if not math.isnan(r.get("residual_ta_us", float("nan")))]
        cfo_ol = [r["residual_cfo_hz"] for r in ai_off_rows
                  if not math.isnan(r.get("residual_cfo_hz", float("nan")))]

        g = groups[group]
        if tput:
            g["throughput"].append(mean(tput))
        if rtt:
            g["latency"].append(mean(rtt))
            g["tail_latency_95"].append(percentile(rtt, 95))
        if ta_cl:
            g["ta_closed"].extend(ta_cl)
        if cfo_cl:
            g["cfo_closed"].extend(cfo_cl)
        if ta_ol:
            g["ta_open"].extend(ta_ol)
        if cfo_ol:
            g["cfo_open"].extend(cfo_ol)

        # 越区切换统计
        hw = count_handovers(log_dir, run_id)
        if not math.isnan(hw["success_rate_pct"]):
            g["handover_rates"].append(hw["success_rate_pct"])

        # D 组：收集 fidelity 数据（模型值 vs Pi Ping 实测值）
        if group == "D":
            for r in rows:
                ping_delay = r.get("ping_real_delay_ms", "")
                if ping_delay not in ("", None):
                    try:
                        pd = float(ping_delay)
                        fidelity_rows.append({
                            "elapsed_s":      r["elapsed_s"],
                            "model_delay_ms": r["rtt_model_ms"],
                            "ping_delay_ms":  pd,
                            "model_loss_pct": r["loss_pct"],
                            "ping_loss_pct":  (float(r["ping_real_loss_pct"])
                                               if r.get("ping_real_loss_pct") not in ("", None)
                                               else float("nan")),
                            "model_jitter_ms": r["jitter_ms"],
                            "ping_jitter_ms": (float(r["ping_real_jitter_ms"])
                                               if r.get("ping_real_jitter_ms") not in ("", None)
                                               else float("nan")),
                            "ping_via":       r.get("ping_via", ""),
                        })
                    except (ValueError, TypeError):
                        pass

    return {"groups": groups, "fidelity": fidelity_rows}


# ── 输出函数 ──────────────────────────────────────────────────────
def write_table1(result: dict, out_dir: pathlib.Path):
    """Throughput / Latency / Handover mean±std 汇总表。"""
    groups = result["groups"]

    def g_stat(grp, key):
        data = groups[grp][key]
        if not data:
            return "N/A", "N/A"
        return fmt(mean(data)), fmt(std(data))

    rows = [
        ["Metric", "Edge-AI (A)", "Baseline (B)", "Improvement"],
    ]

    # Throughput
    am, as_ = g_stat("A", "throughput")
    bm, bs  = g_stat("B", "throughput")
    try:
        imp = (float(am) - float(bm)) / float(bm) * 100
        imp_s = f"+{imp:.1f}%"
    except Exception:
        imp_s = "N/A"
    rows.append([
        "Aggregate Throughput (Mbps)",
        f"{am} ± {as_}", f"{bm} ± {bs}", imp_s,
    ])

    # Mean Latency
    am, as_ = g_stat("A", "latency")
    bm, bs  = g_stat("B", "latency")
    try:
        imp = (float(bm) - float(am)) / float(bm) * 100
        imp_s = f"-{imp:.1f}%"
    except Exception:
        imp_s = "N/A"
    rows.append([
        "Mean Latency (ms)",
        f"{am} ± {as_}", f"{bm} ± {bs}", imp_s,
    ])

    # 95th-pct Latency
    am, as_ = g_stat("A", "tail_latency_95")
    bm, bs  = g_stat("B", "tail_latency_95")
    rows.append([
        "95th-Pct Latency (ms)",
        f"{am} ± {as_}", f"{bm} ± {bs}", "—",
    ])

    # Handover Success Rate
    am, as_ = g_stat("A", "handover_rates")
    bm, bs  = g_stat("B", "handover_rates")
    rows.append([
        "Handover Success Rate (%)",
        f"{am} ± {as_}" if am != "N/A" else "N/A (no handovers in window)",
        f"{bm} ± {bs}"  if bm != "N/A" else "N/A",
        "—",
    ])

    path = out_dir / "appendix_table1.csv"
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)
    print(f"[PostProc] → {path}")
    return rows


def write_table2(result: dict, out_dir: pathlib.Path):
    """Residual TA / CFO distribution P50/P95/P99."""
    groups = result["groups"]

    def prow(label, data):
        if not data:
            return [label, "N/A", "N/A", "N/A"]
        return [
            label,
            fmt(percentile(data, 50), 2),
            fmt(percentile(data, 95), 2),
            fmt(percentile(data, 99), 2),
        ]

    rows = [
        ["Metric", "Median (P50)", "95th Pct (P95)", "99th Pct (P99)"],
        prow("Residual TA  open-loop  (µs)",  groups["B"]["ta_open"]),
        prow("Residual TA  closed-loop (µs)", groups["A"]["ta_closed"]),
        prow("Residual CFO open-loop  (Hz)",  groups["B"]["cfo_open"]),
        prow("Residual CFO closed-loop (Hz)", groups["A"]["cfo_closed"]),
    ]

    path = out_dir / "appendix_table2.csv"
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)
    print(f"[PostProc] → {path}")
    return rows


def write_fidelity(result: dict, out_dir: pathlib.Path):
    """D1 run: Model-Driven vs Pi Ping 保真度对比。"""
    fid = result["fidelity"]
    if not fid:
        print("[PostProc] No fidelity data (D1 run not found or no ping data)")
        return []

    model_delays  = [r["model_delay_ms"] for r in fid]
    ping_delays   = [r["ping_delay_ms"]  for r in fid
                     if not math.isnan(r["ping_delay_ms"])]
    model_losses  = [r["model_loss_pct"] for r in fid]
    ping_losses   = [r["ping_loss_pct"]  for r in fid
                     if not math.isnan(r["ping_loss_pct"])]
    model_jitters = [r["model_jitter_ms"] for r in fid]
    ping_jitters  = [r["ping_jitter_ms"]  for r in fid
                     if not math.isnan(r["ping_jitter_ms"])]

    def dev(model_mean, meas_mean):
        if math.isnan(model_mean) or math.isnan(meas_mean) or model_mean == 0:
            return "N/A"
        return f"{(meas_mean - model_mean) / model_mean * 100:+.1f}%"

    mm = mean(model_delays)  if model_delays  else float("nan")
    pm = mean(ping_delays)   if ping_delays   else float("nan")
    ml = mean(model_losses)  if model_losses  else float("nan")
    pl = mean(ping_losses)   if ping_losses   else float("nan")
    mj = mean(model_jitters) if model_jitters else float("nan")
    pj = mean(ping_jitters)  if ping_jitters  else float("nan")

    rows = [
        ["Metric", "Model-Driven (Engine)", "Measured (Pi Ping)", "Deviation"],
        ["One-way Delay (ms)",
         f"{fmt(mm)} ± {fmt(std(model_delays))}",
         f"{fmt(pm)} ± {fmt(std(ping_delays))}",
         dev(mm, pm)],
        ["Packet Loss (%)",
         f"{fmt(ml,3)} ± {fmt(std(model_losses),3)}",
         f"{fmt(pl,1)} ± {fmt(std(ping_losses),1)}",
         dev(ml, pl)],
        ["Jitter / mdev (ms)",
         f"{fmt(mj)} ± {fmt(std(model_jitters))}",
         f"{fmt(pj)} ± {fmt(std(ping_jitters))}",
         dev(mj, pj)],
    ]

    path = out_dir / "appendix_fidelity.csv"
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)
    print(f"[PostProc] → {path}")
    return rows


def write_markdown(t1, t2, tf, out_dir: pathlib.Path,
                   run_count: int, total_rows: int):
    """生成 Appendix Markdown 汇总，可直接附在论文中。"""

    def table_md(rows):
        if not rows:
            return "_No data available._\n"
        lines = []
        header = rows[0]
        lines.append("| " + " | ".join(str(c) for c in header) + " |")
        lines.append("|" + "|".join(["---"] * len(header)) + "|")
        for row in rows[1:]:
            lines.append("| " + " | ".join(str(c) for c in row) + " |")
        return "\n".join(lines) + "\n"

    md = f"""# Appendix: Hardware-in-the-Loop Experimental Dataset

## Overview

This appendix presents measurement traces collected from a Hardware-in-the-Loop (HIL)
testbed that validates the edge intelligence framework proposed in the main paper.
The testbed comprises an Ubuntu cloud server running the orbital computation and
Edge-AI PID optimization engine, connected via WireGuard VPN tunnels to Raspberry Pi
nodes that emulate satellite link characteristics using Linux `tc netem`. Real Starlink
TLE ephemeris from CelesTrak drives the orbital model in real time at 10 Hz.

**Methodology Note:** The testbed implements a *cross-layer penalty mapping* methodology.
Residual TA and CFO values are computed by the Skyfield-driven orbital engine and the
Edge-AI PID controller state. When residuals exceed NR CP/SCS thresholds
(TA > 2.34 µs or CFO > 90 Hz), the engine injects corresponding tc netem penalties
into the WireGuard tunnel. End-to-end ICMP probes on physical Raspberry Pi hardware
provide independent measurements of the injected link characteristics (Table 3).

**Dataset:** {run_count} runs collected, {total_rows:,} total 10-Hz telemetry ticks.
Run labels: A1–A3 (Edge-AI ON), B1–B3 (Baseline/AI-OFF), D1 (Fidelity check with
real Pi ICMP measurement).

---

## Table 1: Aggregate Performance (Mean ± Std, across 3 runs per group)

{table_md(t1)}
> *Improvement* is computed relative to the Baseline group.
> Only NORMAL-phase ticks (excluding active handover windows) are included in
> throughput and latency statistics to ensure steady-state comparison.

---

## Table 2: Residual TA / CFO Distribution

{table_md(t2)}
> *Open-loop* = Edge-AI OFF (Baseline group, B1–B3), reflecting the uncompensated
> residual from orbital geometry alone. *Closed-loop* = Edge-AI ON (group A1–A3),
> after PID feedback. NR 30 kHz SCS CP tolerance: 2.34 µs; CFO tolerance: 90 Hz.

---

## Table 3: tc netem Injection Fidelity (D1 Run)

{table_md(tf) if tf else '_D1 run data not available._'}
> Independent ICMP (ping) probes from Raspberry Pi measure the actual injected
> link characteristics, providing ground-truth validation of the simulation pipeline.
> Deviation > 0 indicates real network jitter exceeds the model (expected behavior).

---

## Dataset Schema

Each run directory under `logs/` contains:

| File | Description |
|------|-------------|
| `meta.json` | Run metadata (ground station, TLE count, PID params, model constants) |
| `telemetry.csv` | 10 Hz telemetry: orbital geometry, link model, TA/CFO, ping measurements |
| `events.jsonl` | Discrete events: handover phase transitions, AI toggle, run start/stop |

Key `telemetry.csv` columns:

| Column | Unit | Description |
|--------|------|-------------|
| `elapsed_s` | s | Seconds since run start |
| `elevation_deg` | ° | Satellite elevation above horizon |
| `slant_range_km` | km | GS-to-satellite slant range |
| `rtt_model_ms` | ms | Model-computed RTT (propagation + overhead) |
| `jitter_ms` | ms | Modeled RTT variance |
| `loss_pct` | % | Modeled packet loss rate |
| `throughput_mbps` | Mbps | Estimated TCP BBR goodput |
| `residual_ta_us` | µs | Residual Timing Advance after compensation |
| `residual_cfo_hz` | Hz | Residual Carrier Frequency Offset |
| `edge_ai_enabled` | 0/1 | Edge-AI PID controller state |
| `handover_phase` | str | NORMAL / PRE_WARN / PRE_WARM / SWITCHING / CLEANUP |
| `ping_real_delay_ms` | ms | Measured one-way delay via ICMP (D1 only) |
| `ping_real_loss_pct` | % | Measured packet loss via ICMP (D1 only) |

---

*Dataset collected: {__import__('datetime').datetime.utcnow().strftime('%Y-%m-%d')} ·
SDGS HIL Testbed v4.1 · Ground Station: Shenzhen (22.54°N, 114.05°E)*
"""

    path = out_dir / "appendix_summary.md"
    with open(path, "w") as f:
        f.write(md)
    print(f"[PostProc] → {path}")


def write_readme(out_dir: pathlib.Path, log_dir: pathlib.Path,
                 run_count: int, total_rows: int):
    readme = f"""# SDGS HIL Dataset — README

## Summary

Hardware-in-the-Loop experiment traces from the SDGS (Software-Defined Ground Station)
Edge-Intelligence Digital Twin testbed.

- **Runs collected:** {run_count}
- **Total rows:** {total_rows:,} (@ 10 Hz, ~100ms/row)
- **Ground Station:** Shenzhen, China (22.54°N, 114.05°E)
- **TLE Source:** CelesTrak Starlink group (real-time, ~9741 objects)
- **Propagation model:** Skyfield SGP4/SDP4

## File Structure

```
logs/
  run_YYYYMMDD_HHMMSS_<LABEL>/
    meta.json        # Run parameters
    telemetry.csv    # 10Hz telemetry (main dataset)
    events.jsonl     # Discrete event log
dataset/
  appendix_table1.csv    # Performance comparison (Table 1)
  appendix_table2.csv    # TA/CFO distributions (Table 2)
  appendix_fidelity.csv  # tc netem fidelity (Table 3)
  appendix_summary.md    # Full appendix text
  README_dataset.md      # This file
```

## Experimental Notes

1. **TA/CFO values** are emulated residuals driven by the orbital geometry model
   and Edge-AI PID controller state, not measured from a physical RF front-end.
   This is by design: the HIL testbed operates at Layer 3 (WireGuard VPN) and
   uses cross-layer penalty mapping to translate PHY-layer impairments into
   measurable network-layer effects.

2. **Independent measurements** (D1 run `ping_real_*` columns) are genuine ICMP
   probe results from Raspberry Pi hardware, providing ground-truth validation of
   the simulation pipeline's fidelity.

3. **Spectral Efficiency** is not reported because it requires PHY-layer MCS/SINR
   information unavailable in the Layer-3 testbed. This metric is deferred to
   future work with RF-capable prototypes.

## Reproduction

```bash
# 1. Start engine
cd /home/ubuntu/sdgs_lab
python3 sdgs_web_engine.py &

# 2. Run experiment matrix
python3 experiment_runner.py --duration 180

# 3. Post-process
python3 post_process.py
```

## Citation

He, L. (2026). Edge Intelligence-Driven Uplink Optimization for Software-Defined
Ground Stations in 5G NTN Scenarios. OgCloud Limited / Penn State University.
"""
    path = out_dir / "README_dataset.md"
    with open(path, "w") as f:
        f.write(readme)
    print(f"[PostProc] → {path}")


# ── Multi-station cross-comparison ─────────────────────────────────
STATION_DIRS = {
    "Shenzhen":    "logs/shenzhen",
    "Beijing":     "logs/beijing",
    "Los Angeles": "logs/la",
}

def write_cross_station_table(station_results: dict, out_dir: pathlib.Path):
    """
    Generates Table A-4: cross-station comparison of Edge-AI performance.
    station_results = {station_name: {"groups": ..., "fidelity": ...}}
    """
    rows = [["Station", "Lat", "Tput Edge-AI (Mbps)", "Tput Baseline (Mbps)",
             "Tput Δ", "RTT Edge-AI (ms)", "RTT Baseline (ms)",
             "TA P95 closed (µs)", "CFO P95 closed (Hz)"]]

    STATION_LAT = {"Shenzhen": "22.5°N", "Beijing": "39.9°N", "Los Angeles": "34.1°N"}

    for name, result in sorted(station_results.items()):
        g = result["groups"]
        ta_cl  = g["A"]["ta_closed"]
        cfo_cl = g["A"]["cfo_closed"]
        a_tput = mean(g["A"]["throughput"])
        b_tput = mean(g["B"]["throughput"])
        a_rtt  = mean(g["A"]["latency"])
        b_rtt  = mean(g["B"]["latency"])
        try:
            delta = f"+{(a_tput - b_tput)/b_tput*100:.1f}%"
        except Exception:
            delta = "N/A"
        rows.append([
            name, STATION_LAT.get(name, "?"),
            fmt(a_tput), fmt(b_tput), delta,
            fmt(a_rtt),  fmt(b_rtt),
            fmt(percentile(ta_cl,  95)) if ta_cl  else "N/A",
            fmt(percentile(cfo_cl, 95), 0) if cfo_cl else "N/A",
        ])

    path = out_dir / "appendix_table4_cross_station.csv"
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)
    print(f"[PostProc] → {path}")
    return rows


def run_single(log_dir: pathlib.Path, out_dir: pathlib.Path):
    """Process a single station log directory."""
    runs = load_runs(log_dir)
    if not runs:
        print(f"[PostProc] No runs found in {log_dir}")
        return None, 0, {}
    total_rows = sum(len(r) for r in runs.values())
    result = analyse(runs, log_dir)
    return result, total_rows, runs


# ── Entry point ────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="SDGS HIL Post-Processor")
    parser.add_argument("--log-dir", default="logs",
                        help="Directory containing run subdirectories (single-station mode)")
    parser.add_argument("--out-dir", default="dataset",
                        help="Output directory for processed tables")
    parser.add_argument("--multi-station", action="store_true",
                        help="Process all stations under logs/shenzhen, logs/beijing, logs/la")
    args = parser.parse_args()

    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.multi_station:
        # ── Multi-station mode ──────────────────────────────────────
        print("\n[PostProc] Multi-station mode — processing all stations\n")
        station_results = {}
        for station_name, station_log_dir in STATION_DIRS.items():
            log_dir = pathlib.Path(station_log_dir)
            if not log_dir.exists():
                print(f"[PostProc] ⚠ {station_name}: log dir not found ({log_dir}), skipping")
                continue
            print(f"\n[PostProc] ── {station_name} ({log_dir}) ──")
            result, total_rows, runs = run_single(log_dir, out_dir)
            if result:
                station_results[station_name] = result
                station_out = out_dir / station_name.lower().replace(" ", "_")
                station_out.mkdir(exist_ok=True)
                t1 = write_table1(result, station_out)
                t2 = write_table2(result, station_out)
                tf = write_fidelity(result, station_out)
                write_markdown(t1, t2, tf, station_out,
                               run_count=len(runs), total_rows=total_rows)
                print(f"  → {station_out}/")

        if len(station_results) > 1:
            print("\n[PostProc] Writing cross-station comparison table...")
            t4 = write_cross_station_table(station_results, out_dir)
            print("\n" + "=" * 70)
            print("  TABLE A-4: Cross-Station Comparison")
            print("=" * 70)
            for row in t4:
                print("  " + " | ".join(str(c)[:22] for c in row))
        else:
            print("[PostProc] ⚠ Only one station found — cross-station table skipped")

        print(f"\n[PostProc] ✓ Multi-station processing complete → {out_dir.resolve()}/\n")

    else:
        # ── Single-station mode (original behaviour) ─────────────────
        # Support both flat logs/ and per-station logs/shenzhen/
        log_dir = pathlib.Path(args.log_dir)
        if not log_dir.exists():
            # Fallback to per-station subdirectory if flat dir missing
            fallback = pathlib.Path("logs/shenzhen")
            if fallback.exists():
                log_dir = fallback
                print(f"[PostProc] Using per-station log dir: {log_dir}")
            else:
                print(f"[PostProc] ✗ log-dir not found: {log_dir}")
                sys.exit(1)

        print(f"\n[PostProc] Loading runs from {log_dir}...")
        runs = load_runs(log_dir)
        if not runs:
            print("[PostProc] ✗ No telemetry CSV found")
            sys.exit(1)

        total_rows = sum(len(r) for r in runs.values())
        print(f"[PostProc] Total: {len(runs)} runs, {total_rows:,} rows\n")

        result = analyse(runs, log_dir)

        print("[PostProc] Writing outputs...")
        t1 = write_table1(result, out_dir)
        t2 = write_table2(result, out_dir)
        tf = write_fidelity(result, out_dir)
        write_markdown(t1, t2, tf, out_dir, run_count=len(runs), total_rows=total_rows)
        write_readme(out_dir, log_dir, run_count=len(runs), total_rows=total_rows)

        print(f"\n[PostProc] ✓ Done. Dataset → {out_dir.resolve()}/\n")

        print("=" * 60)
        print("  TABLE 1: Performance Summary")
        print("=" * 60)
        for row in t1:
            print("  " + " | ".join(str(c)[:28] for c in row))

        print("\n" + "=" * 60)
        print("  TABLE 2: Residual TA/CFO")
        print("=" * 60)
        for row in t2:
            print("  " + " | ".join(str(c)[:28] for c in row))

        if tf:
            print("\n" + "=" * 60)
            print("  TABLE 3: Fidelity (D1 run)")
            print("=" * 60)
            for row in tf:
                print("  " + " | ".join(str(c)[:28] for c in row))


if __name__ == "__main__":
    main()
