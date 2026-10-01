"""The helper that runs pulserver's image for MaRGE's page, against a stand-in for Docker."""

import importlib.util
import json
import math
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("pulserver_local", ROOT / "web" / "pulserver_local.py")
local = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(local)

PUBLISHED = "sha256:" + "1" * 64
OLDER = "sha256:" + "0" * 64

#: A stand-in for the docker command line, keeping its images and containers in
#: the JSON file named by its first argument and logging every call there.
FAKE = r'''
import json, sys
path, *argv = sys.argv[1:]
state = json.load(open(path))
state["calls"].append(argv)
def done(code=0, out=""):
    json.dump(state, open(path, "w"))
    sys.stdout.write(out)
    sys.exit(code)
if not state["daemon"]:
    done(1)
if argv[0] == "info":
    done(0, "27.0.0\n")
if argv[:2] == ["image", "inspect"]:
    image = state["images"].get(argv[2])
    done(0, json.dumps([image])) if image else done(1)
if argv[:2] == ["container", "inspect"]:
    container = state["containers"].get(argv[2])
    done(0, json.dumps([container])) if container else done(1)
if argv[0] == "logs":
    done(0, "the console's last words\n")
if argv[0] == "pull":
    print("Pulling from pulserver/pulserver")
    state["images"][argv[1]] = {"Id": "sha256:new", "RepoDigests": [argv[1] + "@" + state["published"]]}
    done(0, "Status: Downloaded newer image\n")
if argv[0] == "rm":
    state["containers"].pop(argv[2], None)
    done()
if argv[0] == "run":
    name = argv[argv.index("--name") + 1]
    label = argv[argv.index("--label") + 1].split("=", 1)[1]
    state["containers"][name] = {
        "Image": state["images"][argv[-1]]["Id"],
        "Config": {"Labels": {"org.pulserver.scanner": label}},
        "State": {"Running": True, "Restarting": False, "Status": "running"},
    }
    done(0, "0123abcd\n")
done(2)
'''


@pytest.fixture
def docker(tmp_path, monkeypatch):
    """The stand-in's state file, with Docker running, no image and no container."""
    monkeypatch.setenv("PULSERVER_HOME", str(tmp_path / "home"))
    script = tmp_path / "docker.py"
    script.write_text(FAKE)
    state = tmp_path / "docker.json"
    state.write_text(json.dumps({
        "daemon": True, "images": {}, "containers": {}, "calls": [], "published": PUBLISHED,
    }))
    return state


def _helper(state, **kwargs):
    return local.Helper(
        docker=[sys.executable, str(state.parent / "docker.py"), str(state)],
        published=lambda image: PUBLISHED,
        **kwargs,
    )


def _wait(helper, timeout=30.0):
    deadline = time.monotonic() + timeout
    while helper.task["running"]:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert helper.task["error"] is None


def _calls(state):
    return json.loads(state.read_text())["calls"]


def test_a_first_run_writes_the_settings_file_with_the_images_scanner(docker):
    helper = _helper(docker)

    settings = local.parse(helper.path.read_text())

    assert helper.path == docker.parent / "home" / "scanner.txt"
    assert settings["limits"] == local.DEFAULT_LIMITS
    assert settings["plugins"] == {"sequences": "", "recon": ""}


def test_docker_that_is_not_installed_points_to_its_installation(docker, tmp_path):
    helper = local.Helper(docker=[str(tmp_path / "nowhere" / "docker")])

    state = helper.state()

    assert state["docker"] == {"installed": False, "running": False, "install": local.INSTALL_DOCKER}


def test_docker_that_is_not_running_is_reported_as_installed_and_stopped(docker):
    docker.write_text(json.dumps({**json.loads(docker.read_text()), "daemon": False}))

    state = _helper(docker).state()

    assert state["docker"]["installed"] and not state["docker"]["running"]


