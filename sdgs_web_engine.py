import argparse
import asyncio
import re
import time
import random
import math
import json
import csv
import pathlib
import uvicorn
import redis.asyncio as aioredis
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from skyfield.api import load, wgs84

# ── CLI 参数解析（支持多站点部署）──────────────────────────────────────
_parser = argparse.ArgumentParser(description="SDGS Digital Twin Engine")
_parser.add_argument("--lat",          type=float, default=22.54,      help="Ground station latitude  (°N)")
_parser.add_argument("--lon",          type=float, default=114.05,     help="Ground station longitude (°E)")
_parser.add_argument("--alt",          type=float, default=20.0,       help="Ground station altitude  (m)")
_parser.add_argument("--station-name", type=str,   default="Shenzhen", help="Ground station name")
_parser.add_argument("--port",         type=int,   default=8000,       help="HTTP/WebSocket listen port")
_parser.add_argument("--log-dir",      type=str,   default="logs",     help="Base directory for run logs")
_args, _unknown = _parser.parse_known_args()  # _unknown handles uvicorn/pytest args gracefully

SPEED_OF_LIGHT_KM_S = 299792.458     # km/s
CP_TOLERANCE_US = 2.34
SCS_TOLERANCE_HZ = 90.0

# ── 结构化日志根目录 ──
LOG_ROOT = None  # set after CLI args parsed → see bottom of module-level init

# ══════════════════════════════════════════════════════════════════
#  DataCollector — HIL 实验数据采集器
#  写入 logs/<run_id>/telemetry.csv   (高频遥测, ~10Hz)
#         logs/<run_id>/events.jsonl  (离散事件)
#         logs/<run_id>/meta.json     (运行元数据)
# ══════════════════════════════════════════════════════════════════
class DataCollector:
    TELEM_FIELDS = [
        "timestamp_iso", "elapsed_s", "run_id", "run_label",
        "sat_name", "elevation_deg", "slant_range_km",
        "sat_lat", "sat_lon",
        "propagation_one_way_ms", "rtt_model_ms", "jitter_ms", "loss_pct",
        "throughput_mbps",
        "residual_ta_us", "residual_cfo_hz",
        "edge_ai_enabled", "handover_phase", "diag_color",
        "ping_real_delay_ms", "ping_real_loss_pct",
        "ping_real_jitter_ms", "ping_via",
    ]

    def __init__(self):
        self.run_id: str | None = None
        self.run_label: str = ""
        self._csv_file = None
        self._csv_writer = None
        self._event_file = None
        self._start_wall: float = 0.0
        self._run_dir: pathlib.Path | None = None
        self._active: bool = False
        self._last_handover_phase: str = "NORMAL"
        self._last_ai_state: bool = True
        self._row_count: int = 0

    def start_run(self, run_id: str, run_label: str, meta: dict) -> None:
        self._close_files()
        self.run_id = run_id
        self.run_label = run_label
        self._run_dir = LOG_ROOT / run_id
        self._run_dir.mkdir(parents=True, exist_ok=True)
        self._start_wall = time.time()
        self._row_count = 0

        # 元数据 JSON
        with open(self._run_dir / "meta.json", "w") as f:
            json.dump({"run_id": run_id, "run_label": run_label,
                       "start_time": datetime.utcnow().isoformat() + "Z",
                       **meta}, f, indent=2)

        # 遥测 CSV（行缓冲，每行即时落盘）
        self._csv_file = open(self._run_dir / "telemetry.csv", "w",
                              newline="", buffering=1)
        self._csv_writer = csv.DictWriter(self._csv_file,
                                          fieldnames=self.TELEM_FIELDS)
        self._csv_writer.writeheader()

        # 事件 JSONL（行缓冲）
        self._event_file = open(self._run_dir / "events.jsonl", "w",
                                buffering=1)

        self._active = True
        self._last_handover_phase = "NORMAL"
        self._last_ai_state = True
        self._log_event_raw("RUN_START", run_id=run_id, run_label=run_label)
        print(f"[Collector] ▶ Run started: {run_id} ({run_label})")

    def stop_run(self) -> str | None:
        if not self._active:
            return None
        duration = time.time() - self._start_wall
        self._log_event_raw("RUN_STOP",
                            duration_s=round(duration, 1),
                            total_rows=self._row_count)
        run_id = self.run_id
        print(f"[Collector] ■ Run stopped: {run_id} | "
              f"rows={self._row_count} | {duration:.0f}s")
        self._close_files()
        self._active = False
        self.run_id = None
        return run_id

    def _close_files(self):
        for f in [self._csv_file, self._event_file]:
            if f:
                try:
                    f.flush()
                    f.close()
                except Exception:
                    pass
        self._csv_file = None
        self._csv_writer = None
        self._event_file = None

    def record_tick(self, *, alt_deg: float, dist_km: float,
                    sat_lat: float, sat_lon: float, sat_name: str,
                    one_way_prop_ms: float, base_delay_ms: float,
                    jitter_ms: float, loss_pct: float,
                    residual_ta: float, residual_cfo: float,
                    est_tput: float, diag_color: str) -> None:
        if not self._active or not self._csv_writer:
            return

        elapsed = time.time() - self._start_wall
        cur_phase = handover_state["phase"]
        cur_ai = sim_state["edge_ai_enabled"]

        row = {
            "timestamp_iso": (datetime.utcnow()
                              .isoformat(timespec="milliseconds") + "Z"),
            "elapsed_s":              round(elapsed, 2),
            "run_id":                 self.run_id,
            "run_label":              self.run_label,
            "sat_name":               sat_name,
            "elevation_deg":          round(alt_deg, 3),
            "slant_range_km":         round(dist_km, 1),
            "sat_lat":                round(sat_lat, 4),
            "sat_lon":                round(sat_lon, 4),
            "propagation_one_way_ms": round(one_way_prop_ms, 3),
            "rtt_model_ms":           round(base_delay_ms, 2),
            "jitter_ms":              round(jitter_ms, 3),
            "loss_pct":               round(loss_pct, 3),
            "throughput_mbps":        round(est_tput, 2),
            "residual_ta_us":         round(residual_ta, 3),
            "residual_cfo_hz":        round(residual_cfo, 1),
            "edge_ai_enabled":        int(cur_ai),
            "handover_phase":         cur_phase,
            "diag_color":             diag_color,
            "ping_real_delay_ms":     (real_metrics.get("delay")
                                       if real_metrics.get("delay") is not None
                                       else ""),
            "ping_real_loss_pct":     (real_metrics.get("loss")
                                       if real_metrics.get("loss") is not None
                                       else ""),
            "ping_real_jitter_ms":    (real_metrics.get("jitter")
                                       if real_metrics.get("jitter") is not None
                                       else ""),
            "ping_via":               real_metrics.get("via") or "",
        }
        self._csv_writer.writerow(row)
        self._row_count += 1

        # 每 500 行额外 flush 一次（行缓冲已实时，这里是保险）
        if self._row_count % 500 == 0:
            self._csv_file.flush()

        # 检测状态跳转 → 写事件日志
        if cur_phase != self._last_handover_phase:
            self._log_event_raw(
                "HANDOVER_PHASE_CHANGE",
                from_phase=self._last_handover_phase,
                to_phase=cur_phase,
                primary_elev=round(alt_deg, 2),
                standby_name=handover_state.get("standby_name"),
                standby_elev=handover_state.get("standby_alt"),
                elapsed_s=round(elapsed, 1),
            )
            self._last_handover_phase = cur_phase

        if cur_ai != self._last_ai_state:
            self._log_event_raw("EDGE_AI_TOGGLE",
                                enabled=cur_ai,
                                elapsed_s=round(elapsed, 1))
            self._last_ai_state = cur_ai

    def log_event(self, event_type: str, **kwargs) -> None:
        self._log_event_raw(event_type, **kwargs)

    def _log_event_raw(self, event_type: str, **kwargs) -> None:
        if not self._event_file:
            return
        entry = {
            "ts": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
            "event": event_type,
            **kwargs,
        }
        self._event_file.write(json.dumps(entry) + "\n")

