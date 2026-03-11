#!/usr/bin/env python3
"""
NTN SAT-Node Worker  (部署在 Pi2 上)
=====================================
订阅 Ubuntu Redis，管理本地 tc netem 和 wg1 接口。

频道:
  ntn_link_state           → 应用 tc netem 到 wg0（Sat-A 主链路）
  ntn_link_state_secondary → 管理 wg1（Sat-B 预热备链路）

控制消息 (ntn_link_state_secondary):
  {"action": "prepare",  "delay":X, "jitter":Y, "loss":Z}  → 在 wg1 预设 Sat-B 参数
  {"action": "promote",  "delay":X, "jitter":Y, "loss":Z}  → 将 Sat-B 参数切换到 wg0（原子）
  {"action": "cleanup"}                                      → 清理 wg1

依赖:
  pip3 install redis
  sudoers: og ALL=(ALL) NOPASSWD: /usr/sbin/tc, /usr/bin/wg, /usr/bin/ip
"""

import os
import redis
import json
import subprocess
import time
import sys

REDIS_HOST = os.environ.get("REDIS_HOST", "10.100.0.1")  # Ubuntu server WireGuard IP
REDIS_PORT = 6379
RECONNECT_SEC = 5

PRIMARY_IFACE   = "wg0"
SECONDARY_IFACE = "wg1"

# ── wg1 config template — fill in your own keys before deploying ──
# Generate keys: wg genkey | tee pi2_private.key | wg pubkey > pi2_public.key
# See wg1_pi2.conf.example in this repo for the full template.
WG1_CONFIG = os.environ.get("WG1_CONFIG", """
[Interface]
Address = 10.100.1.3/24
ListenPort = 51821
PrivateKey = <PI2_PRIVATE_KEY>

[Peer]
PublicKey = <UBUNTU_PUBLIC_KEY>
Endpoint = <UBUNTU_WG_IP>:51821
AllowedIPs = 10.100.1.0/24
PersistentKeepalive = 25
""")

# ────────────────────────────────────────────────
def run(cmd, check=False):
    result = subprocess.run(
        ["sudo"] + cmd, capture_output=True, text=True
    )
    if result.returncode != 0 and check:
        print(f"[Worker] CMD FAIL: {' '.join(cmd)}")
        print(f"  stderr: {result.stderr.strip()}")
    return result.returncode == 0


def tc_apply(iface, delay_ms, jitter_ms, loss_pct):
    """原子地将 tc netem 应用到指定接口（先删后加）"""
    run(["tc", "qdisc", "del", "dev", iface, "root"])
    ok = run([
        "tc", "qdisc", "add", "dev", iface, "root", "netem",
        "delay", f"{delay_ms}ms", f"{jitter_ms}ms",
        "loss", f"{loss_pct}%",
        "limit", "10000"
    ], check=True)
    if ok:
        print(f"[Worker] tc netem → {iface}: delay={delay_ms}ms jitter={jitter_ms}ms loss={loss_pct}%")


def tc_clear(iface):
    run(["tc", "qdisc", "del", "dev", iface, "root"])
    print(f"[Worker] tc cleared → {iface}")


def wg1_up():
    """写 wg1 配置文件并拉起接口"""
    conf_path = "/tmp/wg1_pi2.conf"
    with open(conf_path, "w") as f:
        f.write(WG1_CONFIG.strip())
    # 若已存在则先拆掉
    run(["ip", "link", "del", "wg1"])
    ok = run(["wg-quick", "up", conf_path], check=True)
    if ok:
        print("[Worker] wg1 interface UP (Sat-B standby)")
    return ok


def wg1_down():
    conf_path = "/tmp/wg1_pi2.conf"
    run(["wg-quick", "down", conf_path])
    print("[Worker] wg1 interface DOWN")


# ────────────────────────────────────────────────
def handle_primary(data):
    """Sat-A 主链路：直接应用 tc netem 到 wg0"""
    try:
        tc_apply(PRIMARY_IFACE,
                 data.get("delay", 30),
                 data.get("jitter", 2),
                 data.get("loss",  0.1))
    except Exception as e:
        print(f"[Worker] primary handle error: {e}")


def handle_secondary(data):
    """Sat-B 预热备链路：管理 wg1 生命周期"""
    action = data.get("action", "")

    if action == "prepare":
        # 阶段一：拉起 wg1，应用 Sat-B 参数（预热，不承载流量）
        print(f"[Worker] PRE_WARM: bringing up wg1 for {data.get('sat_name','Sat-B')}")
        if wg1_up():
            time.sleep(1)   # 等待握手
            tc_apply(SECONDARY_IFACE,
                     data.get("delay", 30),
                     data.get("jitter", 2),
                     data.get("loss",  0.1))

    elif action == "update_secondary":
        # 持续更新 wg1 的 Sat-B 参数（随轨道变化）
        tc_apply(SECONDARY_IFACE,
                 data.get("delay", 30),
                 data.get("jitter", 2),
                 data.get("loss",  0.1))

    elif action == "promote":
        # 阶段三：原子切换 —— Sat-B 参数推入 wg0，wg1 退役
        print(f"[Worker] ATOMIC SWITCH: promoting Sat-B to wg0")
        tc_apply(PRIMARY_IFACE,
                 data.get("delay", 30),
                 data.get("jitter", 2),
                 data.get("loss",  0.1))
        wg1_down()

    elif action == "cleanup":
        # 阶段四：清理残留
        tc_clear(SECONDARY_IFACE)
        wg1_down()


# ────────────────────────────────────────────────
def main():
    print(f"[Worker] NTN SAT-Node worker starting, Redis → {REDIS_HOST}:{REDIS_PORT}")

    while True:
        try:
            r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT,
                            decode_responses=True,
                            socket_timeout=None,        # pubsub 长连接不超时
                            socket_connect_timeout=5)   # 仅连接阶段超时
            r.ping()
            print(f"[Worker] Connected to Redis at {REDIS_HOST}")

            pubsub = r.pubsub()
            pubsub.subscribe("ntn_link_state", "ntn_link_state_secondary")
            print("[Worker] Subscribed: ntn_link_state, ntn_link_state_secondary")

            for msg in pubsub.listen():
                if msg["type"] != "message":
                    continue
                try:
                    data = json.loads(msg["data"])
                except json.JSONDecodeError:
                    continue

                if msg["channel"] == "ntn_link_state":
                    handle_primary(data)
                elif msg["channel"] == "ntn_link_state_secondary":
                    handle_secondary(data)

        except (redis.ConnectionError, redis.TimeoutError) as e:
            print(f"[Worker] Redis connection lost: {e}, retrying in {RECONNECT_SEC}s...")
            time.sleep(RECONNECT_SEC)
        except KeyboardInterrupt:
            print("[Worker] Exiting")
            sys.exit(0)
        except Exception as e:
            print(f"[Worker] Unexpected error: {e}")
            time.sleep(RECONNECT_SEC)


if __name__ == "__main__":
    main()