def test_pulling_a_missing_image_creates_the_container_with_the_settings(docker):
    helper = _helper(docker)
    before = helper.state()

    assert helper.begin("pull", helper.pull)
    _wait(helper)
    after = helper.state()

    assert not before["image"]["present"] and not before["container"]["present"]
    assert after["image"]["present"] and after["image"]["current"] is True
    assert after["container"]["running"] and after["container"]["current"]
    run = next(call for call in _calls(docker) if call[0] == "run")
    assert run[-1] == local.IMAGE
    assert ["-p", "127.0.0.1:8765:8765"] == run[run.index("-p"):run.index("-p") + 2]
    limits = docker.parent / "home" / "console-limits.txt"
    assert f"type=bind,source={limits},target=/console/limits.txt,readonly" in run


def test_an_image_older_than_the_published_one_is_not_current(docker):
    state = json.loads(docker.read_text())
    state["images"][local.IMAGE] = {"Id": "sha256:old", "RepoDigests": [f"{local.IMAGE}@{OLDER}"]}
    docker.write_text(json.dumps(state))

    image = _helper(docker).state()["image"]

    assert image["present"] and image["current"] is False


def test_an_image_whose_newest_version_cannot_be_asked_for_is_neither_current_nor_old(docker):
    state = json.loads(docker.read_text())
    state["images"][local.IMAGE] = {"Id": "sha256:old", "RepoDigests": []}
    docker.write_text(json.dumps(state))
    helper = _helper(docker)
    helper.published = lambda image: None

    assert helper.state()["image"]["current"] is None


def test_saving_settings_recreates_a_container_with_them_and_mounts_the_plugin_directories(docker, tmp_path):
    helper = _helper(docker)
    helper.begin("pull", helper.pull)
    _wait(helper)
    sequences, recon = tmp_path / "my seq", tmp_path / "recon"
    sequences.mkdir()
    recon.mkdir()
    settings = helper.settings()
    settings["limits"].update(B0="0.55", forbidden_band_7="all 500 600", forbidden_band_2="z 1100 1300 5")
    settings["plugins"] = {"sequences": str(sequences), "recon": str(recon)}

    helper.save(settings)
    _wait(helper)
    run = [call for call in _calls(docker) if call[0] == "run"][-1]

    assert f"type=bind,source={sequences},target=/console/user/plugins,readonly" in run
    assert f"type=bind,source={recon},target=/console/user/recon,readonly" in run
    assert helper.state()["container"]["current"]
    assert helper.settings()["plugins"] == {"sequences": str(sequences), "recon": str(recon)}


def test_a_container_running_other_settings_is_not_current(docker):
    helper = _helper(docker)
    helper.begin("pull", helper.pull)
    _wait(helper)
    settings = helper.settings()
    settings["limits"]["B0"] = "1.5"
    helper.path.write_text(local.render(settings))

    assert not helper.state()["container"]["current"]


def test_the_console_limits_derive_the_design_limits_and_renumber_the_bands():
    settings = {
        "limits": {"B0": "3", "max_grad": "40", "max_slew": "150",
                   "forbidden_band_9": "x 1 2", "forbidden_band_3": "all 3 4 0.5"},
        "plugins": {},
    }

    lines = local.console_limits(settings).splitlines()
    limits = dict(line.split(": ", 1) for line in lines[1:-1])

    assert lines[0] == "[Limits]" and lines[-1] == "[Limits End]"
    assert float(limits["design_max_grad"]) == pytest.approx(40 / math.sqrt(3))
    assert float(limits["design_max_slew"]) == pytest.approx(150 / math.sqrt(3))
    assert limits["forbidden_band_1"] == "all 3 4 0.5"
    assert limits["forbidden_band_2"] == "x 1 2"


def test_the_console_limits_are_the_ones_pulserver_reads():
    blocks = pytest.importorskip("pulserver.host._blocks")
    limits_module = pytest.importorskip("pulserver.host._limits")
    settings = local.validate({
        "limits": {**local.DEFAULT_LIMITS, "forbidden_band_1": "all 500 600 1",
                   "pns_chronaxie": "0.00036", "pns_rheobase": "20"},
    })

    system, _, checks = limits_module.split_limits(blocks.parse_limits(local.console_limits(settings)))

    assert system.B0 == pytest.approx(3.0)
    assert len(checks.bands) == 1


