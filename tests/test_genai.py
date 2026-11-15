"""Settings › Generative AI: the shared key, the ceilings over every
collection, the ledger and the page."""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from app import artwork, fal, genai
from app.main import REPO_ROOT, create_app


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    a.config["TESTING"] = True
    return a


def _signed_in(app: Flask):
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    return client


def test_ledger_counts_today_the_month_and_the_reset(tmp_path):
    led = genai.Ledger(tmp_path / "artwork")
    led.append("overhead_plate", "paintings", "nano_banana", 4)
    led.append("overhead_plate", "paintings", "nano_banana", 4)
    led.append("specimen_sheet", "cutouts", "birefnet", 1)
    assert led.today_cents() == 9 and led.month_cents() == 9
    s = led.summary()
    assert s["today"] == 9 and s["by_widget"][("overhead_plate", "paintings")]["pictures"] == 2
    assert s["by_model"][("specimen_sheet", "cutouts", "birefnet")] == {"pictures": 1, "cents": 1}
    assert len(s["strip"]) == 30 and s["strip"][-1]["cents"] == 9 and s["strip"][-1]["level"] == 4
    led.reset_today()
    assert led.today_cents() == 0 and led.month_cents() == 9, (
        "the lines stay; the count starts over"
    )
    led.append("overhead_plate", "paintings", "nano_banana", 4)
    assert led.today_cents() == 4
    # a broken line is skipped, not fatal
    with led.path.open("a") as f:
        f.write("not json\n")
    assert led.month_cents() == 13


def test_settings_round_trip_and_the_key_is_what_resolve_key_finds(app, tmp_path, monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.delenv("FAL_API_KEY", raising=False)
    store = app.config["SETTINGS_STORE"]
    assert genai.settings(store) == {"api_key": "", "daily_usd": 0, "monthly_usd": 0, "contact": ""}
    genai.save(store, {"api_key": " fal_sk_abc ", "daily_usd": "6", "monthly_usd": "$60"})
    assert genai.settings(store) == {
        "api_key": "fal_sk_abc",
        "daily_usd": 6.0,
        "monthly_usd": 60.0,
        "contact": "",
    }
    assert genai.ceilings(store) == (600, 6000)
    with app.app_context():
        assert fal.resolve_key({}) == "fal_sk_abc"
        assert fal.resolve_key({"fal_api_key": "own"}) == "own"
    # the mask leaves the key alone; an empty string clears it
    genai.save(store, {"api_key": "********", "daily_usd": "0"})
    assert (
        genai.settings(store)["api_key"] == "fal_sk_abc"
        and genai.ceilings(store)[0] == genai.UNLIMITED
    )
    genai.save(store, {"api_key": ""})
    assert genai.settings(store)["api_key"] == ""
    assert genai.for_admin(store)["has_key"] is False


def test_the_ceiling_holds_every_collection(tmp_path, monkeypatch):
    led = genai.Ledger(tmp_path / "artwork")
    ceiling = {"daily": 10, "monthly": genai.UNLIMITED}
    artwork.host_configure(ledger=led, ceilings=lambda: (ceiling["daily"], ceiling["monthly"]))
    try:
        c = artwork.Collection(
            "t_plugin", "paintings", make=lambda job: (b"x", {}), label="Test", title="Painting"
        )
        data_dir = tmp_path / "t_plugin"
        c.spend(data_dir, 4, "nano_banana")
        c.spend(data_dir, 4, "nano_banana")
        assert led.today_cents() == 8 and c.budget_used(data_dir) == 8
        assert artwork.ceiling_spent() == ""
        c.spend(data_dir, 4, "nano_banana")
        assert artwork.ceiling_spent().startswith("the server's daily ceiling")
        assert c.room(data_dir, artwork.UNLIMITED) == 0
        client = c._fal_for(data_dir, "k", __import__("threading").Event(), artwork.UNLIMITED)
        with pytest.raises(artwork.ArtworkError):
            client.generate("nano_banana", "p", refs=["u"])
        ceiling["daily"] = genai.UNLIMITED
        ceiling["monthly"] = 5
        assert artwork.ceiling_spent().startswith("the server's monthly ceiling")
        led.reset_today()
        ceiling["monthly"] = genai.UNLIMITED
        assert artwork.ceiling_spent() == "" and led.today_cents() == 0
    finally:
        artwork.host_configure(None, None)


def test_check_key_tells_a_good_key_from_a_bad_one(monkeypatch):
    import urllib.error

    def answer(code):
        def opener(req, timeout=0):
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)

        return opener

    monkeypatch.setattr(genai.urllib.request, "urlopen", answer(404))
    ok, note = genai.check_key("k")
    assert ok and "accepted" in note
    monkeypatch.setattr(genai.urllib.request, "urlopen", answer(401))
    ok, note = genai.check_key("k")
    assert not ok and "refused" in note
    assert genai.check_key("") == (False, "No key to check.")


