import redis
import json
import paramiko
import threading

PI1_HOST = '172.31.18.37'
PI2_HOST = '172.31.18.38'
PI_USER = 'og'
PI_PASS = 'Ogcloud123'


def ssh_tc(host, delay, jitter, loss):
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(host, username=PI_USER, password=PI_PASS, timeout=5)
        cmd = (
            f"sudo tc qdisc change dev wg0 root handle 1: netem "
            f"delay {delay:.2f}ms {jitter:.2f}ms loss {loss:.1f}%"
        )
        stdin, stdout, stderr = ssh.exec_command(cmd)
        stdout.channel.recv_exit_status()
        ssh.close()
    except Exception as e:
        print(f"[VLM] SSH tc error on {host}: {e}")


def init_tc(host):
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(host, username=PI_USER, password=PI_PASS, timeout=5)
        ssh.exec_command("sudo tc qdisc del dev wg0 root 2>/dev/null")
        ssh.exec_command("sudo tc qdisc add dev wg0 root handle 1: netem delay 1ms")
        ssh.close()
        print(f"[VLM] {host} tc 基线队列初始化完成")
    except Exception as e:
        print(f"[VLM] 初始化 {host} tc 失败: {e}")


def main():
    print("[VLM] 数据面执行器启动 (SSH 远程 tc 模式)...")

    for host in [PI1_HOST, PI2_HOST]:
        init_tc(host)

    r = redis.Redis(host='localhost', port=6379, db=0)
    pubsub = r.pubsub()
    pubsub.subscribe('ntn_link_state')
    print("[VLM] 监听 Redis ntn_link_state 频道...")

    for message in pubsub.listen():
        if message['type'] == 'message':
            state = json.loads(message['data'])
            delay = state['delay']
            jitter = state['jitter']
            loss = state['loss']
            print(f"[VLM] 下发 → delay={delay:.2f}ms jitter={jitter:.2f}ms loss={loss:.1f}%")
            t1 = threading.Thread(target=ssh_tc, args=(PI1_HOST, delay, jitter, loss))
            t2 = threading.Thread(target=ssh_tc, args=(PI2_HOST, delay, jitter, loss))
            t1.start()
            t2.start()
            t1.join()
            t2.join()


if __name__ == '__main__':
    main()
