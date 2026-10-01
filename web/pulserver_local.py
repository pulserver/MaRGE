"""Keep pulserver's image running on this computer for MaRGE's page.

Run it with Python 3.8 or newer, which is all it needs, on Linux, macOS or
Windows::

    python pulserver-local.py

It answers MaRGE's page on ``http://127.0.0.1:8764``: whether Docker is
installed and running, whether pulserver's image is present and the newest
published, and whether its container runs with the settings below; it pulls
the image and (re)creates the container when the page asks.

The settings are the scanner's, which the console designs every sequence
under, and the directories of the user's own sequence and reconstruction
plugins. They are kept in ``~/.pulserver/scanner.txt`` (``PULSERVER_HOME``
names another directory), written with the defaults on the first run and
rewritten by the page; the file can be edited by hand as well. Its
``[Limits]`` block is the console's limits block, and its ``[Plugins]`` block
names the two directories, each mounted read-only into the container where
its plugins come before the image's own.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8764
CONSOLE_PORT = 8765
IMAGE = "ghcr.io/pulserver/pulserver"
CONTAINER = "pulserver"
PAGE = "https://pulserver.github.io/MaRGE/"
#: Pages allowed to drive this helper; a request from any other origin is refused.
ORIGINS = ("https://pulserver.github.io", "http://localhost:8000", "http://127.0.0.1:8000")
INSTALL_DOCKER = "https://docs.docker.com/get-started/get-docker/"
#: The label carrying the digest of the settings a container was created with.
CONFIG_LABEL = "org.pulserver.scanner"

#: The limits of a new settings file: pulserver's image's own, with the rasters
#: and dead times of ``pypulseqpp.Opts`` written out.
DEFAULT_LIMITS = {
    "B0": "3.0",
    "max_grad": "40.0",
    "grad_unit": "mT/m",
    "max_slew": "150.0",
    "slew_unit": "T/m/s",
    "grad_raster_time": "2e-05",
    "rf_raster_time": "2e-06",
    "adc_raster_time": "2e-06",
    "block_duration_raster": "2e-05",
    "rf_dead_time": "0.0",
    "rf_ringdown_time": "0.0",
    "adc_dead_time": "0.0",
}
#: Limits that are a positive number, and those that are a number not below zero.
POSITIVE = (
    "B0", "max_grad", "max_slew", "grad_raster_time", "rf_raster_time",
    "adc_raster_time", "block_duration_raster", "design_max_grad", "design_max_slew",
    "pns_chronaxie", "pns_limit",
)
NON_NEGATIVE = ("rf_dead_time", "rf_ringdown_time", "adc_dead_time")
UNITS = {
    "grad_unit": ("Hz/m", "mT/m", "rad/ms/mm"),
    "slew_unit": ("Hz/m/s", "mT/m/ms", "T/m/s", "rad/ms/mm/ms"),
}
PLUGINS = ("sequences", "recon")
#: Where each plugin directory is mounted; the image's console searches them first.
MOUNTS = {"sequences": "/console/user/plugins", "recon": "/console/user/recon"}
_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_BAND = re.compile(r"forbidden_band_(\d+)")


def home() -> Path:
    """Return the directory of the settings, ``~/.pulserver`` unless ``PULSERVER_HOME`` names one."""
    return Path(os.environ.get("PULSERVER_HOME") or Path.home() / ".pulserver")


# Settings ---------------------------------------------------------------------


def parse(text: str) -> dict:
    """Read a settings file: its ``[Limits]`` and ``[Plugins]`` blocks, as ``key: value`` lines."""
    blocks: dict = {"limits": {}, "plugins": {}}
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in ("[Limits]", "[Plugins]"):
            current = blocks[stripped[1:-1].lower()]
        elif stripped in ("[Limits End]", "[Plugins End]"):
            current = None
        elif current is not None and ":" in stripped:
            key, _, value = stripped.partition(":")
            current[key.strip()] = value.strip()
    return blocks


def render(settings: dict) -> str:
    """Write ``settings`` as the text :func:`parse` reads."""
    lines = ["[Limits]"]
    lines += [f"{key}: {value}" for key, value in settings["limits"].items()]
    lines += ["[Limits End]", "[Plugins]"]
    lines += [f"{key}: {settings['plugins'].get(key, '')}" for key in PLUGINS]
    lines += ["[Plugins End]"]
    return "\n".join(lines) + "\n"


def validate(settings: dict) -> dict:
    """Return ``settings`` checked and normalised, limits as strings and plugin paths absolute.

    Raises
    ------
    ValueError
        Naming every limit that is not a number where one is required, a unit
        that is not one of :data:`UNITS`, a forbidden band that is not an axis,
        two increasing frequencies in Hz and an optional amplitude, and a
        plugin directory that does not exist.
    """
    limits = {str(k).strip(): str(v).strip() for k, v in settings.get("limits", {}).items()}
    limits = {k: v for k, v in limits.items() if v != ""}
    problems = []
    for key, value in limits.items():
        if not _KEY.fullmatch(key) or "\n" in value:
            problems.append(f"{key!r} is not a limit name")
        elif key in POSITIVE or key in NON_NEGATIVE:
            try:
                number = float(value)
            except ValueError:
                number = math.nan
            if not math.isfinite(number) or number < 0 or (key in POSITIVE and number == 0):
                kind = "positive" if key in POSITIVE else "non-negative"
                problems.append(f"{key} must be a {kind} number: {value!r}")
        elif key in UNITS and value not in UNITS[key]:
            problems.append(f"{key} must be one of {', '.join(UNITS[key])}: {value!r}")
        elif _BAND.fullmatch(key):
            words = value.split()
            try:
                low, high = float(words[1]), float(words[2])
                fine = (
                    len(words) in (3, 4)
                    and words[0] in ("x", "y", "z", "all")
                    and 0 <= low < high
                    and (len(words) == 3 or float(words[3]) >= 0)
                )
            except (IndexError, ValueError):
                fine = False
            if not fine:
                problems.append(
                    f"{key} must be an axis (x, y, z or all), the lowest and highest "
                    f"frequency in Hz and optionally the largest amplitude: {value!r}"
                )
    for key in ("B0", "max_grad", "max_slew"):
        if key not in limits:
            problems.append(f"{key} is required")
    plugins = {}
    for key in PLUGINS:
        value = str(settings.get("plugins", {}).get(key, "") or "").strip()
        if value:
            path = Path(value).expanduser()
            if not path.is_dir():
                problems.append(f"the {key} plugin directory does not exist: {value}")
            value = str(path.resolve())
        plugins[key] = value
    if problems:
        raise ValueError("; ".join(problems))
    return {"limits": limits, "plugins": plugins}


def console_limits(settings: dict) -> str:
    """Return the console's ``[Limits]`` block from the settings.

    Bands are renumbered from 1 in their order. A design limit the settings
    leave out is the scanner's divided by the square root of three, as in
    pulserver's image: the amplitude and slew rate each logical axis may take
    whatever the prescription's rotation.
    """
    limits = {k: v for k, v in settings["limits"].items() if not _BAND.fullmatch(k)}
    for key in ("max_grad", "max_slew"):
        limits.setdefault(f"design_{key}", repr(float(limits[key]) / math.sqrt(3.0)))
    bands = sorted(
        (int(_BAND.fullmatch(k).group(1)), v)
        for k, v in settings["limits"].items()
        if _BAND.fullmatch(k)
    )
    for number, (_, value) in enumerate(bands, 1):
        limits[f"forbidden_band_{number}"] = value
    lines = [f"{key}: {value}" for key, value in limits.items()]
    return "\n".join(["[Limits]", *lines, "[Limits End]"]) + "\n"


def digest(settings: dict) -> str:
    """Return the digest of the settings a container is created with."""
    return hashlib.sha256(render(settings).encode()).hexdigest()[:16]


# The image's registry ---------------------------------------------------------


def published_digest(image: str, timeout: float = 15.0) -> str | None:
    """Return the digest the registry gives the image's tag, or None where it cannot be asked.

    An image named without a registry host, such as one built here, is not
    asked about. A registry that answers 401 is asked again with an anonymous
    token from the realm it names, as ``ghcr.io`` and Docker Hub do.
    """
    name, _, tag = image.rpartition(":") if ":" in image.split("/")[-1] else (image, "", "latest")
    host, _, repository = name.partition("/")
    if "." not in host and ":" not in host and host != "localhost":
        return None
    url = f"https://{host}/v2/{repository}/manifests/{tag}"
    accept = ", ".join((
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
    ))

    def head(token: str | None):
        headers = {"Accept": accept}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, headers=headers, method="HEAD")
        return urllib.request.urlopen(request, timeout=timeout)

    try:
        try:
            response = head(None)
        except urllib.error.HTTPError as error:
            challenge = error.headers.get("WWW-Authenticate", "")
            if error.code != 401 or not challenge.startswith("Bearer "):
                return None
            fields = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
            query = "&".join(f"{k}={fields[k]}" for k in ("service", "scope") if k in fields)
            with urllib.request.urlopen(f"{fields['realm']}?{query}", timeout=timeout) as answer:
                token = json.load(answer).get("token")
            response = head(token)
        with response:
            return response.headers.get("Docker-Content-Digest")
    except (OSError, ValueError, KeyError):
        return None


# Docker -----------------------------------------------------------------------


class Helper:
    """The state of Docker, the image and its container, and the tasks that change it.

    ``docker`` is the command line Docker is run with; ``published`` returns
    the image's published digest or None, and is asked at most every
    ``recheck`` seconds.
    """

    def __init__(
        self,
        image: str = IMAGE,
        container: str = CONTAINER,
        console_port: int = CONSOLE_PORT,
        docker: list | None = None,
        published=published_digest,
        recheck: float = 600.0,
    ):
        self.image, self.container, self.console_port = image, container, console_port
        self.docker = list(docker or ["docker"])
        self.published, self.recheck = published, recheck
        self.lock = threading.Lock()
        self.task: dict = {"name": None, "running": False, "log": [], "error": None}
        self._published: tuple = (0.0, None)
        self.path = home() / "scanner.txt"
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(render({"limits": dict(DEFAULT_LIMITS), "plugins": {}}))

    def settings(self) -> dict:
        settings = parse(self.path.read_text())
        settings["plugins"] = {k: settings["plugins"].get(k, "") for k in PLUGINS}
        return settings

    def run(self, *arguments: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
        return subprocess.run(
            [*self.docker, *arguments], capture_output=True, text=True, timeout=timeout,
        )

    def _inspect(self, kind: str, name: str) -> dict | None:
        result = self.run(kind, "inspect", name)
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)[0]

    def state(self) -> dict:
        """Return what the page shows: Docker, the image, the container, the task and the settings."""
        settings = self.settings()
        state = {
            "docker": {"installed": True, "running": False, "install": INSTALL_DOCKER},
            "image": {"name": self.image, "present": False, "current": None},
            "container": {"present": False, "running": False, "current": False, "log": ""},
            "console": f"ws://localhost:{self.console_port}",
            "task": dict(self.task, log=self.task["log"][-5:]),
            "settings": settings,
            "path": str(self.path),
        }
        if shutil.which(self.docker[0]) is None:
            state["docker"]["installed"] = False
            return state
        try:
            info = self.run("info", "--format", "{{.ServerVersion}}", timeout=20.0)
        except (OSError, subprocess.TimeoutExpired):
            return state
        if info.returncode != 0:
            return state
        state["docker"]["running"] = True
        image = self._inspect("image", self.image)
        if image is not None:
            local = [d.rpartition("@")[2] for d in image.get("RepoDigests") or []]
            published = self._published_digest()
            state["image"].update(
                present=True,
                id=image["Id"],
                current=None if published is None else published in local,
            )
        container = self._inspect("container", self.container)
        if container is not None:
            labels = container.get("Config", {}).get("Labels") or {}
            status = container.get("State", {})
            state["container"].update(
                present=True,
                running=bool(status.get("Running")) and not status.get("Restarting"),
                status=status.get("Status", ""),
                current=(
                    image is not None
                    and container.get("Image") == image["Id"]
                    and labels.get(CONFIG_LABEL) == digest(settings)
                ),
            )
            if not state["container"]["running"]:
                logs = self.run("logs", "--tail", "20", self.container)
                state["container"]["log"] = (logs.stdout + logs.stderr)[-4000:]
        return state

    def _published_digest(self) -> str | None:
        checked, value = self._published
        if value is None or time.monotonic() - checked > self.recheck:
            value = self.published(self.image)
            self._published = (time.monotonic(), value)
        return value

    def save(self, settings: dict) -> dict:
        """Write checked settings and, when the container exists, create it again with them."""
        settings = validate(settings)
        self.path.write_text(render(settings))
        if self._inspect("container", self.container) is not None:
            self.begin("start", self._create)
        return settings

    def begin(self, name: str, work) -> bool:
        """Run ``work`` on a thread of its own as the task ``name``; False while another runs."""
        with self.lock:
            if self.task["running"]:
                return False
            self.task = {"name": name, "running": True, "log": [], "error": None}

        def target():
            try:
                work()
            except Exception as error:  # reported to the page, which shows it
                self.task["error"] = str(error)
            finally:
                self.task["running"] = False

        threading.Thread(target=target, daemon=True).start()
        return True

    def _say(self, line: str) -> None:
        self.task["log"].append(line.rstrip())

    def _stream(self, *arguments: str) -> None:
        process = subprocess.Popen(
            [*self.docker, *arguments],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        for line in process.stdout:
            self._say(line)
        if process.wait() != 0:
            raise RuntimeError(self.task["log"][-1] if self.task["log"] else f"docker {arguments[0]} failed")

    def pull(self) -> None:
        """Pull the image's newest version, then create the container on it."""
        self._stream("pull", self.image)
        self._published = (0.0, None)
        self._create()

    def _create(self) -> None:
        """Create the container afresh on the image, with the settings' limits and plugin directories."""
        settings = self.settings()
        limits = self.path.parent / "console-limits.txt"
        limits.write_text(console_limits(settings))
        mounts = [f"type=bind,source={limits},target=/console/limits.txt,readonly"]
        for key, target in MOUNTS.items():
            if settings["plugins"][key]:
                mounts.append(f"type=bind,source={settings['plugins'][key]},target={target},readonly")
        self._say(f"Creating the container {self.container}")
        self.run("rm", "-f", self.container)
        arguments = [
            "run", "-d", "--restart", "unless-stopped", "--name", self.container,
            "-p", f"127.0.0.1:{self.console_port}:8765",
            "--label", f"{CONFIG_LABEL}={digest(settings)}",
        ]
        for mount in mounts:
            arguments += ["--mount", mount]
        result = self.run(*arguments, self.image)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "docker run failed")
        self._say(f"Started {self.container}")


