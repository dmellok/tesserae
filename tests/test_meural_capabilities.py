"""Meural exposes LCD controls without e-paper calibration or wake predictions."""

import pytest

from app import device_service
from app.main import create_app


@pytest.fixture
def app(tmp_path):
    app = create_app(testing=True, data_root=tmp_path)
    result = device_service.create_instance(
        devices=app.config["DEVICE_REGISTRY"],
        renderers=app.config["RENDERER_REGISTRY"],
        data_root=app.config["DATA_ROOT"],
        instance_id="example_frame",
        kind_id="meural",
        name="Example frame",
    )
    assert result.ok
    return app


def test_meural_page_only_offers_supported_controls(app):
    client = app.test_client()
    page = client.get("/settings/devices/example_frame").text.split('class="dx-dp"', 1)[1]
    for text in [
        ">Calibrate<",
        "Send orientation card",
        ">Battery<",
        ">Firmware<",
        ">Checked in<",
        "no heartbeat yet",
        'name="sleep_interval_s"',
        "Button map",
        "Touch monitor",
        "OTA logs",
        "Device logs",
        ">Rendering<",
        "Recalibrate",
        'name="dither"',
    ]:
        assert text not in page, text
    for text in ["Example frame", "Push now", "Frame address", "Rotation"]:
        assert text in page
    assert client.get("/settings/devices/example_frame/calibration").status_code == 404
    assert client.post("/settings/devices/example_frame/calibrate").status_code == 400


@pytest.mark.parametrize("kind", ["samsung_frame", "fraimic", "esp32_client"])
def test_other_kinds_keep_existing_capability_defaults(app, kind):
    result = device_service.create_instance(
        devices=app.config["DEVICE_REGISTRY"],
        renderers=app.config["RENDERER_REGISTRY"],
        data_root=app.config["DATA_ROOT"],
        instance_id="other_frame",
        kind_id=kind,
        name="Other frame",
    )
    if not result.ok and app.config["DEVICE_REGISTRY"].get(kind) is None:
        pytest.skip(f"{kind} is not included in this branch")
    assert result.ok
    page = app.test_client().get("/settings/devices/other_frame").text
    assert ">Calibrate<" in page
