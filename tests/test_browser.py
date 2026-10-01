"""MaRGE in a browser tab as the console of pulserver's virtual scanner, from exam to images.

Runs where ``MARGE_WEB`` is the address the page of ``web/build.py`` is served
at and ``MARGE_PULSERVER`` the WebSocket address of a ``pulserver console``
that has the ``gre2d`` plugin and reconstructs its scans. ``CHROMIUM`` names a
Chromium to run instead of Playwright's own.
"""

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from marge.browser.boot import PROJECT

WEB = os.environ.get("MARGE_WEB", "")
CONSOLE = os.environ.get("MARGE_PULSERVER", "")

pytestmark = pytest.mark.skipif(
    not (WEB and CONSOLE), reason="needs MARGE_WEB and MARGE_PULSERVER"
)


@pytest.fixture(scope="module")
def page():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("CHROMIUM") or None,
            args=["--autoplay-policy=no-user-gesture-required"],
        )
        tab = browser.new_page(viewport={"width": 1400, "height": 900})
        tab.goto(f"{WEB}/index.html?console={CONSOLE}")
        tab.wait_for_function(
            "window.marge && (window.marge.ready || window.marge.error)", timeout=600_000
        )
        assert tab.evaluate("window.marge.error") is None
        yield tab
        browser.close()


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_a_page_waits_for_its_console_and_goes_on_once_it_answers():
    sync_api = pytest.importorskip("playwright.sync_api")
    websockets = pytest.importorskip("websockets.sync.server")
    address = f"ws://127.0.0.1:{_free_port()}"
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)
        tab = browser.new_page()
        tab.goto(f"{WEB}/index.html?console={address}")
        tab.wait_for_function("window.marge && window.marge.waiting", timeout=60_000)
        waiting = tab.evaluate("[window.marge.waiting, 'pyodide' in window]")
        port = int(address.rsplit(":", 1)[1])
        with websockets.serve(lambda connection: None, "127.0.0.1", port) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            tab.wait_for_function("window.marge.waiting === null", timeout=60_000)
            server.shutdown()
        browser.close()

    assert waiting == [address, False]


def _landing(tab):
    tab.wait_for_function("window.marge && window.marge.landing", timeout=60_000)
    return tab.evaluate("window.marge.landing")


def _levels(tab):
    return tab.evaluate(
        "Object.fromEntries([...document.querySelectorAll('[data-check]')]"
        ".map((dot) => [dot.dataset.check, dot.dataset.level]))"
    )


def test_a_landing_page_without_the_helper_offers_it_and_docker_s_own_command():
    sync_api = pytest.importorskip("playwright.sync_api")
    helper = f"http://127.0.0.1:{_free_port()}"
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)
        tab = browser.new_page()
        tab.goto(f"{WEB}/index.html?helper={helper}")
        landing = _landing(tab)
        command = tab.inner_text("#command")
        with tab.expect_download() as download:
            tab.click("#helper-download")
        script = Path(download.value.path()).read_text()
        browser.close()

    assert landing["helper"] is False
    assert command.startswith("docker run -d --restart unless-stopped --name pulserver ")
    assert command.endswith(" -p 127.0.0.1:8765:8765 ghcr.io/pulserver/pulserver")
    assert script == (Path(__file__).parent.parent / "web" / "pulserver_local.py").read_text()


def test_a_landing_page_installs_pulserver_and_saves_the_scanner_through_the_helper(tmp_path, monkeypatch):
    sync_api = pytest.importorskip("playwright.sync_api")
    from test_pulserver_local import FAKE, PUBLISHED, local

    monkeypatch.setenv("PULSERVER_HOME", str(tmp_path / "home"))
    (tmp_path / "docker.py").write_text(FAKE)
    state = tmp_path / "docker.json"
    state.write_text(json.dumps(
        {"daemon": True, "images": {}, "containers": {}, "calls": [], "published": PUBLISHED}))
    helper = local.Helper(
        docker=[sys.executable, str(tmp_path / "docker.py"), str(state)],
        published=lambda image: PUBLISHED,
    )
    port = _free_port()
    server = local.serve(helper, port, (WEB.rstrip("/"),))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    sequences = tmp_path / "sequences"
    sequences.mkdir()
    try:
        with sync_api.sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ.get("CHROMIUM") or None)
            tab = browser.new_page()
            tab.goto(f"{WEB}/index.html?helper=http://127.0.0.1:{port}")
            tab.wait_for_function("window.marge.landing && window.marge.landing.helper", timeout=60_000)
            before = _levels(tab)
            tab.click("[data-call=pull]")
            tab.wait_for_function(
                "document.querySelector('[data-check=Container]')?.dataset.level === 'ok'", timeout=60_000)
            installed = _levels(tab)
            tab.fill("input[name=B0]", "0.55")
            tab.fill("input[name=grad_raster_time]", "10")
            tab.click("#add-band")
            band = tab.locator("#bands .band input")
            band.nth(0).fill("500")
            band.nth(1).fill("600")
            tab.fill("input[name=sequences]", str(sequences))
            tab.click("#save")
            tab.wait_for_function("document.getElementById('saved').textContent === 'Saved.'", timeout=60_000)
            tab.fill("input[name=recon]", str(tmp_path / "nowhere"))
            tab.click("#save")
            tab.wait_for_function("document.getElementById('saved').className === 'failed'", timeout=60_000)
            refused = tab.inner_text("#saved")
            browser.close()
    finally:
        server.shutdown()
    settings = helper.settings()

    assert before == {"Helper": "ok", "Docker": "ok", "Image": "warn", "Console": "off"}
    assert installed["Image"] == "ok" and installed["Container"] == "ok"
    assert settings["limits"]["B0"] == "0.55"
    assert float(settings["limits"]["grad_raster_time"]) == pytest.approx(10e-6)
    assert settings["limits"]["forbidden_band_1"] == "all 500 600"
    assert settings["plugins"]["sequences"] == str(sequences)
    assert "recon plugin directory does not exist" in refused


