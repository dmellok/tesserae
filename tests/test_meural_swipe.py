"""Meural gestures use the existing dashboard-item and lineup push paths."""

import io
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs

import pytest
from PIL import Image, PngImagePlugin

from app.frame_log import exchanges
from app.push import PushResult
from app.state.deck_model import Deck, DeckPage
from app.state.page_store import Cell, Page
from tests.test_meural import app as app
from tests.test_meural import no_network as no_network
from tests.test_meural import setup as setup


def picture(key="", color="red"):
    out = io.BytesIO()
    meta = PngImagePlugin.PngInfo()
    if key:
        meta.add_text("tesserae_items", json.dumps({"cell": [key]}))
    Image.new("RGB", (108, 192), color).save(out, "PNG", pnginfo=meta)
    return out.getvalue()


@pytest.fixture
def swipe(setup, monkeypatch):
    class Cloud:
        account_id = "17"

        def __init__(self):
            self.loads = []
            self.uploads = []
            self.pictures = []

        def get_playback(self, target):
            return {"item_id": "28", "settings": {"imageDuration": 60}}

        def pause_rotation(self, target):
            pass

        def upload(self, image, name):
            self.uploads.append(image)
            return str(100 + len(self.uploads) - 1)

        def show_item(self, target, item):
            self.loads.append((target, item))
            setup.frame.item = item

        def download_current_image(self, target, item):
            self.pictures.append((target, item))
            return picture(color="green")

        def delete_item(self, item):
            pass

    cloud = Cloud()
    setup.settings.update_item(
        "devices",
        "test_frame",
        {"delivery_method": "cloud", "cloud_device_id": "101", "meural_account_id": "17"},
    )
    monkeypatch.setattr("app.meural_cloud.get_cloud_client", lambda *a: cloud)
    setup.latest.update(page_id="a", digest="first")
    setup.app.config["RENDERS_DIR"].joinpath("first.png").write_bytes(picture("b"))
    setup.app.config["PAGE_STORE"].save(Page(id="a", name="A", device_ids=["test_frame"]))
    setup.app.config["PAGE_STORE"].save(Page(id="z", name="Z", device_ids=["test_frame"]))
    setup.app.config["DECK_STORE"].upsert(
        Deck(id="line", name="Line", device_ids=["test_frame"], pages=[DeckPage(page_id="a")])
    )
    setup.app.config["DECK_NAV_STORE"].set("test_frame", "line", "a")
    pusher = setup.app.config["PUSH_MANAGER"]
    monkeypatch.setattr(pusher, "latest_render_for", lambda _: setup.latest)
    # The served-frame store needs the same published render as the publisher.
    pusher._latest_renders["test_frame"] = setup.latest
    sends = []

    def republish(png, *, page_id, device_ids):
        sends.append((png, page_id, device_ids))
        setup.latest.update(composition_digest="second", digest="second", page_id=page_id)
        setup.app.config["RENDERS_DIR"].joinpath("second.png").write_bytes(png)
        return PushResult(status="sent", page_id=page_id)

    monkeypatch.setattr(pusher, "republish_composition", republish)
    renders = []

    def render(**kw):
        key = parse_qs(kw["query"].lstrip("&"))["item"][0]
        renders.append(key)
        kw["cache_path"].parent.mkdir(parents=True, exist_ok=True)
        kw["cache_path"].write_bytes(picture(key, "blue"))

    monkeypatch.setattr("app.preview_cache.render_now", render)
    publisher = setup.publisher()
    publisher.poll_once()
    publisher.poll_once()  # Confirm the cloud item reached the frame.
    setup.clock[0] += 20

    def gesture():
        setup.frame.item = "999"
        publisher.poll_once()

    return SimpleNamespace(
        **vars(setup), cloud=cloud, pub=publisher, gesture=gesture, sends=sends, renders=renders
    )


def listed(swipe, *, draw=False):
    plugin = SimpleNamespace(
        manifest={"lineups": {"draw": True} if draw else {"pin_option": "file"}},
        server_module=SimpleNamespace(
            lineup_items=lambda options: [{"key": k, "label": k} for k in ("a", "b", "c")]
        ),
        capabilities=None,
        data_dir=swipe.device.data_dir,
    )
    swipe.app.config["PLUGIN_REGISTRY"].plugins["swipe_test"] = plugin
    swipe.app.config["PAGE_STORE"].save(
        Page(
            id="a",
            name="A",
            device_ids=["test_frame"],
            cells=[Cell(id="cell", plugin="swipe_test", x=0, y=0, w=1, h=1)],
        )
    )


def test_swipe_pushes_item_after_the_one_shown(swipe):
    listed(swipe)
    swipe.gesture()
    assert swipe.renders == ["c"]
    assert swipe.sends[0][1:] == ("a", {"test_frame"})
    assert any(
        "Swipe on the frame: next item" in row[2] for row in exchanges(swipe.app, "test_frame")
    )