# ── Starlink 端到端延迟模型（参考 SpaceX 2025 官方白皮书 + Ookla 实测）──
# RTT = 2×propagation + ground_network + fronthaul_scheduling + processing
# SpaceX 实测: US median 33ms RTT, p99 < 65ms, 目标 20ms
GROUND_NETWORK_MS = 10.0             # 地面网络路由（PoP → 互联网），8-12ms
FRONTHAUL_SCHED_MS = 6.0             # 前传调度（卫星波束共享无线资源），5-8ms
PROCESSING_MS = 5.0                  # 星上/终端处理 + 缓冲，4-6ms
HANDOVER_SPIKE_MS = 140.0            # 15秒卫星重配置周期边界延迟尖峰

# ── 地面站坐标（从 CLI 参数读取，默认深圳）──────────────────────────────
GS_LAT  = _args.lat
GS_LON  = _args.lon
GS_ALT  = _args.alt
GS_NAME = _args.station_name
LOG_ROOT = pathlib.Path(_args.log_dir)   # replaces the placeholder above
TLE_FILE = 'starlink.tle'
TOP_N_SATS = 60            # 初始最优候选星（轨道模拟用）
MIN_ELEVATION_DEG = 25.0   # 可见星最低仰角阈值
POOL_REFRESH_SEC = 15      # 可见卫星池刷新间隔（秒）
POOL_SIZE = 10             # 可见卫星池最多显示数

app = FastAPI(title="5G NTN SDGS Digital Twin")
r = None

top_sats = []      # 初始最优 TOP_N_SATS 颗，用于初始目标选取与轨道模拟
all_sats  = []      # 全量 TLE 星历（9741 颗），供可见星池实时扫描
data_source = "simulated"

# ── 当前过境可见卫星池（Visible Pool）──
# 每 POOL_REFRESH_SEC 秒异步扫描 all_sats，过滤仰角 > MIN_ELEVATION_DEG 的卫星
visible_pool = []   # list of {name, alt, idx}  idx 指向 all_sats

# ── 真实 Pi 测量数据（方案A：ping）──
measurement_mode = "simulated"    # "simulated" | "real"
real_metrics = {
    "delay": None,    # one-way ms (ping RTT / 2)
    "loss":  None,    # %
    "jitter": None,   # mdev ms
    "last_update": None,
    "reachable": False,
    "via": None,      # "WireGuard" | "LAN" | "timeout"
}

scan_state = {
    "scanning": False,
    "complete": False,
    "progress": 0,
    "total": 0,
    "current_name": "",
    "best_alt": -90.0,
}

sim_state = {
    "edge_ai_enabled": True,
    "force_handover": False,
    "current_sat_idx": 0,
    "switching": False,
    # 自动切换防抖：仰角降入 25-30° 区间时自动切星，之后等新星仰角>35° 才允许再次触发
    "auto_switch_ready": True,
}

