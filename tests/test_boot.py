"""The fields of MaRGE's Gradients and RF tabs that the landing page's scanner sets."""

import pytest

from marge.browser.boot import GAMMA, hardware_fields


def test_every_gradient_axis_takes_the_scanner_s_amplitude_and_the_ramp_its_slew():
    gradients, _ = hardware_fields(
        {"B0": "3.0", "max_grad": "40", "grad_unit": "mT/m", "max_slew": "200",
         "slew_unit": "T/m/s", "grad_raster_time": "1e-05"}
    )

    assert [gradients[f"G{axis} max (mT/m)"] for axis in "xyz"] == ["40", "40", "40"]
    assert gradients["Max slew rate (mT/m/ms)"] == "200"
    assert gradients["Gradient raster time (us)"] == "10"
    assert float(gradients["Gradient rise time (us)"]) == pytest.approx(200.0)
    assert gradients["Gradient steps"] == "20"
    assert gradients["Gradient delay (us)"] == "0"


def test_gradient_limits_in_hz_are_converted_with_the_gyromagnetic_ratio():
    gradients, _ = hardware_fields(
        {"B0": "3.0", "max_grad": str(40e-3 * GAMMA), "grad_unit": "Hz/m",
         "max_slew": str(150 * GAMMA), "slew_unit": "Hz/m/s"}
    )

    assert float(gradients["Gx max (mT/m)"]) == pytest.approx(40.0)
    assert float(gradients["Max slew rate (mT/m/ms)"]) == pytest.approx(150.0)


def test_the_rf_dead_times_and_the_larmor_frequency_follow_the_scanner():
    _, rf = hardware_fields(
        {"B0": "0.55", "rf_dead_time": "1e-04", "rf_ringdown_time": "3e-05"}
    )

    assert rf["RFPA de-blanking time (us)"] == "100"
    assert rf["RF dead time (us)"] == "30"
    assert float(rf["Gyromagnetic ratio (MHz/T)"]) == pytest.approx(GAMMA * 1e-6)
    assert float(rf["Larmor frequency (MHz)"]) == pytest.approx(GAMMA * 0.55 * 1e-6)
