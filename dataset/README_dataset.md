# SDGS HIL Dataset — README

## Summary

Hardware-in-the-Loop experiment traces from the SDGS (Software-Defined Ground Station)
Edge-Intelligence Digital Twin testbed.

- **Runs collected:** 7
- **Total rows:** 11,541 (@ 10 Hz, ~100ms/row)
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