@pytest.mark.parametrize(
    "limits, problem",
    [
        ({"B0": "-1"}, "B0 must be a positive number"),
        ({"max_grad": "fast"}, "max_grad must be a positive number"),
        ({"rf_dead_time": "-1e-6"}, "rf_dead_time must be a non-negative number"),
        ({"grad_unit": "G/cm"}, "grad_unit must be one of"),
        ({"forbidden_band_1": "w 1 2"}, "forbidden_band_1 must be an axis"),
        ({"forbidden_band_1": "all 600 500"}, "forbidden_band_1 must be an axis"),
        ({"bad key": "1"}, "is not a limit name"),
    ],
)
def test_settings_that_pulserver_could_not_design_under_are_refused(limits, problem):
    with pytest.raises(ValueError, match=problem):
        local.validate({"limits": {**local.DEFAULT_LIMITS, **limits}})


def test_a_plugin_directory_that_does_not_exist_is_refused(tmp_path):
    with pytest.raises(ValueError, match="sequences plugin directory does not exist"):
        local.validate({"limits": local.DEFAULT_LIMITS, "plugins": {"sequences": str(tmp_path / "no")}})


def test_an_image_built_on_this_computer_is_not_asked_about_at_a_registry():
    assert local.published_digest("pulserver") is None


def test_settings_read_back_as_written():
    settings = {"limits": {"B0": "3", "pns_x_a1": "0.4"}, "plugins": {"sequences": "/a b", "recon": ""}}

    assert local.parse(local.render(settings)) == settings


@pytest.fixture
def server(docker):
    helper = _helper(docker)
    httpd = local.serve(helper, port=0)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = local.handler(helper, local.ORIGINS, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield helper, port
    httpd.shutdown()


def _request(port, path, origin=None, body=None, host=None, method=None):
    headers = {}
    if origin:
        headers["Origin"] = origin
    if host:
        headers["Host"] = host
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def test_the_page_reads_the_state_across_origins(server):
    _, port = server

    code, headers, body = _request(port, "/state", origin="https://pulserver.github.io")

    assert code == 200
    assert headers["Access-Control-Allow-Origin"] == "https://pulserver.github.io"
    assert json.loads(body)["settings"]["limits"]["B0"] == "3.0"


def test_a_preflight_allows_a_public_page_to_reach_the_helper_on_this_computer(server):
    _, port = server

    code, headers, _ = _request(port, "/settings", origin="https://pulserver.github.io", method="OPTIONS")

    assert code == 204
    assert headers["Access-Control-Allow-Private-Network"] == "true"
    assert headers["Access-Control-Allow-Origin"] == "https://pulserver.github.io"


def test_another_site_cannot_drive_docker(server):
    helper, port = server

    code, _, _ = _request(port, "/pull", origin="https://example.com", body={})

    assert code == 403
    assert helper.task["name"] is None


def test_a_page_reached_by_another_host_name_is_refused(server):
    _, port = server

    code, _, _ = _request(port, "/state", host=f"attacker.example:{port}")

    assert code == 403


def test_settings_posted_by_the_page_are_saved_or_refused_with_the_reason(server):
    helper, port = server
    origin = "https://pulserver.github.io"
    settings = helper.settings()
    settings["limits"]["B0"] = "7"

    saved = _request(port, "/settings", origin=origin, body=settings)
    settings["limits"]["B0"] = "zero"
    refused = _request(port, "/settings", origin=origin, body=settings)

    assert saved[0] == 200 and helper.settings()["limits"]["B0"] == "7"
    assert refused[0] == 400 and "B0 must be a positive number" in json.loads(refused[2])["error"]


def test_the_page_pulls_the_image_through_the_helper(server):
    helper, port = server

    code, _, _ = _request(port, "/pull", origin="https://pulserver.github.io", body={})
    _wait(helper)

    assert code == 202
    assert helper.state()["container"]["running"]