# ── 链路预热状态机 ──
# 四阶段: NORMAL → PRE_WARN → PRE_WARM → SWITCHING → CLEANUP → NORMAL
handover_state = {
    "phase":               "NORMAL",  # 当前阶段
    "standby_name":        None,      # 备星名称
    "standby_alt":         None,      # 备星当前仰角 (°)
    "standby_dist":        None,      # 备星当前斜距 (km)
    "standby_delay":       None,      # 备星预计 RTT (ms)
    "standby_loss":        None,      # 备星预计丢包率 (%)
    "standby_idx":         None,      # all_sats 索引
    "pre_warm_published":  False,     # 是否已向 Pi2 发送 prepare 命令
    "secondary_tick":      0,         # 控制 secondary 更新频率
}

# Skyfield handles shared across coroutines (set once in orbit_simulation_loop)
ts_global = None
gs_pos_global = None


class ConnectionManager:
    def __init__(self):
        self.active_connections = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active_connections.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active_connections:
            self.active_connections.remove(ws)

    async def broadcast(self, message: str):
        for connection in list(self.active_connections):
            try:
                await connection.send_text(message)
            except Exception:
                self.active_connections.remove(connection)


manager = ConnectionManager()
collector = DataCollector()


class EdgePIDController:
    def __init__(self, kp=0.5, ki=0.1, kd=0.05):
        self.kp, self.ki, self.kd = kp, ki, kd
        self.integral, self.last_error = 0, 0

    def compute(self, error, dt):
        self.integral += error * dt
        derivative = (error - self.last_error) / dt if dt > 0 else 0
        self.last_error = error
        return self.kp * error + self.ki * self.integral + self.kd * derivative


async def run_scan(ts, gs_pos):
    """异步扫描所有卫星仰角，每 200 颗 yield 一次保持事件循环畅通"""
    global top_sats, all_sats, data_source, scan_state
    try:
        print("[Engine] 正在加载 TLE 数据库...")
        sats = await asyncio.get_event_loop().run_in_executor(
            None, lambda: load.tle_file(TLE_FILE)
        )
        # 保存全量星历供可见星池实时扫描（关键修复：不限制数量）
        all_sats = list(sats)

        scan_state["total"] = len(sats)
        scan_state["scanning"] = True
        scan_state["progress"] = 0
        scan_state["best_alt"] = -90.0
        print(f"[Engine] 已加载 {len(sats)} 颗卫星，开始计算深圳仰角...")

        t_now = ts.now()
        scored = []

        for i, s in enumerate(sats):
            scan_state["progress"] = i + 1
            scan_state["current_name"] = s.name.strip()
            try:
                a, _, _ = (s - gs_pos).at(t_now).altaz()
                scored.append((a.degrees, s))
                if a.degrees > scan_state["best_alt"]:
                    scan_state["best_alt"] = a.degrees
            except Exception:
                pass
            if (i + 1) % 200 == 0:
                await asyncio.sleep(0)

        scored.sort(key=lambda x: x[0], reverse=True)
        # top_sats 仍保留初始最优 TOP_N_SATS 颗用于默认目标选取
        top_sats = [s for _, s in scored[:TOP_N_SATS]]
        data_source = "celestrak"
        scan_state["scanning"] = False
        scan_state["complete"] = True
        best = top_sats[0] if top_sats else None
        scan_state["current_name"] = best.name.strip() if best else ""
        print(f"[Engine] 扫描完成！最佳目标: {best.name if best else 'N/A'}，仰角 {scan_state['best_alt']:.1f}°")
        print(f"[Engine] 全量星历已缓存：{len(all_sats)} 颗，供可见星池持续扫描")
    except Exception as e:
        print(f"[Engine] 扫描失败: {e}")
        scan_state["scanning"] = False
        scan_state["complete"] = False


async def refresh_visible_pool_loop():
    """每 POOL_REFRESH_SEC 秒扫描全量 all_sats，维护当前过境可见卫星池"""
    global visible_pool
    while True:
        # 等待全量星历加载完成再开始扫描
        if all_sats and ts_global and gs_pos_global and scan_state["complete"]:
            pool = []
            t_now = ts_global.now()
            for i, s in enumerate(all_sats):
                try:
                    a, _, _ = (s - gs_pos_global).at(t_now).altaz()
                    if a.degrees > MIN_ELEVATION_DEG:
                        pool.append({
                            "name": s.name.strip(),
                            "alt": round(a.degrees, 1),
                            "idx": i,       # 指向 all_sats 的索引
                        })
                    # 每 200 颗 yield 一次，保持事件循环畅通
                    if (i + 1) % 200 == 0:
                        await asyncio.sleep(0)
                except Exception:
                    pass
            pool.sort(key=lambda x: x["alt"], reverse=True)
            visible_pool = pool[:POOL_SIZE]
            print(f"[Pool] 当前过境可见星池：{len(visible_pool)} 颗 (>{MIN_ELEVATION_DEG}°)"
                  + (f"，最高 {visible_pool[0]['alt']}°" if visible_pool else "，无可见星"))
        await asyncio.sleep(POOL_REFRESH_SEC)


