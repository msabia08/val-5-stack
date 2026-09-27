"""Cloudflare Tunnel runner.

Downloads `cloudflared` on first use (Windows x64, Linux x64/arm64), starts
either a *quick* tunnel (random https://....trycloudflare.com address, no
account needed) or a dashboard-managed tunnel (token + your own hostname),
captures the public URL, and restarts cloudflared if it dies.
"""
import os
import platform
import re
import shutil
import stat
import subprocess
import threading
import time
import urllib.request
from collections import deque

RELEASES = "https://github.com/cloudflare/cloudflared/releases/latest/download/"
ASSETS = {
    ("windows", "amd64"): "cloudflared-windows-amd64.exe",
    ("windows", "x86_64"): "cloudflared-windows-amd64.exe",
    ("linux", "x86_64"): "cloudflared-linux-amd64",
    ("linux", "amd64"): "cloudflared-linux-amd64",
    ("linux", "aarch64"): "cloudflared-linux-arm64",
    ("linux", "arm64"): "cloudflared-linux-arm64",
}
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
DOCS = "https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"


class TunnelError(Exception):
    pass


def _kill_with_parent(proc):
    """Windows: put the child in a job object that is killed when this process exits for any reason,
    so closing the console window never leaves an orphaned cloudflared behind. Best effort."""
    if platform.system().lower() != "windows":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # 9 = ExtendedLimitInformation
            return None
        if not k32.AssignProcessToJobObject(job, wintypes.HANDLE(proc._handle)):
            return None
        return job  # keep the handle alive for as long as the tunnel object lives
    except Exception:  # noqa: BLE001
        return None


class Tunnel:
    def __init__(self, cfg, port, tools_dir):
        self.port = int(port)
        self.tools_dir = tools_dir
        self.token = (cfg.get("tunnel_token") or "").strip()
        host = (cfg.get("tunnel_hostname") or "").strip().rstrip("/")
        if "://" in host:
            host = host.split("://", 1)[1]
        self.hostname = host
        self.mode = "off"
        self.proc = None
        self._job = None
        self.stopping = False
        self.logs = deque(maxlen=40)
        self.state = {"mode": "off", "status": "off", "url": None, "error": None, "since": None}

    def log(self, msg):
        self.logs.append({"ts": time.time(), "msg": msg})
        print(f"[tunnel] {msg}", flush=True)

    # ---- binary ----------------------------------------------------------
    def binary(self):
        found = shutil.which("cloudflared")
        if found:
            return found
        is_win = platform.system().lower() == "windows"
        local = os.path.join(self.tools_dir, "cloudflared.exe" if is_win else "cloudflared")
        if os.path.exists(local):
            return local
        key = (platform.system().lower(), platform.machine().lower())
        asset = ASSETS.get(key)
        if not asset:
            raise TunnelError(
                f"cloudflared is not installed and there is no automatic download for {key[0]}/{key[1]}. "
                f"Install it from {DOCS}"
            )
        os.makedirs(self.tools_dir, exist_ok=True)
        self.log(f"Downloading cloudflared ({asset}), this takes a moment...")
        tmp = local + ".part"
        try:
            with urllib.request.urlopen(RELEASES + asset, timeout=90) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f)
            os.replace(tmp, local)
        except Exception as e:  # noqa: BLE001
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise TunnelError(f"Could not download cloudflared: {e}. Install it manually from {DOCS}")
        if not is_win:
            os.chmod(local, os.stat(local).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self.log("cloudflared downloaded")
        return local

    # ---- lifecycle -------------------------------------------------------
    def start(self, mode):
        self.mode = (mode or "off").lower()
        self.state["mode"] = self.mode
        if self.mode == "off":
            return
        threading.Thread(target=self._run, name="tunnel", daemon=True).start()

    def stop(self):
        self.stopping = True
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass

    def _args(self, exe):
        base = [exe, "tunnel", "--no-autoupdate"]
        if self.mode == "quick":
            return base + ["--url", f"http://127.0.0.1:{self.port}"]
        if self.mode == "token":
            if not self.token:
                raise TunnelError('tunnel is set to "token" but tunnel_token is empty in config.json.')
            return base + ["run", "--token", self.token]
        raise TunnelError(f"Unknown tunnel mode '{self.mode}' (use off, quick or token).")

    def _run(self):
        try:
            exe = self.binary()
            args = self._args(exe)
        except TunnelError as e:
            self.state.update({"status": "error", "error": str(e)})
            self.log(str(e))
            return
        backoff = 5
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        while not self.stopping:
            fixed_url = f"https://{self.hostname}" if (self.mode == "token" and self.hostname) else None
            self.state.update({"status": "starting", "error": None, "url": fixed_url})
            self.log("Starting cloudflared" + (" (quick tunnel)" if self.mode == "quick" else ""))
            try:
                self.proc = subprocess.Popen(
                    args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    encoding="utf-8", errors="replace", creationflags=flags,
                )
            except OSError as e:
                self.state.update({"status": "error", "error": f"Could not start cloudflared: {e}"})
                self.log(self.state["error"])
                return
            self._job = _kill_with_parent(self.proc)
            started = time.time()
            for raw in self.proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                m = URL_RE.search(line)
                if m and self.mode == "quick":
                    self.state.update({"url": m.group(0), "status": "up", "since": time.time(), "error": None})
                    self.log(f"Public URL: {m.group(0)}")
                elif "Registered tunnel connection" in line:
                    if self.state["status"] != "up":
                        self.state.update({"status": "up", "since": time.time(), "error": None})
                        self.log("Tunnel connected" + (f": {fixed_url}" if fixed_url else ""))
                elif " ERR " in line:
                    text = re.sub(r"^\S+\s+ERR\s+", "", line)[:300]
                    self.state["error"] = text
                    self.log(text)
            rc = self.proc.wait()
            if self.stopping:
                break
            if time.time() - started > 60:
                backoff = 5
            self.state.update({
                "status": "error",
                "error": self.state.get("error") or f"cloudflared exited with code {rc}",
                "url": fixed_url,
            })
            self.log(f"cloudflared exited ({rc}); restarting in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)
        self.state.update({"status": "off", "url": None})
