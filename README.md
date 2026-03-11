# Edge Intelligence-Driven Uplink Optimization for Software-Defined Ground Stations in 5G NTN Scenarios

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![arXiv](https://img.shields.io/badge/arXiv-preprint-b31b1b.svg)](#citation)

**Authors:** Longji He¹²  
**Affiliations:** ¹OgCloud Limited · ²The Pennsylvania State University  
**Contact:** keith@ogcloud.com · lph5530@psu.edu

---

## Overview

This repository contains the full source code, HIL testbed implementation, and experimental dataset for the paper:

> **Edge Intelligence-Driven Uplink Optimization for Software-Defined Ground Stations in 5G NTN Scenarios**  
> Longji He. *arXiv preprint*, 2026.

We propose an edge intelligence-driven uplink optimization framework for Software-Defined Ground Stations (SDGS) operating in 5G Non-Terrestrial Network (NTN) environments. The system leverages UE-side geometric Doppler pre-compensation combined with an adaptive Edge-AI PID closed-loop controller to reduce residual Timing Advance (TA) and Carrier Frequency Offset (CFO) to within 3GPP-compliant thresholds.

**Key simulation results:**
- +34.7% uplink throughput improvement
- −42.3% latency reduction
- +28.1% spectral efficiency enhancement

**HIL validation results (4-station, 28 runs):**
- +143–148% TCP throughput improvement across all stations
- Residual TA P95 = **0.49 µs** (< 2.34 µs CP limit) — consistent across 4 geographic locations
- Residual CFO P95 = **76 Hz** (< 90 Hz SCS/30 limit) — geographically invariant

---

## System Architecture

```
┌─────────────────────────────────────────────────────────┐
│              Ubuntu Cloud Server (Edge Node)             │
│  ┌─────────────────────┐   ┌──────────────────────────┐ │
│  │  sdgs_web_engine.py  │   │     dashboard.html        │ │
│  │  - Skyfield SGP4/4   │   │  Real-time WebSocket UI   │ │
│  │  - Edge-AI PID ctrl  │   │  ECharts · Orbital Globe  │ │
│  │  - Handover FSM      │   └──────────────────────────┘ │
│  │  - FastAPI + WS      │                                 │
│  └────────┬────────────┘                                 │
│           │ Redis Pub/Sub                                 │
│  ┌────────▼────────────┐                                 │
│  │  WireGuard wg0/wg1  │ ← tc netem (delay/jitter/loss) │
└──┴────────┬────────────┴─────────────────────────────────┘
            │ VPN Tunnel
   ┌────────▼──────────┐
   │  Raspberry Pi 2   │
   │  ntn_worker.py    │  ← Subscribes Redis, applies tc
   │  WireGuard client │
   └───────────────────┘
```

**Handover State Machine:** `NORMAL → PRE_WARN → PRE_WARM → SWITCHING → CLEANUP → NORMAL`  
Pre-warm latency: ~0 ms link interruption for pre-warmed handovers.

---

## Repository Structure

```
sdgs_edge/
├── sdgs_web_engine.py        # Core engine: orbital mechanics, Edge-AI PID, HIL telemetry
├── dashboard.html            # Real-time monitoring dashboard (single-file, no build needed)
├── ntn_worker.py             # Pi-side worker: tc netem + WireGuard management
├── OpenSN_VLM.py             # Legacy SSH-based tc executor (superseded by ntn_worker)
├── multi_station_runner.py   # Orchestrate 4-station sequential data collection
├── experiment_runner.py      # Single-station automated experiment matrix
├── supplemental_runner.py    # Additional run collection utility
├── check_orbital_window.py   # Pre-scan Starlink orbital coverage windows
├── post_process.py           # Statistical post-processing → dataset/
├── .env.example              # Environment variable template (IPs, credentials)
│
├── logs/                     # Raw HIL telemetry (28 runs × 4 stations)
│   ├── shenzhen/             # 7 runs · 2026-03-09 · 22.5°N 114.1°E
│   ├── beijing/              # 7 runs · 2026-03-09 · 39.9°N 116.4°E
│   ├── tokyo/                # 7 runs · 2026-03-10 · 35.7°N 139.7°E
│   └── la/                   # 7 runs · 2026-03-10 · 34.1°N 118.3°W
│
└── dataset/                  # Post-processed statistical tables (paper Appendix)
    ├── MULTI_STATION_SUMMARY.md   # Cross-station analysis summary
    ├── appendix_table1.csv        # Throughput & latency comparison
    ├── appendix_table2.csv        # Residual TA/CFO distributions
    ├── appendix_table4_cross_station.csv  # 4-station geographic comparison
    ├── {shenzhen,beijing,tokyo,los_angeles}/  # Per-station detailed tables
    └── README_dataset.md          # Dataset schema documentation
```

---

## Hardware-in-the-Loop (HIL) Dataset

The experimental dataset covers **28 runs** across 4 geographically distributed ground station locations, each running the full 7-experiment matrix (A1–A3: Edge-AI ON, B1–B3: Baseline open-loop, D1: Edge-AI + real ICMP measurement).

| Station | Coordinates | Runs | Total Rows | Collection Date |
|---|---|---|---|---|
| Shenzhen | 22.5°N, 114.1°E | 7 | 11,541 | 2026-03-09 |
| Beijing | 39.9°N, 116.4°E | 7 | 11,502 | 2026-03-09 |
| Tokyo | 35.7°N, 139.7°E | 7 | 11,459 | 2026-03-10 |
| Los Angeles | 34.1°N, 118.3°W | 7 | 11,447 | 2026-03-10 |

Each run directory contains:
- `meta.json` — station coordinates, PID parameters, run configuration
- `telemetry.csv` — per-second telemetry: elevation, TA, CFO, throughput, RTT, handover phase
- `events.jsonl` — timestamped handover state machine transitions

**Key finding:** Closed-loop TA P95 = 0.49 µs and CFO P95 = 76 Hz are *identical* across all 4 stations (17° latitude span), confirming geographic generalizability of the PID compensation.

---

## Quick Start

### Prerequisites

```bash
# Ubuntu 22.04+ / Debian 12+
sudo apt install python3-pip redis-server wireguard iproute2 iputils-ping

pip3 install fastapi uvicorn[standard] aioredis skyfield requests websockets
```

### 1. Obtain Starlink TLE data

```bash
curl -o starlink.tle "https://celestrak.org/SATCAT/elements.php?GROUP=starlink&FORMAT=tle"
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your Pi IP addresses (only needed for real HIL mode)
# In simulation-only mode, no hardware is required
```

### 3. Start the engine (simulation mode — no hardware needed)

```bash
python3 sdgs_web_engine.py --station-name Shenzhen --lat 22.54 --lon 114.05 --alt 20
# Dashboard: http://localhost:8000
```

### 4. Run the full experiment matrix

```bash
# Single station
python3 experiment_runner.py

# Multi-station (4 stations sequentially)
python3 multi_station_runner.py --stations shenzhen beijing tokyo la --duration 180
```

### 5. Post-process results

```bash
python3 post_process.py --multi-station
# Outputs: dataset/{station}/appendix_*.csv  and  dataset/appendix_table4_cross_station.csv
```

### 6. Re-run full pipeline from scratch

```bash
# Check orbital coverage windows before collecting
python3 check_orbital_window.py --hours 4

# Collect data
python3 multi_station_runner.py --stations shenzhen beijing tokyo la --duration 180

# Process and generate tables
python3 post_process.py --multi-station
```

---

## Configuration Reference

Key CLI arguments for `sdgs_web_engine.py`:

| Argument | Default | Description |
|---|---|---|
| `--lat` | 22.54 | Ground station latitude (°N) |
| `--lon` | 114.05 | Ground station longitude (°E) |
| `--alt` | 20.0 | Altitude (m) |
| `--station-name` | Shenzhen | Station label for logs/display |
| `--port` | 8000 | HTTP/WebSocket listen port |
| `--log-dir` | logs/ | Base directory for run logs |

Hardware-dependent IPs are set via environment variables (see `.env.example`). The engine operates in **full simulation mode** when no hardware is connected.

---

## Reproducing Paper Results

To reproduce the HIL Appendix tables from the raw logs already in this repository:

```bash
python3 post_process.py --multi-station
# Regenerates dataset/appendix_table{1,2,4}_*.csv from logs/
```

To reproduce the simulation results (Section 5 of the paper), the engine self-simulates LEO orbital mechanics, Doppler shifts, TA/CFO errors, and cross-layer penalties without any hardware. Simply run the experiment matrix as described above.

---

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| `skyfield` | ≥ 1.46 | SGP4/SDP4 satellite propagation |
| `fastapi` | ≥ 0.110 | REST API + WebSocket server |
| `uvicorn` | ≥ 0.29 | ASGI server |
| `aioredis` | ≥ 2.0 | Async Redis Pub/Sub |
| `requests` | ≥ 2.31 | TLE download |
| `websockets` | ≥ 12.0 | Runner WebSocket client |

Frontend: pure HTML/JS — no build step. Uses ECharts (CDN) and custom Canvas 2D orbital globe.

---

## Citation

If you use this code or dataset in your research, please cite:

```bibtex
@article{he2026sdgs,
  title   = {Edge Intelligence-Driven Uplink Optimization for Software-Defined
             Ground Stations in {5G} {NTN} Scenarios},
  author  = {He, Longji},
  journal = {arXiv preprint},
  year    = {2026},
  url     = {https://github.com/keithhegit/sdgs_edge}
}
```

---

## License

This project is released under the **MIT License**. See [LICENSE](LICENSE) for details.

The HIL dataset (`logs/` and `dataset/`) is released under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

---

## Acknowledgements

- Satellite TLE data provided by [CelesTrak](https://celestrak.org) (T.S. Kelso)
- Orbital propagation via [Skyfield](https://rhodesmill.org/skyfield/) (Brandon Rhodes)
- Starlink constellation reference: SpaceX Starlink Gen2 Shell 1 (53° inclination)