def _console_plugins():
    """Return the sequence plugins the console serves."""
    client = pytest.importorskip("websockets.sync.client")
    with client.connect(CONSOLE, max_size=None) as connection:
        connection.send(json.dumps({"id": 1, "call": "plugins"}))
        return json.loads(connection.recv(timeout=60))["plugins"]


def _python(page, code):
    """Run ``code`` in the tab and return its last expression, which is JSON."""
    return json.loads(page.evaluate("code => window.pyodide.runPythonAsync(code)", code))


def _until(page, code, timeout=600.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = _python(page, code)
        if value:
            return value
        page.wait_for_timeout(1000)
    raise AssertionError(f"not within {timeout} s: {code}")


PRELUDE = """
import json
from marge.browser import boot
from marge.seq.sequences import defaultsequences
session = boot._kept[1]
main = session.main_gui
history = [] if main is None else [main.history_list.item(i).text() for i in range(main.history_list.count())]
"""


def test_the_session_opens_configured_for_the_virtual_scanner_with_its_coils_to_choose_from(page):
    state = _python(
        page,
        PRELUDE
        + "combo = session.tab_session.rf_coil_combo_box\n"
        + "json.dumps([session.launch_gui_action.isEnabled(),"
        + " session.tab_session.project_combo_box.currentText(),"
        + " [combo.itemText(i) for i in range(combo.count())], combo.currentText()])",
    )

    assert state == [True, PROJECT, ["body", "body/head48", "head8/head32"], "body"]


def test_an_exam_opens_on_the_localizer_of_the_subjects_phantom_among_pulservers_sequences_only(page):
    _python(
        page,
        PRELUDE
        + """
session.tab_session.name_line_edit.setText("vials")
session.launch_gui_action.trigger()
json.dumps(None)
""",
    )

    history = _until(page, PRELUDE + "json.dumps(history)")
    localizer, names = _python(
        page,
        PRELUDE + 'json.dumps([len(defaultsequences["Localizer"].files), sorted(defaultsequences)])',
    )

    assert history[0].split(" | ")[1].startswith("Localizer.")
    assert localizer == 3
    assert names == sorted(["Localizer", *_console_plugins()])


def test_the_console_offers_acquire_and_the_localizer_and_not_what_needs_marcos(page):
    enabled = _python(
        page,
        PRELUDE
        + """
bar = main.toolbar_sequences
actions = (bar.action_acquire, bar.action_localizer, bar.action_add_to_list, bar.action_iterate,
           bar.action_autocalibration, bar.action_bender, bar.action_view_sequence)
json.dumps([action.isVisible() and action.isEnabled() for action in actions])
""",
    )

    assert enabled == [True, True, False, False, False, False, False]


def test_a_scan_of_marges_protocol_plays_its_sound_and_returns_its_reconstruction(page):
    _python(
        page,
        PRELUDE
        + """
sequence = defaultsequences["gre2d"]
sequence.mapVals["nx"] = sequence.mapVals["ny"] = 32
main.sequence_list.setCurrentText("gre2d")
bar = main.toolbar_sequences
bar.widgetForAction(bar.action_acquire).click()
json.dumps(None)
""",
    )

    history = _until(page, PRELUDE + "json.dumps(len(history) == 2 and history)")
    files, prepared, clock, played = _python(
        page,
        PRELUDE
        + """
from marge.seq import pulserver_console as console
sequence = defaultsequences["gre2d"]
json.dumps([len(sequence.files), sequence.prepared, sequence.clock, console.SPEAKER.played])
""",
    )

    assert history[1].split(" | ")[1].startswith("gre2d.")
    assert files >= 1
    assert prepared >= 1
    assert clock[0] == pytest.approx(clock[1]) and clock[1] > 0.0
    assert played == pytest.approx(clock[1], rel=0.01)


def test_another_subject_in_the_session_window_opens_another_exam_on_its_localizer(page):
    _python(
        page,
        PRELUDE
        + """
main.close()
session.tab_session.name_line_edit.setText("phantom")
session.launch_gui_action.trigger()
json.dumps(None)
""",
    )

    history = _until(page, PRELUDE + "json.dumps(len(history) == 1 and history)")
    subjects = _python(
        page,
        PRELUDE
        + """
import io
import pydicom
files = defaultsequences["Localizer"].files
json.dumps(sorted({str(pydicom.dcmread(io.BytesIO(data)).PatientName) for data in files}))
""",
    )

    assert history[0].split(" | ")[1].startswith("Localizer.")
    assert subjects == ["phantom"]