async def ping_measurement_loop():
    """方案A：每8秒 ping Pi1，优先 WireGuard IP，回退到真实 LAN IP"""
    global real_metrics
    # 优先顺序: WireGuard tunnel → 真实 LAN IP
    PI1_WG_IP   = "10.100.0.2"
    PI1_LAN_IP  = "172.31.18.37"

    async def do_ping(ip: str, count: int = 10):
        proc = await asyncio.create_subprocess_exec(
            'ping', '-c', str(count), '-W', '2', '-q', ip,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=35)
        output = stdout.decode()
        loss_m = re.search(r'(\d+)% packet loss', output)
        loss   = float(loss_m.group(1)) if loss_m else 100.0
        rtt_m  = re.search(
            r'rtt min/avg/max/mdev = [\d.]+/([\d.]+)/[\d.]+/([\d.]+) ms', output)
        if rtt_m:
            rtt_avg = float(rtt_m.group(1))
            jitter  = float(rtt_m.group(2))
        else:
            rtt_avg = jitter = None
        return loss, rtt_avg, jitter

    while True:
        try:
            # 先用 3 包快速探测 WireGuard IP 是否可达
            wg_loss, _, _ = await do_ping(PI1_WG_IP, count=3)
            if wg_loss < 100:
                # WireGuard tunnel 活跃，用 10 包完整测量（反映 tc netem 效果）
                loss, rtt_avg, jitter = await do_ping(PI1_WG_IP, count=10)
                via = "WireGuard"
                target_ip = PI1_WG_IP
            else:
                # WireGuard 未连接，回退到真实 LAN IP
                loss, rtt_avg, jitter = await do_ping(PI1_LAN_IP, count=10)
                via = "LAN"
                target_ip = PI1_LAN_IP

            one_way = round(rtt_avg / 2.0, 2) if rtt_avg else None
            real_metrics.update({
                "delay":       one_way,
                "loss":        round(loss, 1),
                "jitter":      round(jitter, 2) if jitter else None,
                "last_update": (datetime.utcnow() + __import__('datetime').timedelta(hours=8)).strftime("%H:%M:%S"),
                "reachable":   (loss < 100),
                "via":         via,
            })
            print(f"[Ping] Pi1 {target_ip} ({via}): "
                  f"单向={one_way}ms, mdev={jitter}ms, 丢包={loss}%")
        except asyncio.TimeoutError:
            real_metrics.update({
                "delay": None, "loss": 100.0, "reachable": False,
                "via": "timeout", "last_update": (datetime.utcnow() + __import__('datetime').timedelta(hours=8)).strftime("%H:%M:%S")
            })
            print("[Ping] Pi1 全部超时，标记为不可达")
        except Exception as e:
            print(f"[Ping] 采集异常: {e}")
        await asyncio.sleep(8)


def calc_link_params(alt_deg: float, dist_km: float) -> dict:
    """根据仰角和斜距计算链路参数（主星/备星通用）"""
    one_way_prop_ms = (dist_km / SPEED_OF_LIGHT_KM_S) * 1000.0
    rtt_base = (2.0 * one_way_prop_ms
                + GROUND_NETWORK_MS + FRONTHAUL_SCHED_MS + PROCESSING_MS)
    elev_rad = math.radians(max(alt_deg, 5.0))
    rtt_base += 1.2 / math.sin(elev_rad)   # 大气散射
    rtt = max(20.0, round(rtt_base, 2))

    if alt_deg > 60:
        loss = 0.05 + random.uniform(0, 0.05)
    elif alt_deg > 45:
        loss = 0.10 + random.uniform(0, 0.15)
    elif alt_deg > 35:
        loss = 0.25 + random.uniform(0, 0.25)
    elif alt_deg > 30:
        loss = 0.50 + random.uniform(0, 0.40)
    else:
        loss = 1.20 + random.uniform(0, 1.00)

    return {
        "rtt":    rtt,
        "delay":  round(rtt / 2, 1),          # tc netem 单向
        "jitter": round(rtt * 0.04, 1),
        "loss":   round(max(0.02, loss), 2),
    }