def test_swipe_draws_a_new_item(swipe):
    listed(swipe, draw=True)
    swipe.gesture()
    assert len(swipe.renders) == 1
    assert swipe.renders[0].startswith("draw:")


def test_one_item_dashboard_steps_forward(swipe, monkeypatch):
    buttons = swipe.app.config["BUTTON_SERVICE"]
    step = Mock(return_value=SimpleNamespace(pushed_page_id="z", error=None))
    monkeypatch.setattr(buttons, "step", step)
    swipe.gesture()
    assert step.call_args.kwargs == dict(
        device_id="test_frame",
        forward=True,
        source="meural_swipe",
        lock_timeout_s=20.0,
        settle_max_s=45.0,
    )
    assert any("next dashboard" in row[2] for row in exchanges(swipe.app, "test_frame"))


def test_single_dashboard_single_item_reshows_without_upload(swipe):
    swipe.gesture()
    assert swipe.cloud.loads == [("101", "100"), ("101", "100")]
    assert len(swipe.cloud.uploads) == 1
    assert any(
        "showing Tesserae's image again" in row[2] for row in exchanges(swipe.app, "test_frame")
    )


@pytest.mark.parametrize("guard", ["sleep", "quiet", "grace", "pending", "own", "no_host"])
def test_swipe_guards(swipe, monkeypatch, guard):
    step = Mock()
    monkeypatch.setattr(swipe.app.config["BUTTON_SERVICE"], "step", step)
    if guard == "sleep":
        swipe.frame.sleeping = True
    elif guard == "quiet":
        swipe.quiet[0] = True
    elif guard == "grace":
        swipe.clock[0] -= 19
    elif guard == "pending":
        path = next(swipe.device.data_dir.glob("meural_cloud_*.json"))
        state = json.loads(path.read_text())
        state.update(pending_key="other", pending_item="200")
        path.write_text(json.dumps(state))
    elif guard == "no_host":
        swipe.settings.update_item("devices", "test_frame", {"host": ""})
    if guard == "own":
        swipe.pub.poll_once()
    else:
        swipe.gesture()
    step.assert_not_called()
    assert len(swipe.cloud.loads) == 1


def test_one_gesture_does_not_repeat_while_waiting_for_a_render(swipe, monkeypatch):
    step = Mock(return_value=SimpleNamespace(pushed_page_id="z", error=None))
    monkeypatch.setattr(swipe.app.config["BUTTON_SERVICE"], "step", step)
    swipe.gesture()
    swipe.clock[0] += 30
    swipe.pub.poll_once()
    step.assert_called_once()


def test_step_push_callback_does_not_deadlock_the_poller(swipe, monkeypatch):
    listed(swipe)
    push = swipe.app.config["PUSH_MANAGER"]
    original = push.republish_composition

    def notify(*args, **kwargs):
        result = original(*args, **kwargs)
        swipe.pub.on_push(result)
        return result

    monkeypatch.setattr(push, "republish_composition", notify)
    thread = threading.Thread(target=swipe.gesture, daemon=True)
    thread.start()
    thread.join(3)
    assert not thread.is_alive()
    assert swipe.renders == ["c"]
    assert len(swipe.cloud.loads) == 2


def test_delayed_cloud_show_is_not_a_swipe_even_after_the_grace_period(swipe, monkeypatch):
    step = Mock()
    monkeypatch.setattr(swipe.app.config["BUTTON_SERVICE"], "step", step)
    # Netgear accepts the command before the frame has downloaded the image.
    monkeypatch.setattr(
        swipe.cloud, "show_item", lambda target, item: swipe.cloud.loads.append((target, item))
    )
    swipe.latest["composition_digest"] = "second"
    swipe.app.config["RENDERS_DIR"].joinpath("second.png").write_bytes(picture("c", "blue"))
    swipe.pub.poll_once()
    swipe.clock[0] += 30
    swipe.pub.poll_once()
    step.assert_not_called()


def test_one_item_uses_the_real_lineup_step_and_updates_navigation(swipe, monkeypatch):
    swipe.app.config["DECK_STORE"].upsert(
        Deck(
            id="line",
            name="Line",
            device_ids=["test_frame"],
            pages=[DeckPage(page_id="a"), DeckPage(page_id="z")],
        )
    )
    calls = []

    def push(page_id, **kw):
        calls.append((page_id, kw))
        return PushResult(status="sent", page_id=page_id)

    monkeypatch.setattr(swipe.app.config["PUSH_MANAGER"], "push", push)
    swipe.gesture()
    assert calls[0][0] == "z"
    assert calls[0][1]["source"] == "meural_swipe"
    assert swipe.app.config["DECK_NAV_STORE"].current_page("test_frame", "line") == "z"


def test_own_item_keeps_existing_thumbnail(swipe):
    swipe.pub.poll_once()
    assert swipe.cloud.pictures == []
    served = swipe.app.config["PUSH_MANAGER"].last_served_render_for("test_frame")
    assert served["composition_digest"] == "first"


