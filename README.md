# SDGS Edge Intelligence — ArXiv Release

<p align="center">
  <a href="#english">🇬🇧 English</a> &nbsp;|&nbsp;
  <a href="#chinese">🇨🇳 中文</a>
</p>

---

<a name="english"></a>

# Edge Intelligence-Driven Uplink Optimization for Software-Defined Ground Stations in 5G NTN Scenarios

[![License: Apache 2.0](https://img.shields.io/badge/Code-Apache%202.0-blue.svg)](https://www.apache.org/licenses/LICENSE-2.0)
[![Dataset: CC BY 4.0](https://img.shields.io/badge/Dataset-CC%20BY%204.0-green.svg)](https://creativecommons.org/licenses/by/4.0/)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![arXiv](https://img.shields.io/badge/arXiv-preprint-b31b1b.svg)](#citation)

**Author:** Longji He¹²  
**Affiliations:** ¹OgCloud Limited · ²The Pennsylvania State University  
**Contact:** keith@ogcloud.com · lph5530@psu.edu

## Abstract

This repository contains the full source code, Hardware-in-the-Loop (HIL) testbed implementation, and experimental dataset accompanying the paper:

> **Edge Intelligence-Driven Uplink Optimization for Software-Defined Ground Stations in 5G NTN Scenarios**  
> Longji He. *arXiv preprint*, 2026.

We propose an edge intelligence-driven framework for Software-Defined Ground Stations (SDGS) operating in 5G Non-Terrestrial Network (NTN) environments. An Edge-AI PID closed-loop controller reduces residual Timing Advance (TA) and Carrier Frequency Offset (CFO) to within 3GPP NR compliance thresholds in real hardware.

**Simulation results:** +34.7% throughput · −42.3% latency · +28.1% spectral efficiency  
**HIL results (4 stations, 28 runs):** +143–148% TCP throughput · TA P95 = **0.49 µs** · CFO P95 = **76 Hz**

## System Architecture

```
┌──────────────────────────────────────────────────────────┐
│              Ubuntu Server (Edge Node)                    │
│  ┌────────────────────────┐  ┌──────────────────────────┐│
│  │  sdgs_web_engine.py    │  │   dashboard.html          ││
│  │  Skyfield SGP4 · PID   │  │   WebSocket · ECharts     ││
│  │  Handover FSM · FastAPI│  │   Orbital Globe (Canvas)  ││
│  └──────────┬─────────────┘  └──────────────────────────┘│
│             │ Redis Pub/Sub (tc netem parameters)         │
│  ┌──────────▼─────────────┐                              │
│  │  WireGuard wg0 / wg1   │  ← delay / jitter / loss     │
└──┴──────────┬─────────────┴──────────────────────────────┘
              │ VPN Tunnel
   ┌──────────▼──────────┐
   │  Raspberry Pi 2     │
   │  ntn_worker.py      │  ← Subscribes Redis, applies tc netem
   └─────────────────────┘
```

**Handover FSM:** `NORMAL → PRE_WARN (40°) → PRE_WARM (35°) → SWITCHING (30°) → CLEANUP → NORMAL`

## Repository Structure

```
sdgs_edge_arxiv/
├── sdgs_web_engine.py         # Core engine: orbital mechanics, Edge-AI PID, HIL telemetry
├── dashboard.html             # Real-time dashboard (zero build, single HTML file)
├── ntn_worker.py              # Pi-side worker: tc netem + WireGuard management
├── OpenSN_VLM.py              # Legacy SSH-based tc executor
├── multi_station_runner.py    # 4-station sequential data collection orchestrator
├── experiment_runner.py       # Single-station 7-experiment matrix runner
├── check_orbital_window.py    # Pre-scan Starlink orbital coverage windows
├── post_process.py            # Statistical post-processing → dataset/
├── supplemental_runner.py     # Additional run collection utility
├── .env.example               # Environment variable template (no secrets committed)
│
├── logs/                      # Raw HIL telemetry (28 runs × 4 stations, ~46K rows)
│   ├── shenzhen/              # 7 runs · 22.5°N · 2026-03-09
│   ├── beijing/               # 7 runs · 39.9°N · 2026-03-09
│   ├── tokyo/                 # 7 runs · 35.7°N · 2026-03-10
│   └── la/                    # 7 runs · 34.1°N · 2026-03-10
│
└── dataset/                   # Post-processed tables (paper Appendix A)
    ├── MULTI_STATION_SUMMARY.md
    ├── appendix_table1.csv              # Throughput & latency
    ├── appendix_table2.csv              # Residual TA/CFO distributions
    ├── appendix_table4_cross_station.csv
    └── {shenzhen,beijing,tokyo,los_angeles}/
```

## HIL Dataset

| Station | Coordinates | Runs | Rows | Date |
|---|---|---|---|---|
| Shenzhen | 22.5°N, 114.1°E | 7 | 11,541 | 2026-03-09 |
| Beijing | 39.9°N, 116.4°E | 7 | 11,502 | 2026-03-09 |
| Tokyo | 35.7°N, 139.7°E | 7 | 11,459 | 2026-03-10 |
| Los Angeles | 34.1°N, 118.3°W | 7 | 11,447 | 2026-03-10 |

Each run: `meta.json` (config) + `telemetry.csv` (1 Hz, ~1,640 rows) + `events.jsonl` (FSM transitions).  
**Key result:** TA P95 = 0.49 µs and CFO P95 = 76 Hz are *identical* across all 4 stations.

## Quick Start

```bash
# 1. Install dependencies
pip3 install fastapi "uvicorn[standard]" aioredis skyfield requests websockets

# 2. Get Starlink TLEs
curl -o starlink.tle "https://celestrak.org/SATCAT/elements.php?GROUP=starlink&FORMAT=tle"

# 3. Configure (only needed for real HIL hardware)
cp .env.example .env   # edit with your Pi IPs — no hardware needed for simulation mode

# 4. Start engine (simulation mode works without any hardware)
python3 sdgs_web_engine.py --station-name Shenzhen --lat 22.54 --lon 114.05 --alt 20
# Dashboard → http://localhost:8000

# 5. Run experiment matrix
python3 experiment_runner.py

# 6. Reproduce paper dataset tables
python3 post_process.py --multi-station
```

## Configuration

Key CLI arguments for `sdgs_web_engine.py`:

| Argument | Default | Description |
|---|---|---|
| `--lat` | 22.54 | Ground station latitude (°N) |
| `--lon` | 114.05 | Ground station longitude (°E) |
| `--station-name` | Shenzhen | Station label |
| `--port` | 8000 | HTTP/WebSocket port |
| `--log-dir` | logs/ | Run log directory |

Hardware IPs are configured via environment variables (see `.env.example`).  
**The engine runs in full simulation mode with no hardware required.**

## Reproducing Paper Results

```bash
# Reproduce Appendix tables from existing raw logs
python3 post_process.py --multi-station

# Re-run full 4-station data collection from scratch
python3 check_orbital_window.py --hours 4   # verify coverage windows first
python3 multi_station_runner.py --stations shenzhen beijing tokyo la --duration 180
python3 post_process.py --multi-station
```

## Citation

```bibtex
@article{he2026sdgs,
  title   = {Edge Intelligence-Driven Uplink Optimization for Software-Defined
             Ground Stations in {5G} {NTN} Scenarios},
  author  = {He, Longji},
  journal = {arXiv preprint},
  year    = {2026},
  url     = {arxiv.org/abs/2604.13984}
}
```

## License

Code: **Apache License 2.0** · Dataset (`logs/`, `dataset/`): **CC BY 4.0**

Satellite TLE data: [CelesTrak](https://celestrak.org) (T.S. Kelso) · Propagation: [Skyfield](https://rhodesmill.org/skyfield/) (Brandon Rhodes)

---

<a name="chinese"></a>

# 面向 5G NTN 软件定义地面站的边缘智能上行链路优化

[![协议: Apache 2.0](https://img.shields.io/badge/代码-Apache%202.0-blue.svg)](https://www.apache.org/licenses/LICENSE-2.0)
[![数据集: CC BY 4.0](https://img.shields.io/badge/数据集-CC%20BY%204.0-green.svg)](https://creativecommons.org/licenses/by/4.0/)

<p align="right"><a href="#english">↑ Switch to English</a></p>

## 摘要

本仓库包含论文《面向 5G NTN 软件定义地面站的边缘智能上行链路优化》的完整源代码、硬件在环（HIL）测试平台实现及实验数据集。

本文提出一种面向 5G 非地面网络（NTN）场景的软件定义地面站（SDGS）边缘智能上行优化框架，利用终端侧几何推导实现预测性多普勒补偿，并结合 **Edge-AI PID 闭环控制器** 自适应抑制残余定时提前（TA）和载波频偏（CFO），使其满足 3GPP NR 物理层门限要求。

**仿真结果：** 上行吞吐量 +34.7%，端到端时延 −42.3%，频谱效率 +28.1%  
**HIL 实测结果（4 站点，28 次运行）：** TCP 吞吐量 +143–148%，残余 TA P95 = **0.49 µs**，残余 CFO P95 = **76 Hz**

## 系统架构

```
┌──────────────────────────────────────────────────────────┐
│              Ubuntu 云服务器（边缘节点）                    │
│  ┌────────────────────────┐  ┌──────────────────────────┐│
│  │  sdgs_web_engine.py    │  │   dashboard.html          ││
│  │  Skyfield SGP4 轨道    │  │   WebSocket 实时仪表盘     ││
│  │  Edge-AI PID 控制器    │  │   ECharts 图表 + 地球仪    ││
│  │  越区切换状态机         │  └──────────────────────────┘│
│  └──────────┬─────────────┘                              │
│             │ Redis Pub/Sub（tc netem 参数传递）           │
│  ┌──────────▼─────────────┐                              │
│  │  WireGuard wg0 / wg1   │  ← 注入延迟/抖动/丢包        │
└──┴──────────┬─────────────┴──────────────────────────────┘
              │ VPN 隧道
   ┌──────────▼──────────┐
   │  Raspberry Pi 2     │
   │  ntn_worker.py      │  ← 订阅 Redis，执行 tc netem
   └─────────────────────┘
```

**越区切换状态机：** `NORMAL → PRE_WARN (40°) → PRE_WARM (35°) → SWITCHING (30°) → CLEANUP → NORMAL`

## 仓库结构

```
sdgs_edge_arxiv/
├── sdgs_web_engine.py         # 核心引擎：轨道力学、Edge-AI PID、HIL 遥测记录
├── dashboard.html             # 单文件实时监控仪表盘（无需构建）
├── ntn_worker.py              # Pi 端 Worker：tc netem + WireGuard 管理
├── multi_station_runner.py    # 4 站点顺序采集编排器
├── experiment_runner.py       # 单站点 7 实验矩阵自动运行器
├── check_orbital_window.py    # Starlink 轨道覆盖窗口预扫描
├── post_process.py            # 统计后处理 → dataset/ 附录表格
├── .env.example               # 环境变量模板（内网 IP 等，不提交 git）
├── logs/                      # 原始 HIL 遥测数据（28 次运行，约 46K 行）
└── dataset/                   # 后处理统计表格（论文附录 A）
```

## HIL 数据集

| 站点 | 坐标 | 运行次数 | 数据行数 | 采集日期 |
|---|---|---|---|---|
| 深圳 | 22.5°N, 114.1°E | 7 | 11,541 | 2026-03-09 |
| 北京 | 39.9°N, 116.4°E | 7 | 11,502 | 2026-03-09 |
| 东京 | 35.7°N, 139.7°E | 7 | 11,459 | 2026-03-10 |
| 洛杉矶 | 34.1°N, 118.3°W | 7 | 11,447 | 2026-03-10 |

每次运行包含：`meta.json`（配置参数）+ `telemetry.csv`（1Hz 遥测，约 1,640 行）+ `events.jsonl`（状态机转换事件）  
**核心发现：** 4 个站点的闭环 TA P95 和 CFO P95 完全一致，证明算法的地理泛化性。

## 快速开始

```bash
# 1. 安装依赖
pip3 install fastapi "uvicorn[standard]" aioredis skyfield requests websockets

# 2. 获取 Starlink TLE 星历
curl -o starlink.tle "https://celestrak.org/SATCAT/elements.php?GROUP=starlink&FORMAT=tle"

# 3. 配置（仅真实 HIL 硬件模式需要）
cp .env.example .env   # 填写 Pi 设备 IP — 纯模拟模式无需硬件

# 4. 启动引擎（纯模拟模式，无需任何硬件）
python3 sdgs_web_engine.py --station-name Shenzhen --lat 22.54 --lon 114.05 --alt 20
# 仪表盘 → http://localhost:8000

# 5. 运行实验矩阵
python3 experiment_runner.py

# 6. 复现论文数据集表格
python3 post_process.py --multi-station
```

## 引用

```bibtex
@article{he2026sdgs,
  title   = {Edge Intelligence-Driven Uplink Optimization for Software-Defined
             Ground Stations in {5G} {NTN} Scenarios},
  author  = {He, Longji},
  journal = {arXiv preprint},
  year    = {2026},
  url     = {https://github.com/keithhegit/sdgs_edge_arxiv}
}
```

## 许可证

代码：**Apache License 2.0** · 数据集（`logs/`、`dataset/`）：**CC BY 4.0**

卫星 TLE 数据由 [CelesTrak](https://celestrak.org)（T.S. Kelso）提供 · 轨道传播使用 [Skyfield](https://rhodesmill.org/skyfield/)（Brandon Rhodes）