async def orbit_simulation_loop():
    global top_sats, data_source, ts_global, gs_pos_global
    print("[Engine] SDGS 数字孪生 Web 引擎启动，异步扫描卫星数据库...")

    ts = load.timescale()
    ts_global = ts
    gs_pos = wgs84.latlon(GS_LAT, GS_LON, elevation_m=GS_ALT)
    gs_pos_global = gs_pos

    # 立即启动扫描（不阻塞 orbit loop）
    asyncio.create_task(run_scan(ts, gs_pos))
    # 启动可见星池刷新任务
    asyncio.create_task(refresh_visible_pool_loop())

    pid_ctrl = EdgePIDController()
    last_time = time.time()
    sim_angle = 0.0

    # EMA 平滑状态（跨循环持久）
    smoothed_delay_ms: float | None = None
    EMA_ALPHA = 0.15          # 新值权重；越小越平滑

    # 持续型 spike 状态（让偶发抖峰持续 2-4s，而非单帧闪烁）
    spike_ticks_remaining = 0
    spike_extra_ms = 0.0

    while True:
        curr_time = time.time()
        dt = max(curr_time - last_time, 1e-6)
        last_time = curr_time

        # 卫星选取
        sat = None
        sat_name = "SIMULATED ORBIT"
        sat_epoch = "N/A"

        if top_sats and not scan_state["scanning"]:
            idx = sim_state["current_sat_idx"] % len(top_sats)
            sat = top_sats[idx]
            sat_name = sat.name.strip()
            try:
                sat_epoch = sat.epoch.utc_strftime('%Y-%m-%d')
            except Exception:
                sat_epoch = "N/A"
        elif scan_state["scanning"] and scan_state["current_name"]:
            sat_name = scan_state["current_name"]

        # 轨道位置计算
        sat_lat = GS_LAT
        sat_lon = GS_LON
        if sat is not None:
            try:
                t_now = ts.now()
                alt, az, distance = (sat - gs_pos).at(t_now).altaz()
                alt_deg = alt.degrees
                dist_km = distance.km
                sub = wgs84.subpoint(sat.at(t_now))
                sat_lat = sub.latitude.degrees
                sat_lon = sub.longitude.degrees
            except Exception:
                alt_deg = -90.0
                dist_km = 13000.0
        else:
            sim_angle = (sim_angle + dt * 0.5) % 360
            alt_deg = 45.0 * math.sin(math.radians(sim_angle)) + 30.0
            dist_km = 550.0 / max(math.sin(math.radians(max(alt_deg, 1.0))), 0.01)
            sat_lat = GS_LAT + 35 * math.sin(math.radians(sim_angle))
            sat_lon = GS_LON + 50 * math.cos(math.radians(sim_angle))

        # ══════════════════════════════════════════════════════════════
        #  Starlink 端到端 RTT 模型（基于 SpaceX 2025 白皮书 + 实测校准）
        #
        #  RTT = 2 × propagation + ground_network + fronthaul + processing
        #
        #  Propagation (one-way): slant_range / c
        #    ε=90°: 550km  → 1.8ms     ε=45°: 786km → 2.6ms
        #    ε=30°: 1126km → 3.8ms     ε=25°: 1320km → 4.4ms
        #
        #  Real-world reference:
        #    US median RTT:  33ms   (SpaceX 2025)
        #    Aviation RTT:   65ms   (Speedtest Starlink in-flight)
        #    P99:           <65ms   (SpaceX 2025)
        #    Handover spike: 140ms  (15s reconfiguration boundary)
        # ══════════════════════════════════════════════════════════════

        # One-way propagation (ms)
        one_way_prop_ms = (dist_km / SPEED_OF_LIGHT_KM_S) * 1000.0

        # RTT = 2 × propagation + fixed overheads + jitter
        rtt_base = (2.0 * one_way_prop_ms
                    + GROUND_NETWORK_MS
                    + FRONTHAUL_SCHED_MS
                    + PROCESSING_MS)

        # Low-elevation atmospheric scintillation adds extra delay
        elev_rad = math.radians(max(alt_deg, 5.0))
        atm_scatter_ms = 1.2 / math.sin(elev_rad)     # 低仰角 → 更长大气路径
        rtt_base += atm_scatter_ms

        # ── 持续型 spike 管理（1% 概率触发，持续 20-40 ticks ≈ 2-4s）──
        if spike_ticks_remaining > 0:
            spike_ticks_remaining -= 1
        elif random.random() < 0.01:                   # 降低概率：3%→1%
            spike_extra_ms        = random.uniform(12, 35)
            spike_ticks_remaining = random.randint(20, 40)

        # 基础随机抖动（帧内）：只保留小幅高斯，不再直接叠加大 spike
        jitter_component = random.gauss(0, 0.6) + (spike_extra_ms if spike_ticks_remaining > 0 else 0.0)

        raw_delay_ms = rtt_base + jitter_component

        # EMA 平滑（α=0.15：新值权重低，曲线有惯性）
        if smoothed_delay_ms is None:
            smoothed_delay_ms = raw_delay_ms            # 首次初始化
        else:
            smoothed_delay_ms = EMA_ALPHA * raw_delay_ms + (1 - EMA_ALPHA) * smoothed_delay_ms

        base_delay_ms = max(20.0, round(smoothed_delay_ms, 2))

        # ── 丢包率模型（基于 WirelessMoves 2024 + Zenodo 11-day dataset）──
        # 正常: 0.05-0.3%, handover burst: up to 3-10%
        # 低仰角大气多径 + 波束切换频率增加 → 丢包上升
        if alt_deg > 60:
            base_loss = 0.05 + random.uniform(0, 0.10)     # 最佳工况
        elif alt_deg > 45:
            base_loss = 0.10 + random.uniform(0, 0.20)     # 良好
        elif alt_deg > 35:
            base_loss = 0.25 + random.uniform(0, 0.35)     # 正常
        elif alt_deg > 30:
            base_loss = 0.50 + random.uniform(0, 0.60)     # 边缘
        else:  # 25-30°，即将飞离，大气散射+波束切换密集
            base_loss = 1.20 + random.uniform(0, 1.50)

        # 偶发 handover burst (15s 重配置边界)
        if random.random() < 0.02:
            base_loss += random.uniform(3.0, 8.0)

        base_loss = round(max(0.02, base_loss), 2)

        # ══════════════════════════════════════════════════════════════
        #  链路预热状态机  NORMAL→PRE_WARN→PRE_WARM→SWITCHING→CLEANUP
        # ══════════════════════════════════════════════════════════════
        auto_switched = False

        if alt_deg >= 40.0:
            # 新卫星仰角稳定 → 重置状态机，允许下次切换
            if handover_state["phase"] not in ("NORMAL",):
                if r and handover_state["phase"] == "PRE_WARM":
                    await r.publish('ntn_link_state_secondary',
                                    json.dumps({"action": "cleanup"}))
                handover_state.update({
                    "phase": "NORMAL", "standby_name": None,
                    "standby_alt": None, "standby_dist": None,
                    "standby_delay": None, "standby_loss": None,
                    "standby_idx": None, "pre_warm_published": False,
                    "secondary_tick": 0,
                })
            handover_state["phase"] = "NORMAL"
            sim_state["auto_switch_ready"] = True

        elif 35.0 <= alt_deg < 40.0:
            # ── 阶段一 PRE_WARN：锁定备星候选 ──
            if handover_state["phase"] == "NORMAL":
                standby = next((p for p in visible_pool if p["name"] != sat_name), None)
                if standby:
                    handover_state.update({
                        "phase":        "PRE_WARN",
                        "standby_name": standby["name"],
                        "standby_alt":  standby["alt"],
                        "standby_idx":  standby["idx"],
                    })
                    print(f"[Handover] PRE_WARN 主星仰角 {alt_deg:.1f}° | "
                          f"锁定备星 {standby['name']} ({standby['alt']}°)")

        elif 30.0 <= alt_deg < 35.0:
            # ── 阶段二 PRE_WARM：拉起 wg1，实时推送备星 tc 参数 ──
            if handover_state["phase"] in ("NORMAL", "PRE_WARN"):
                # 紧急选备星（若 PRE_WARN 未能锁定）
                if handover_state["standby_idx"] is None:
                    standby = next((p for p in visible_pool if p["name"] != sat_name), None)
                    if standby:
                        handover_state.update({
                            "standby_name": standby["name"],
                            "standby_alt":  standby["alt"],
                            "standby_idx":  standby["idx"],
                        })
                handover_state["phase"] = "PRE_WARM"

            if handover_state["phase"] == "PRE_WARM":
                s_idx = handover_state["standby_idx"]
                if s_idx is not None and s_idx < len(all_sats):
                    try:
                        s_sat = all_sats[s_idx]
                        s_a, _, s_d = (s_sat - gs_pos).at(ts.now()).altaz()
                        s_params = calc_link_params(s_a.degrees, s_d.km)
                        handover_state.update({
                            "standby_alt":   round(s_a.degrees, 1),
                            "standby_dist":  round(s_d.km, 1),
                            "standby_delay": s_params["rtt"],
                            "standby_loss":  s_params["loss"],
                        })
                        # 每 50 ticks (≈5s) 向 Pi2 推送一次 secondary tc 参数
                        handover_state["secondary_tick"] += 1
                        if (not handover_state["pre_warm_published"]
                                or handover_state["secondary_tick"] >= 50):
                            action = ("prepare" if not handover_state["pre_warm_published"]
                                      else "update_secondary")
                            if r:
                                await r.publish('ntn_link_state_secondary', json.dumps({
                                    "action":   action,
                                    "sat_name": handover_state["standby_name"],
                                    "delay":    s_params["delay"],
                                    "jitter":   s_params["jitter"],
                                    "loss":     s_params["loss"],
                                }))
                            handover_state["pre_warm_published"] = True
                            handover_state["secondary_tick"] = 0
                            if action == "prepare":
                                print(f"[Handover] PRE_WARM → Pi2 wg1 | 备星 "
                                      f"{handover_state['standby_name']} "
                                      f"delay={s_params['delay']}ms "
                                      f"loss={s_params['loss']}%")
                    except Exception as e:
                        print(f"[Handover] 备星轨道计算失败: {e}")

        elif 25.0 <= alt_deg <= 30.0:
            # ── 阶段三 ATOMIC_SWITCH：使用预热备星原子切换 ──
            if sim_state["auto_switch_ready"] and visible_pool:
                # 优先使用 PRE_WARM 预选备星，否则从池中取最优
                if (handover_state["phase"] == "PRE_WARM"
                        and handover_state["standby_idx"] is not None):
                    c_idx  = handover_state["standby_idx"]
                    c_name = handover_state["standby_name"]
                    c_alt  = handover_state["standby_alt"]
                    pre_warmed = True
                else:
                    cand = next((p for p in visible_pool if p["name"] != sat_name), None)
                    c_idx  = cand["idx"]  if cand else None
                    c_name = cand["name"] if cand else None
                    c_alt  = cand["alt"]  if cand else None
                    pre_warmed = False

                if c_idx is not None and c_idx < len(all_sats):
                    sat_obj = all_sats[c_idx]
                    if sat_obj in top_sats:
                        top_sats.insert(0, top_sats.pop(top_sats.index(sat_obj)))
                    else:
                        top_sats.insert(0, sat_obj)
                    sim_state["current_sat_idx"] = 0
                    sim_state["switching"]        = True
                    sim_state["auto_switch_ready"]= False
                    auto_switched = True

                    # 向 Pi2 发布 promote（原子切换 wg0 参数）
                    if r and pre_warmed:
                        s_params_p = calc_link_params(
                            handover_state.get("standby_alt") or 45.0,
                            handover_state.get("standby_dist") or 700.0,
                        )
                        await r.publish('ntn_link_state_secondary', json.dumps({
                            "action": "promote",
                            "delay":  s_params_p["delay"],
                            "jitter": s_params_p["jitter"],
                            "loss":   s_params_p["loss"],
                        }))

                    handover_state["phase"] = "SWITCHING"
                    warm_tag = "预热" if pre_warmed else "冷切换"
                    print(f"[Handover] ATOMIC_SWITCH ({warm_tag}) "
                          f"仰角 {alt_deg:.1f}° → {c_name} ({c_alt}°)")

        else:
            # alt < 25°：主星已飞离，发送清理
            if handover_state["phase"] == "PRE_WARM":
                if r:
                    await r.publish('ntn_link_state_secondary',
                                    json.dumps({"action": "cleanup"}))
                handover_state["phase"] = "CLEANUP"
                print(f"[Handover] CLEANUP 主星仰角 {alt_deg:.1f}°，清理 wg1")

        # ── 跨层物理惩罚（含变量初始化，防 else 分支 jitter_ms 未定义）──
        jitter_ms = 0.0
        if not sim_state["force_handover"] and alt_deg > MIN_ELEVATION_DEG:
            open_loop_cfo = 810.0 + random.uniform(-50, 50)
            if sim_state["edge_ai_enabled"]:
                pid_ctrl.compute(open_loop_cfo, dt)
                residual_ta = 0.45 + random.uniform(-0.05, 0.05)
                residual_cfo = 72.0 + random.uniform(-5, 5)
            else:
                residual_ta = 3.20 + random.uniform(-0.5, 0.5)
                residual_cfo = open_loop_cfo

            if residual_ta > CP_TOLERANCE_US or residual_cfo > SCS_TOLERANCE_HZ:
                # TA 超限 → ISI → HARQ 重传惩罚（3-6 轮 × 8ms TTI往返）
                # CFO 超限 → ICI → 进一步加剧重传次数
                harq_rounds     = random.uniform(3.0, 6.0)
                retx_penalty_ms = harq_rounds * 8.0 + random.uniform(-4, 8)
                # 叠加到传播延迟上，显示为 MAC 层有效 RTT
                base_delay_ms   = round(base_delay_ms + retx_penalty_ms, 2)

                loss_pct  = 12.0 + random.uniform(-1, 3)
                jitter_ms = base_delay_ms * 0.20   # 失锁时抖动也更大
                diag_status = "ISI/ICI 严重干扰 (物理层失锁)"
                diag_color = "danger"
                est_tput = 80.0 + random.uniform(-10, 10)
            else:
                loss_pct  = base_loss
                jitter_ms = base_delay_ms * 0.04
                diag_status = "Edge-AI 闭环锁定 (优)"
                diag_color = "success"
                # 吞吐量模型: ~220Mbps peak (Starlink 2025 实测), 受仰角和丢包影响
                elev_factor = min(1.0, alt_deg / 55.0)     # 55° 以上达到峰值
                loss_penalty = 1.0 - loss_pct / 50.0       # 丢包惩罚
                est_tput = max(15, 220.0 * elev_factor * loss_penalty
                               + random.uniform(-6, 6))

            if r:
                await r.publish('ntn_link_state', json.dumps({
                    'delay': round(base_delay_ms / 2, 1),   # tc netem 取单向
                    'jitter': round(jitter_ms, 1),
                    'loss': round(loss_pct, 2)
                }))
        else:
            if r:
                await r.publish('ntn_link_state', json.dumps({
                    'delay': 500, 'jitter': 0, 'loss': 100
                }))
            pid_ctrl = EdgePIDController()
            loss_pct, base_delay_ms = 100, 10
            residual_ta = residual_cfo = est_tput = 0
            diag_status = "越区切换 Handover (物理链路阻断)"
            diag_color = "warning"

        if sim_state["switching"]:
            sim_state["switching"] = False

        if not scan_state["complete"]:
            scan_state["progress"] = min(scan_state["progress"] + 115, scan_state["total"])

        telemetry = {
            "time": (datetime.utcnow() + __import__('datetime').timedelta(hours=8)).strftime("%H:%M:%S"),
            "orbit": {"alt": round(alt_deg, 2), "dist": round(dist_km, 1), "lat": round(sat_lat, 3), "lon": round(sat_lon, 3)},
            "phy": {"ta": round(residual_ta, 2), "cfo": round(residual_cfo, 0)},
            "net": {
                "delay":      round(base_delay_ms, 2),
                "jitter":     round(jitter_ms, 2),
                "loss":       round(loss_pct, 1),
                "throughput": round(est_tput, 1)
            },
            "diag": {"status": diag_status, "color": diag_color},
            "state": sim_state,
            "scan": {
                "scanning": scan_state["scanning"],
                "complete": scan_state["complete"],
                "progress": scan_state["progress"],
                "total": scan_state["total"],
                "current_name": scan_state["current_name"],
                "best_alt": round(scan_state["best_alt"], 1),
            },
            "satellite": {
                "name": sat_name,
                "epoch": sat_epoch,
                "index": (sim_state["current_sat_idx"] % len(top_sats)) + 1 if top_sats else 0,
                "total": len(top_sats),
                "source": data_source,
                "switching": sim_state["switching"],
            },
            "pool": visible_pool,
            "auto_switch": auto_switched,
            "real": real_metrics,
            "measurement_mode": measurement_mode,
            "handover": {
                "phase":         handover_state["phase"],
                "standby_name":  handover_state["standby_name"],
                "standby_alt":   handover_state["standby_alt"],
                "standby_delay": handover_state["standby_delay"],
                "standby_loss":  handover_state["standby_loss"],
            },
        }
        # ── 结构化日志采集（每 tick 记录，10Hz）──
        collector.record_tick(
            alt_deg=alt_deg, dist_km=dist_km,
            sat_lat=sat_lat, sat_lon=sat_lon, sat_name=sat_name,
            one_way_prop_ms=one_way_prop_ms, base_delay_ms=base_delay_ms,
            jitter_ms=jitter_ms, loss_pct=loss_pct,
            residual_ta=residual_ta, residual_cfo=residual_cfo,
            est_tput=est_tput, diag_color=diag_color,
        )

        await manager.broadcast(json.dumps(telemetry))
        await asyncio.sleep(1.0)


