"""Bounded, single-node CPU test of the complete LiteRegistry Podman path."""

import json
import os
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import redis


def main():
    results = Path(os.environ.get("REXS_RESULTS", "/results"))
    results.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=f"rexs-podman-{os.getuid()}-"))
    processes = []
    logs = []
    report = {"hostname": socket.gethostname(), "scratch": str(scratch), "checks": {}}
    os.environ.update(
        XDG_RUNTIME_DIR=str(scratch / "run"),
        XDG_DATA_HOME=str(scratch / "data"),
        XDG_CONFIG_HOME=str(scratch / "config"),
        CONTAINERS_STORAGE_CONF=str(scratch / "storage.conf"),
        CONTAINERS_CONF=str(scratch / "containers.conf"),
    )
    (scratch / "run").mkdir(mode=0o700)
    (scratch / "bin").mkdir()
    # LiteRegistry explicitly passes --storage-driver, which resets options
    # loaded from storage.conf in Podman 4.3. Supply the VFS option explicitly.
    podman_wrapper = scratch / "bin" / "podman"
    podman_wrapper.write_text(
        '#!/bin/sh\nexec /usr/bin/podman --storage-opt=vfs.ignore_chown_errors=true "$@"\n'
    )
    podman_wrapper.chmod(0o700)
    os.environ["PATH"] = f'{scratch}/bin:{os.environ["PATH"]}'
    (scratch / "storage.conf").write_text(
        f'[storage]\ndriver="vfs"\nrunroot="{scratch}/run/storage"\n'
        f'graphroot="{scratch}/data/storage"\n'
        '[storage.options.vfs]\nignore_chown_errors="true"\n'
    )
    (scratch / "containers.conf").write_text(
        '[containers]\ncgroups="disabled"\nvolumes=["/dev/pts:/dev/pts"]\n'
        '[engine]\ncgroup_manager="cgroupfs"\nevents_logger="file"\n'
        f'events_logfile_path="{scratch}/events.log"\ntmp_dir="{scratch}/libpod"\n'
    )

    def start(name, args):
        log = (results / f"{name}.log").open("w")
        logs.append(log)
        process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        processes.append(process)
        return process

    def request(url, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as response:
            body = response.read()
            return json.loads(body) if body else {}

    def wait(check, timeout=180):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            for process in processes:
                if process.poll() is not None:
                    raise RuntimeError(f"service exited with {process.returncode}; inspect service logs")
            try:
                return check()
            except (OSError, ValueError, redis.RedisError) as exc:
                last = exc
                time.sleep(1)
        raise TimeoutError(f"service readiness timed out: {last}")

    sockets = []
    for _ in range(4):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sockets.append(sock)
    redis_port, gateway_port, mirror_port, podman_port = [s.getsockname()[1] for s in sockets]
    for sock in sockets:
        sock.close()
    registry = f"redis://127.0.0.1:{redis_port}"
    gateway = f"http://127.0.0.1:{gateway_port}"
    report["gateway"] = gateway
    session = None
    try:
        start("redis", ["redis-server", "--bind", "127.0.0.1", "--port", str(redis_port),
                        "--save", "", "--appendonly", "no", "--dir", str(scratch)])
        client = redis.Redis.from_url(registry)
        wait(client.ping)
        report["checks"]["redis"] = "passed"
        start("gateway", ["literegistry", "gateway", f"--registry={registry}",
                          "--host=127.0.0.1", f"--port={gateway_port}", "--workers=1"])
        wait(lambda: request(gateway + "/health"))
        report["checks"]["gateway_health"] = "passed"
        start("mirror", ["python", "-m", "literegistry.services.docker_mirror_server",
                         f"--registry={registry}", "--host=127.0.0.1", f"--port={mirror_port}",
                         "--advertise_host=127.0.0.1", f"--advertise_port={mirror_port}",
                         f"--distribution_config={scratch}/distribution.yml",
                         f"--storage_root={scratch}/mirror"])
        wait(lambda: request(gateway + "/v2/"))
        report["checks"]["gateway_registry_v2"] = "passed"
        with (results / "podman-info.log").open("w") as log:
            subprocess.run(["podman", "info", "--debug"], stdout=log, stderr=subprocess.STDOUT,
                           check=True, timeout=30)
        report["checks"]["nested_podman_info"] = "passed"
        start("podman", ["literegistry", "podman", f"--registry={registry}",
                         "--host=127.0.0.1", f"--port={podman_port}",
                         "--advertise_host=127.0.0.1", f"--advertise_port={podman_port}",
                         f"--registry_mirror={gateway}", "--network=none", "--max_sessions=2",
                         "--instance_id=rexs-smoke", "--session_idle_timeout=120"])
        wait(lambda: request(f"http://127.0.0.1:{podman_port}/health"))
        time.sleep(2)
        handshake = request(gateway + "/affinity/handshake", {
            "service": "podman", "image": "docker.io/library/ubuntu:24.04"})
        session = handshake.get("affinity_id") or handshake["container_id"]
        report["checks"]["handshake"] = "passed"
        for command, expected in [("printf rexs-cpu-ok > /workspace/rexs.txt", ""),
                                  ("cat /workspace/rexs.txt", "rexs-cpu-ok")]:
            response = request(gateway + "/affinity/podman", {
                "service": "podman", "affinity_id": session, "command": command, "timeout": 30})
            if response["exit_code"] != 0 or response["stdout"] != expected:
                raise RuntimeError(f"unexpected command response: {response}")
        report["checks"]["stateful_command_execution"] = "passed"
        request(gateway + "/affinity/close", {"service": "podman", "affinity_id": session})
        session = None
        report["checks"]["close"] = "passed"
        report["status"] = "passed"
    except BaseException as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
        raise
    finally:
        if session:
            try:
                request(gateway + "/affinity/close", {"service": "podman", "affinity_id": session})
            except (OSError, ValueError) as exc:
                report["cleanup_error"] = str(exc)
        for process in reversed(processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        for log in logs:
            log.close()
        (results / "smoke-report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
