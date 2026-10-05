"""The launchers MaRGE's page offers, against a stand-in for Docker.

The shell launcher runs where ``sh`` does, the batch launcher on Windows.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web"
IMAGE = "ghcr.io/pulserver/pulserver"

#: A stand-in for the docker command line, keeping the local image, the
#: published one and every call in the JSON file DOCKER_STATE names.
FAKE = r'''
import json, os, sys
path = os.environ["DOCKER_STATE"]
state = json.load(open(path))
argv = sys.argv[1:]
state["calls"].append(argv)
def done(code=0, out=""):
    json.dump(state, open(path, "w"))
    sys.stdout.write(out)
    sys.exit(code)
if not state["daemon"]:
    done(1)
if argv[0] == "info":
    done()
if argv[:2] == ["image", "inspect"]:
    done(0, state["local"] + "\n") if state["local"] else done(1)
if argv[0] == "pull":
    if not state["published"]:
        done(1)
    state["local"] = state["published"]
    done()
if argv[0] in ("rm", "run"):
    done()
done(1)
'''

LIMITS = ["B0: 0.55", "max_grad: 40.0", "forbidden_band_1: all 500 600", "pns_chronaxie: 0.00036"]


def _filled(name, home, sequences=""):
    """Return the launcher ``name`` filled as the page fills it."""
    text = (WEB / name).read_text()
    if name.endswith(".sh"):
        limits = "\n".join(LIMITS)
    else:
        limits = "\n".join(f'>>"%LIMITS%" echo {line}' for line in LIMITS)
    return (text.replace("@LIMITS@", limits).replace("@PAGE@", "")
            .replace("@SEQUENCES@", sequences).replace("@RECON@", ""))


def _run(tmp_path, local, published, daemon=True, sequences=""):
    state = tmp_path / "docker.json"
    state.write_text(json.dumps({"daemon": daemon, "local": local, "published": published, "calls": []}))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "docker.py").write_text(FAKE)
    home = tmp_path / "home"
    if os.name == "nt":
        (bin_dir / "docker.cmd").write_text(f'@"{sys.executable}" "%~dp0docker.py" %*\r\n')
        script = tmp_path / "pulserver.bat"
        script.write_text(_filled("pulserver.bat", home, sequences).replace("\n", "\r\n"), newline="")
        command = ["cmd", "/c", str(script)]
    else:
        docker = bin_dir / "docker"
        docker.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{bin_dir / "docker.py"}" "$@"\n')
        docker.chmod(0o755)
        script = tmp_path / "pulserver.sh"
        script.write_text(_filled("pulserver.sh", home, sequences))
        command = ["sh", str(script)]
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
           "DOCKER_STATE": str(state), "PULSERVER_HOME": str(home)}
    done = subprocess.run(command, env=env, input="", capture_output=True, text=True, timeout=60)
    return done, json.loads(state.read_text())["calls"], home


needs_shell = pytest.mark.skipif(os.name != "nt" and shutil.which("sh") is None, reason="needs sh")


@needs_shell
def test_an_absent_image_is_installed_and_started_with_the_page_s_limits(tmp_path):
    done, calls, home = _run(tmp_path, None, "sha256:new")

    assert done.returncode == 0, done.stdout + done.stderr
    assert "pulserver is not installed" in done.stdout
    assert "pulserver is installed." in done.stdout
    assert ["pull", IMAGE] in calls
    run = calls[-1]
    assert run[0] == "run" and run[-1] == IMAGE
    assert f"{home / 'limits.txt'}:/console/limits.txt:ro" in run
    limits = (home / "limits.txt").read_text().splitlines()
    assert [line.strip() for line in limits] == ["[Limits]", *LIMITS, "[Limits End]"]


@needs_shell
def test_an_outdated_image_is_updated(tmp_path):
    done, calls, _ = _run(tmp_path, "sha256:old", "sha256:new")

    assert done.returncode == 0, done.stdout + done.stderr
    assert "pulserver was outdated and is updated." in done.stdout


@needs_shell
def test_an_image_up_to_date_is_started_as_it_is(tmp_path):
    done, calls, _ = _run(tmp_path, "sha256:new", "sha256:new")

    assert done.returncode == 0, done.stdout + done.stderr
    assert "pulserver is up to date." in done.stdout
    assert calls[-1][0] == "run"


@needs_shell
def test_an_installed_image_starts_when_the_registry_does_not_answer(tmp_path):
    done, calls, _ = _run(tmp_path, "sha256:old", None)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "could not be checked" in done.stdout
    assert calls[-1][0] == "run"


@needs_shell
def test_docker_not_running_stops_before_anything_is_pulled(tmp_path):
    done, calls, _ = _run(tmp_path, None, "sha256:new", daemon=False)

    assert done.returncode == 1
    assert "not running" in done.stdout
    assert not any(call[0] == "pull" for call in calls)


@needs_shell
def test_a_plugin_directory_is_mounted_where_the_console_searches_first(tmp_path):
    sequences = tmp_path / "my sequences"
    sequences.mkdir()
    done, calls, _ = _run(tmp_path, "sha256:new", "sha256:new", sequences=str(sequences))

    assert done.returncode == 0, done.stdout + done.stderr
    assert f"{sequences}:/console/user/plugins:ro" in calls[-1]