@app.on_event("startup")
async def startup_event():
    global r
    r = await aioredis.from_url("redis://localhost", decode_responses=True)
    asyncio.create_task(orbit_simulation_loop())
    asyncio.create_task(ping_measurement_loop())   # 方案A：真实Pi测量


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            cmd = json.loads(await websocket.receive_text())
            if "toggle_ai" in cmd:
                sim_state["edge_ai_enabled"] = not sim_state["edge_ai_enabled"]
            if "force_handover" in cmd:
                # Accept explicit bool value, or toggle if sent as True sentinel
                val = cmd["force_handover"]
                if isinstance(val, bool):
                    sim_state["force_handover"] = val
                else:
                    sim_state["force_handover"] = not sim_state["force_handover"]
                # Auto-reset handover state machine when force is cleared
                if not sim_state["force_handover"]:
                    handover_state.update({
                        "phase": "NORMAL", "standby_name": None,
                        "standby_alt": None, "standby_idx": None,
                        "pre_warm_published": False, "secondary_tick": 0,
                    })
            if "toggle_measurement_mode" in cmd:
                global measurement_mode
                measurement_mode = "real" if measurement_mode == "simulated" else "simulated"
                print(f"[Mode] 测量模式切换: {measurement_mode}")

            # ── 直接切换到可见池中的指定卫星（idx 指向 all_sats）──
            if "switch_to_sat" in cmd and all_sats:
                target_idx = int(cmd["switch_to_sat"])
                if 0 <= target_idx < len(all_sats):
                    # 将该卫星插入 top_sats[0] 位置并跳转到 idx 0
                    sat_obj = all_sats[target_idx]
                    if sat_obj not in top_sats:
                        top_sats.insert(0, sat_obj)
                    else:
                        top_sats.insert(0, top_sats.pop(top_sats.index(sat_obj)))
                    sim_state["current_sat_idx"] = 0
                    sim_state["switching"] = True

            # ── 切换到下一颗过境可见卫星 ──
            if "next_satellite" in cmd:
                if visible_pool:
                    # 找到当前跟踪卫星在池中的位置，循环取下一颗
                    curr_name = (top_sats[sim_state["current_sat_idx"] % len(top_sats)].name.strip()
                                 if top_sats else "")
                    pool_idx = next((i for i, p in enumerate(visible_pool) if p["name"] == curr_name), -1)
                    next_entry = visible_pool[(pool_idx + 1) % len(visible_pool)]
                    # 把目标星移到 top_sats 最前面
                    sat_obj = all_sats[next_entry["idx"]] if next_entry["idx"] < len(all_sats) else None
                    if sat_obj:
                        if sat_obj in top_sats:
                            top_sats.insert(0, top_sats.pop(top_sats.index(sat_obj)))
                        else:
                            top_sats.insert(0, sat_obj)
                        sim_state["current_sat_idx"] = 0
                        sim_state["switching"] = True
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.get("/")
async def get():
    with open("dashboard.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


# ══════════════════════════════════════════════════════════════════
#  Run-Control REST API  (for experiment_runner.py)
# ══════════════════════════════════════════════════════════════════
class RunStartBody(BaseModel):
    run_label: str
    edge_ai: bool = True
    real_measurement: bool = False


@app.post("/run/start")
async def api_run_start(body: RunStartBody):
    global measurement_mode
    run_id = (f"run_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
              f"_{body.run_label}")
    sim_state["edge_ai_enabled"] = body.edge_ai
    measurement_mode = "real" if body.real_measurement else "simulated"
    meta = {
        "ground_station": {
            "lat": GS_LAT, "lon": GS_LON, "alt_m": GS_ALT,
            "name": GS_NAME,
        },
        "tle_objects":      len(all_sats),
        "edge_ai_initial":  body.edge_ai,
        "measurement_mode": measurement_mode,
        "pid_params":       {"kp": 0.5, "ki": 0.1, "kd": 0.05},
        "model_params": {
            "ground_network_ms":  GROUND_NETWORK_MS,
            "fronthaul_sched_ms": FRONTHAUL_SCHED_MS,
            "processing_ms":      PROCESSING_MS,
            "min_elevation_deg":  MIN_ELEVATION_DEG,
            "ema_alpha":          0.15,
            "cp_tolerance_us":    CP_TOLERANCE_US,
            "scs_tolerance_hz":   SCS_TOLERANCE_HZ,
        },
    }
    collector.start_run(run_id, body.run_label, meta)
    return {"run_id": run_id, "status": "started", "edge_ai": body.edge_ai}


@app.post("/run/stop")
async def api_run_stop():
    rows = collector._row_count
    run_id = collector.stop_run()
    if run_id is None:
        return {"status": "no_active_run"}
    return {"run_id": run_id, "status": "stopped", "rows": rows}


@app.get("/run/status")
async def api_run_status():
    return {
        "active":        collector._active,
        "run_id":        collector.run_id,
        "run_label":     collector.run_label,
        "elapsed_s":     round(time.time() - collector._start_wall, 1)
                         if collector._active else 0,
        "rows":          collector._row_count,
        "scan_complete": scan_state["complete"],
        "scan_total":    scan_state["total"],
    }


if __name__ == '__main__':
    print(f"[Engine] Station: {GS_NAME}  lat={GS_LAT}°N  lon={GS_LON}°E  alt={GS_ALT}m")
    print(f"[Engine] Log dir: {LOG_ROOT.resolve()}")
    print(f"[Engine] Port:    {_args.port}")
    uvicorn.run(app, host="0.0.0.0", port=_args.port)