def test_the_page_saves_checks_and_resets(app, tmp_path, monkeypatch):
    client = _signed_in(app)
    r = client.get("/settings/genai")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Generative AI" in body and "No key" in body and "fal.ai API key" in body
    r = client.post(
        "/settings/genai",
        data={"api_key": "fal_sk_abc", "daily_usd": "6", "monthly_usd": ""},
        follow_redirects=True,
    )
    body = r.get_data(as_text=True)
    assert "Key set" in body and "of a US$6.00 ceiling" in body and "fal_sk_abc" not in body
    monkeypatch.setattr(genai, "check_key", lambda key, timeout=8.0: (key == "fal_sk_abc", key))
    r = client.post("/settings/genai/check", data={"api_key": "********"}, follow_redirects=True)
    assert "Accepted" in r.get_data(as_text=True)
    r = client.post("/settings/genai/check", data={"api_key": "other"}, follow_redirects=True)
    assert "Refused" in r.get_data(as_text=True)
    assert genai.settings(app.config["SETTINGS_STORE"])["api_key"] == "fal_sk_abc", (
        "a refused key is not kept"
    )
    monkeypatch.setattr(genai, "check_key", lambda key, timeout=8.0: (True, "ok"))
    r = client.post("/settings/genai/check", data={"api_key": "fal_sk_new"}, follow_redirects=True)
    assert (
        "Saved" in r.get_data(as_text=True)
        and genai.settings(app.config["SETTINGS_STORE"])["api_key"] == "fal_sk_new"
    ), "Check keeps a new key it accepts"
    led = genai.Ledger(tmp_path / "artwork")
    led.append("overhead_plate", "paintings", "nano_banana", 350)
    body = client.get("/settings/genai").get_data(as_text=True)
    assert "US$3.50" in body and "overhead_plate" in body and "Nano Banana" in body
    assert "not installed" in body, "a widget in the ledger but not installed is still listed"
    r = client.post("/settings/genai/reset", follow_redirects=True)
    assert "counts from now" in r.get_data(as_text=True)
    assert led.today_cents() == 0
    # the tab is in the settings nav everywhere
    assert "/settings/genai" in client.get("/settings/server").get_data(as_text=True)


def test_choose_kie_and_store_its_key_encrypted(app, tmp_path, monkeypatch):
    from app import kie_images

    client = _signed_in(app)
    response = client.post(
        "/settings/genai", data={"image_service": "kie", "kie_api_key": "private-kie-key"}
    )
    assert response.status_code == 302
    with app.app_context():
        assert kie_images.configuration() == ("kie", "private-kie-key")
        assert fal.painting_key({"fal_api_key": "old-key"}) == "old-key"
    assert "private-kie-key" not in (tmp_path / "core" / "settings.json").read_text()
    html = client.get("/settings/genai").get_data(as_text=True)
    assert '<option value="kie" selected>' in html
    assert "private-kie-key" not in html
    assert 'name="kie_api_key"' in html
    client.post("/settings/genai", data={"kie_api_key": "********"})
    with app.app_context():
        assert kie_images.configuration() == ("kie", "private-kie-key")
    monkeypatch.setattr(kie_images, "credits", lambda key: 42)
    response = client.post(
        "/settings/genai/check-kie", data={"kie_api_key": "********"}, follow_redirects=True
    )
    assert "42 credits available" in response.get_data(as_text=True)


@pytest.mark.parametrize("service", ["gemini", "openai", "fal"])
def test_explicit_service_overrides_automatic_model_routing(app, service):
    from app import kie_images

    store = app.config["SETTINGS_STORE"]
    genai.save(store, {"image_service": service, "kie_api_key": "kie"})
    with app.app_context():
        c = fal.FalClient("fal", gemini_key="gemini", openai_key="openai")
        assert kie_images.configuration()[0] == service
        assert (
            c.direct_model("nano_banana")
            == {"gemini": "nano_banana_gemini", "openai": "gpt_image_openai", "fal": None}[service]
        )


def test_selected_service_keeps_fal_key_for_background_masks(app):
    genai.save(
        app.config["SETTINGS_STORE"],
        {"image_service": "kie", "kie_api_key": "kie", "api_key": "fal-mask-key"},
    )
    with app.app_context():
        assert fal.painting_key({}) == "fal-mask-key"
