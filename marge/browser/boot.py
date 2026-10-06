"""Start MaRGE in a browser tab as the console of pulserver's virtual scanner."""

from __future__ import annotations

import math
import os
from typing import Any

#: MaRGE's working directory in the tab's file system, which lasts as long as the tab.
HOME = "/home/pyodide/marge"

#: A project, a study and a Red Pitaya address, which MaRGE's session asks for
#: and the virtual scanner does not use.
PROJECT, STUDY, RED_PITAYA = "pulserver", "Phantom", "127.0.0.1"

#: The gyromagnetic ratio in Hz/T, pypulseqpp's, of a scanner that names none.
GAMMA = 42.576e6

#: Where the landing page's scanner sets a field of MaRGE's Gradients and RF tabs.
SET_BY_PAGE = "Set on the page that opened MaRGE"

_kept: list[Any] = []


async def start(address: str, scanner: dict[str, str] | None = None) -> Any:
    """Connect to ``pulserver console`` at ``address`` and open MaRGE's session window.

    The console's plugin listings are fetched before MaRGE imports its
    sequences, since no Qt handler may wait on the network in a tab, and
    scans play their sound through the tab's speaker. A tab
    starts with no configuration, so the session is configured with
    :data:`PROJECT`, :data:`STUDY` and :data:`RED_PITAYA`, and with the
    virtual scanner's coils as its RF coils, the first selected.

    ``scanner`` is the limits block the landing page started the console with,
    as ``key: value`` strings; the fields of MaRGE's Gradients and RF tabs it
    determines are set from it and cannot be edited, so that MaRGE and the
    console describe one scanner.
    """
    from . import audio, qt5, runtime

    runtime.install()
    qt5.install()
    os.environ["MARGE_PULSERVER"] = address
    from marge.seq import pulserver_console as console

    gateway = await console.AsyncGateway.open(address)
    console.install((gateway, await console.listings_async(gateway)))
    console.SPEAKER = audio.Speaker()
    coils = await console.coil_names(gateway)

    os.makedirs(HOME, exist_ok=True)
    os.chdir(HOME)
    for folder in ("experiments/parameterization", "calibration", "protocols", "reports", "configs"):
        os.makedirs(folder, exist_ok=True)

    from PyQt5.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    from marge.controller.controller_session import SessionController

    session = SessionController()
    if not os.path.exists("configs/sys_projects.csv"):
        _configure(session, coils)
    if scanner:
        _hardware(session, scanner)
    session.show()
    _kept[:] = [application, session]
    return session


def _configure(session: Any, coils: list[str]) -> None:
    tab = session.tab_session
    for combo, name in ((tab.project_combo_box, PROJECT), (tab.study_combo_box, STUDY)):
        combo.addItem(name)
        combo.setCurrentText(name)
        combo.save_items()
    session.tab_console.text_box.setText(RED_PITAYA)
    session.tab_console.add_rp()
    session.tab_console.save_rp_entries()
    session.tab_console.update_hw_config_rp()
    for coil in coils:
        session.tab_rf.text_box_1.setText(coil)
        session.tab_rf.text_box_2.setText("1.0")
        session.tab_rf.add_rf()
    session.tab_rf.save_rf_entries()
    session.tab_gradients.save_gradient_entries()
    session.tab_others.save_others_entries()
    with open("configs/b1Efficiency.csv", "w") as file:
        file.write(f"{coils[0]}\n")
    session.update_hardware()


def hardware_fields(limits: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """Return the Gradients and RF tab fields a limits block determines, as their texts.

    Each gradient axis takes the scanner's ``max_grad``, the rise time is the
    time ``max_slew`` takes to reach it, and its steps are the gradient raster
    times in it. The RFPA de-blanking time is ``rf_dead_time``, before the RF
    pulse, and MaRGE's RF dead time is ``rf_ringdown_time``, after it. The
    virtual scanner's gradients have no delay.
    """
    gamma = float(limits.get("gamma", GAMMA))
    grad = float(limits.get("max_grad", 40.0)) * {
        "mT/m": 1e-3, "Hz/m": 1 / gamma, "rad/ms/mm": 1e6 / (2 * math.pi * gamma)
    }[limits.get("grad_unit", "mT/m")]
    slew = float(limits.get("max_slew", 150.0)) * {
        "T/m/s": 1.0, "mT/m/ms": 1.0, "Hz/m/s": 1 / gamma, "rad/ms/mm/ms": 1e9 / (2 * math.pi * gamma)
    }[limits.get("slew_unit", "T/m/s")]
    raster = float(limits.get("grad_raster_time", 20e-6))
    rise = grad / slew
    gradients = {
        "Gx max (mT/m)": f"{grad * 1e3:g}",
        "Gy max (mT/m)": f"{grad * 1e3:g}",
        "Gz max (mT/m)": f"{grad * 1e3:g}",
        "Max slew rate (mT/m/ms)": f"{slew:g}",
        "Gradient raster time (us)": f"{raster * 1e6:g}",
        "Gradient rise time (us)": f"{rise * 1e6:g}",
        "Gradient steps": str(max(1, round(rise / raster))),
        "Gradient delay (us)": "0",
    }
    rf = {
        "RFPA de-blanking time (us)": f"{float(limits.get('rf_dead_time', 0.0)) * 1e6:g}",
        "RF dead time (us)": f"{float(limits.get('rf_ringdown_time', 0.0)) * 1e6:g}",
        "Gyromagnetic ratio (MHz/T)": f"{gamma * 1e-6:.10g}",
        "Larmor frequency (MHz)": f"{gamma * float(limits.get('B0', 3.0)) * 1e-6:.10g}",
    }
    return gradients, rf


def _hardware(session: Any, limits: dict[str, str]) -> None:
    gradients, rf = hardware_fields(limits)
    for tab, fields, save in (
        (session.tab_gradients, gradients, session.tab_gradients.save_gradient_entries),
        (session.tab_rf, rf, session.tab_rf.save_rf_entries),
    ):
        for label, text in fields.items():
            box = tab.input_boxes[label]
            box.setText(text)
            box.setReadOnly(True)
            box.setStatusTip(SET_BY_PAGE)
            box.setToolTip(SET_BY_PAGE)
        save()
    session.update_hardware()
