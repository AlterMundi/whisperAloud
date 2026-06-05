"""Best-effort hardware provenance capture. Missing tools -> None, never crash."""
from __future__ import annotations

import os
import platform
import re
import shutil
import socket
import subprocess


def parse_cpu_model(cpuinfo: str) -> str | None:
    for line in cpuinfo.splitlines():
        if line.lower().startswith("model name"):
            return line.split(":", 1)[1].strip()
    return None


def parse_nvidia_smi(out: str) -> tuple[str | None, int | None]:
    line = out.strip().splitlines()[0] if out.strip() else ""
    if not line:
        return (None, None)
    parts = [p.strip() for p in line.split(",")]
    name = parts[0] if parts else None
    vram_gb = None
    if len(parts) > 1:
        m = re.search(r"(\d+)", parts[1])
        if m:
            vram_gb = round(int(m.group(1)) / 1024)
    return (name, vram_gb)


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return ""


def probe(hw_id: str | None = None) -> dict:
    cpuinfo = ""
    try:
        with open("/proc/cpuinfo") as f:
            cpuinfo = f.read()
    except OSError:
        pass
    cpu = parse_cpu_model(cpuinfo)

    ram_gb = None
    try:
        ram_gb = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9)
    except (ValueError, OSError):
        pass

    gpu, vram_gb = (None, None)
    if shutil.which("nvidia-smi"):
        gpu, vram_gb = parse_nvidia_smi(
            _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])
        )

    return {
        "id": hw_id or socket.gethostname(),
        "kind": "gpu" if gpu else "cpu",
        "gpu": gpu,
        "vram_gb": vram_gb,
        "cpu": cpu,
        "cores": os.cpu_count(),
        "ram_gb": ram_gb,
        "os": f"{platform.system()} {platform.release()}",
    }