def test_other_art_is_cached_as_the_frame_held_thumbnail(swipe):
    swipe.gesture()
    served = swipe.app.config["PUSH_MANAGER"].last_served_render_for("test_frame")
    assert served["composition_digest"] != "first"
    assert swipe.app.config["RENDERS_DIR"].joinpath(f"{served['composition_digest']}.png").is_file()
    assert swipe.cloud.pictures == [("101", "999")]
    with swipe.app.test_request_context():
        from app.deck_routes import _glass_thumb

        assert served["composition_digest"] in _glass_thumb(swipe.device)
    swipe.pub.poll_once()
    served = swipe.app.config["PUSH_MANAGER"].last_served_render_for("test_frame")
    assert served["composition_digest"] == "first"


def test_screenshot_keeps_the_item_identity_with_the_pixels():
    from app.renderer import _stamp_step_items

    page = SimpleNamespace(evaluate=lambda script: {"cell": ["b"]})
    png = _stamp_step_items(picture(), page)
    with Image.open(io.BytesIO(png)) as image:
        assert json.loads(image.info["tesserae_items"]) == {"cell": ["b"]}
        assert image.getpixel((0, 0)) == (255, 0, 0)


def test_client_reads_gallery_status_without_exposing_other_fields(monkeypatch):
    from app.meural import MeuralClient
    from tests.test_meural import response

    calls = []

    def request(req, timeout):
        calls.append(req.full_url)
        return response(
            {
                "status": "pass",
                "response": {
                    "current_gallery": "5",
                    "current_item": "28167866",
                    "current_gallery_name": "All works",
                },
            }
        )

    monkeypatch.setattr("app.meural.open_request", request)
    assert MeuralClient("192.0.2.10").current_item() == "28167866"
    assert calls == ["http://192.0.2.10/remote/get_gallery_status_json/"]


def test_republished_item_remembers_its_dashboard(setup):
    push = setup.app.config["PUSH_MANAGER"]
    result = push.republish_composition(picture("b"), page_id="a", device_ids={"test_frame"})
    assert result.status == "sent"
    assert push.latest_render_for("test_frame")["page_id"] == "a"


@pytest.mark.parametrize("external", [False, True])
def test_device_card_and_page_use_the_held_thumbnail(swipe, external):
    if external:
        swipe.gesture()
    else:
        swipe.pub.poll_once()
    digest = swipe.app.config["PUSH_MANAGER"].last_served_render_for("test_frame")[
        "composition_digest"
    ]
    with swipe.app.test_request_context():
        from app.settings.index_routes import _build_device_section

        section = _build_device_section(swipe.settings, swipe.device)
        assert digest in section["overview"]["thumb_url"]


@pytest.mark.parametrize("matches", [False, True])
def test_current_art_download_checks_cloud_lag_and_uses_no_account_token(
    setup, monkeypatch, matches
):
    from app.meural_cloud import MeuralCloudClient

    client = MeuralCloudClient(setup.settings, "test_frame")
    monkeypatch.setattr(
        client,
        "request",
        lambda *a: {
            "data": {
                "frameStatus": {"currentItem": "999" if matches else "998"},
                "currentImage": "https://example.cloudfront.net/art.jpg?Signature=private-example",
            }
        },
    )
    requests = []

    def download(request, timeout):
        requests.append(request)
        return io.BytesIO(picture(color="green"))

    monkeypatch.setattr("app.meural_http.open_request", download)
    png = client.download_current_image("101", "999")
    assert bool(png) == matches
    if matches:
        assert requests[0].get_header("Authorization") is None
        with Image.open(io.BytesIO(png)) as image:
            assert image.getpixel((0, 0)) == (0, 128, 0)
    else:
        assert requests == []


def test_queued_cloud_render_does_not_replace_the_held_thumbnail(swipe):
    swipe.pub.poll_once()
    swipe.latest.update(composition_digest="second", digest="second")
    swipe.app.config["RENDERS_DIR"].joinpath("second.png").write_bytes(picture("c", "blue"))
    swipe.pub.poll_once()
    assert len(swipe.cloud.loads) == 1
    held = swipe.app.config["PUSH_MANAGER"].last_served_render_for("test_frame")
    assert held["composition_digest"] == "first"


def test_artwork_lookup_is_throttled_while_the_cloud_lags(setup):
    calls = []
    cloud = SimpleNamespace(download_current_image=lambda target, item: calls.append(item))
    publisher = setup.publisher()
    for _ in range(3):
        publisher._record_external(setup.device, cloud, "101", "999")
    assert calls == ["999"]
    for _ in range(10):
        setup.clock[0] += 61
        publisher._record_external(setup.device, cloud, "101", "999")
    assert calls == ["999"] * 5
    publisher._record_external(setup.device, cloud, "101", "1000")
    assert calls[-1] == "1000"
