#!/usr/bin/env python3
"""
SDGS HIL Experiment Runner
===========================
自动执行论文 Appendix 数据集的实验矩阵。

实验矩阵：
  A1-A3  Edge-AI ON,  auto-switch,  180s  → 论文 Table1/Table2 (Edge-AI 组)
  B1-B3  Edge-AI OFF, auto-switch,  180s  → 论文 Table1/Table2 (Baseline 组)
  D1     Edge-AI ON,  Real Pi ICMP, 180s  → tc netem 注入保真度验证

用法:
  python3 experiment_runner.py [--base-url http://localhost:8000] [--duration 180]
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error

# ── 实验矩阵定义 ──────────────────────────────────────────────────
MATRIX = [
    {"label": "A1", "edge_ai": True,  "real_measurement": False,
     "desc": "Edge-AI ON  | auto-switch | sim metrics"},
    {"label": "A2", "edge_ai": True,  "real_measurement": False,
     "desc": "Edge-AI ON  | auto-switch | sim metrics"},
    {"label": "A3", "edge_ai": True,  "real_measurement": False,
     "desc": "Edge-AI ON  | auto-switch | sim metrics"},
    {"label": "B1", "edge_ai": False, "real_measurement": False,
     "desc": "Baseline    | auto-switch | sim metrics"},
    {"label": "B2", "edge_ai": False, "real_measurement": False,
     "desc": "Baseline    | auto-switch | sim metrics"},
    {"label": "B3", "edge_ai": False, "real_measurement": False,
     "desc": "Baseline    | auto-switch | sim metrics"},
    {"label": "D1", "edge_ai": True,  "real_measurement": True,
     "desc": "Edge-AI ON  | real Pi ICMP | fidelity check"},
]

INTER_RUN_PAUSE = 10   # 两 run 之间的冷却秒数


# ── HTTP 工具 ─────────────────────────────────────────────────────
def http_get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def http_post(url: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def wait_for_engine(base_url: str, timeout: int = 180) -> bool:
    """等待引擎启动并扫描完成。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status = http_get(f"{base_url}/run/status")
            if status.get("scan_complete"):
                total = status.get("scan_total", 0)
                print(f"[Runner] ✓ 引擎就绪 — TLE 扫描完成 ({total} 颗卫星)")
                return True
            print(f"[Runner] 等待扫描完成... "
                  f"(total={status.get('scan_total', '?')})", end="\r")
        except Exception as e:
            print(f"[Runner] 引擎未响应: {e}", end="\r")
        time.sleep(3)
    return False


def run_experiment(base_url: str, exp: dict, duration: int) -> dict:
    """执行单次 run：start → wait → stop → return summary。"""
    label = exp["label"]
    print(f"\n{'='*60}")
    print(f"[Runner] ▶  Run {label}: {exp['desc']}")
    print(f"         edge_ai={exp['edge_ai']}  "
          f"real_measurement={exp['real_measurement']}  "
          f"duration={duration}s")
    print(f"{'='*60}")

    # 若有残留 run，先停止
    try:
        status = http_get(f"{base_url}/run/status")
        if status.get("active"):
            print(f"[Runner] 检测到残留 run，先停止...")
            http_post(f"{base_url}/run/stop", {})
            time.sleep(2)
    except Exception:
        pass

    # 启动 run
    try:
        resp = http_post(f"{base_url}/run/start", {
            "run_label":        label,
            "edge_ai":          exp["edge_ai"],
            "real_measurement": exp["real_measurement"],
        })
    except Exception as e:
        print(f"[Runner] ✗ 启动失败: {e}")
        return {"label": label, "status": "failed", "error": str(e)}

    run_id = resp.get("run_id", "unknown")
    print(f"[Runner] run_id = {run_id}")

    # 进度轮询
    start_ts = time.time()
    last_print = 0
    while True:
        elapsed = time.time() - start_ts
        if elapsed >= duration:
            break
        if elapsed - last_print >= 10:
            try:
                st = http_get(f"{base_url}/run/status")
                rows = st.get("rows", 0)
                print(f"[Runner]   {elapsed:5.0f}s / {duration}s  "
                      f"rows={rows:5d}  "
                      f"run={st.get('run_id','?')}")
                last_print = elapsed
            except Exception:
                pass
        time.sleep(1)

    # 停止 run
    try:
        stop = http_post(f"{base_url}/run/stop", {})
        rows = stop.get("rows", "?")
        print(f"[Runner] ■  Run {label} 完成 — rows={rows}  run_id={run_id}")
        return {"label": label, "run_id": run_id,
                "status": "ok", "rows": rows}
    except Exception as e:
        print(f"[Runner] ✗ stop 失败: {e}")
        return {"label": label, "run_id": run_id,
                "status": "stop_failed", "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="SDGS HIL Experiment Runner")
    parser.add_argument("--base-url", default="http://localhost:8000",
                        help="Engine base URL")
    parser.add_argument("--duration", type=int, default=180,
                        help="Duration per run in seconds (default: 180)")
    parser.add_argument("--runs", nargs="*",
                        help="Specific run labels to execute (default: all)")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    duration = args.duration

    print(f"\n{'#'*60}")
    print(f"  SDGS HIL Experiment Runner")
    print(f"  Engine : {base_url}")
    print(f"  Duration/run : {duration}s")
    print(f"  Total runs   : {len(MATRIX)}")
    total_min = (len(MATRIX) * duration + (len(MATRIX)-1) * INTER_RUN_PAUSE) / 60
    print(f"  Est. total   : ~{total_min:.0f} min")
    print(f"{'#'*60}\n")

    # 等待引擎就绪
    print("[Runner] 等待引擎扫描完成...")
    if not wait_for_engine(base_url, timeout=180):
        print("[Runner] ✗ 引擎未就绪，退出")
        sys.exit(1)

    # 过滤 run 标签
    matrix = MATRIX
    if args.runs:
        matrix = [e for e in MATRIX if e["label"] in args.runs]
        print(f"[Runner] 仅执行: {[e['label'] for e in matrix]}")

    # 执行实验矩阵
    results = []
    for i, exp in enumerate(matrix):
        result = run_experiment(base_url, exp, duration)
        results.append(result)

        # run 间冷却（最后一个不等）
        if i < len(matrix) - 1:
            print(f"[Runner] 冷却 {INTER_RUN_PAUSE}s...\n")
            time.sleep(INTER_RUN_PAUSE)

    # 汇总
    print(f"\n{'#'*60}")
    print(f"  实验矩阵执行完成")
    print(f"{'#'*60}")
    ok_count = sum(1 for r in results if r.get("status") == "ok")
    print(f"\n  成功: {ok_count}/{len(results)} runs\n")
    for r in results:
        status_sym = "✓" if r.get("status") == "ok" else "✗"
        print(f"  {status_sym} {r['label']:4s}  {r.get('run_id','?'):35s}  "
              f"rows={r.get('rows','?'):>5}")

    # 写汇总到 logs/
    import pathlib
    log_root = pathlib.Path("logs")
    log_root.mkdir(exist_ok=True)
    summary_path = log_root / "experiment_summary.json"
    with open(summary_path, "w") as f:
        json.dump({
            "completed_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            "duration_per_run_s": duration,
            "results": results,
        }, f, indent=2)
    print(f"\n  Summary → {summary_path}")
    print()


if __name__ == "__main__":
    main()