# HTTP -------------------------------------------------------------------------


def handler(helper: Helper, origins: tuple, port: int):
    """Return the request handler answering the page for ``helper``.

    A request is refused unless its ``Host`` is this computer at ``port``,
    which a page reached by another name cannot forge, and its ``Origin``,
    when it has one, is among ``origins``. Changes are POSTed as JSON, which
    a browser sends cross-origin only after a preflight this handler answers
    for ``origins`` alone.
    """
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "pulserver-local"

        def log_message(self, format, *args):
            pass

        def _allowed(self) -> bool:
            origin = self.headers.get("Origin")
            if self.headers.get("Host") not in hosts or (origin is not None and origin not in origins):
                self._send(403, {"error": "not an allowed page"})
                return False
            return True

        def _send(self, code: int, body: dict | None = None) -> None:
            data = json.dumps(body).encode() if body is not None else b""
            self.send_response(code)
            origin = self.headers.get("Origin")
            if origin in origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            if body is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self):
            if not self._allowed():
                return
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", self.headers["Origin"] or "")
            self.send_header("Access-Control-Allow-Methods", "GET, POST")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Vary", "Origin")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            if not self._allowed():
                return
            if self.path == "/state":
                self._send(200, helper.state())
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._allowed():
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self._send(415, {"error": "send JSON"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._send(400, {"error": "not JSON"})
                return
            if self.path == "/settings":
                try:
                    settings = helper.save(body)
                except ValueError as error:
                    self._send(400, {"error": str(error)})
                    return
                self._send(200, {"settings": settings})
            elif self.path in ("/pull", "/start"):
                work = helper.pull if self.path == "/pull" else helper._create
                if helper.begin(self.path[1:], work):
                    self._send(202, {"task": self.path[1:]})
                else:
                    self._send(409, {"error": f"{helper.task['name']} is running"})
            else:
                self._send(404, {"error": "not found"})

    return Handler


def serve(helper: Helper, port: int = PORT, origins: tuple = ORIGINS) -> ThreadingHTTPServer:
    """Return the server answering the page for ``helper`` on ``127.0.0.1:port``, not yet serving."""
    server = ThreadingHTTPServer(("127.0.0.1", port), handler(helper, tuple(origins), port))
    server.daemon_threads = True
    return server


def main(argv: list | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=PORT, help="port the page asks this helper on")
    parser.add_argument("--image", default=IMAGE, help="pulserver's image")
    parser.add_argument("--console-port", type=int, default=CONSOLE_PORT, help="port of the console")
    parser.add_argument("--origin", action="append", default=[], help="another page allowed, repeatable")
    parser.add_argument("--page", default=PAGE, help="MaRGE's page, opened once the helper answers")
    parser.add_argument("--no-browser", action="store_true", help="do not open the page")
    args = parser.parse_args(argv)

    helper = Helper(image=args.image, console_port=args.console_port)
    try:
        server = serve(helper, args.port, (*ORIGINS, *args.origin))
    except OSError as error:
        sys.exit(f"Port {args.port} is taken, perhaps by this helper already running: {error}")
    print(f"pulserver's helper answers MaRGE's page on http://127.0.0.1:{args.port}")
    print(f"Settings: {helper.path}")
    print("Leave this window open while you use the page; Ctrl+C stops the helper.")
    if not args.no_browser:
        webbrowser.open(args.page)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
