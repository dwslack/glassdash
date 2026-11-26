import asyncio
import time
from types import SimpleNamespace as NS

from fastapi.testclient import TestClient

from dashd.assistant import TOOLS, Assistant
from dashd.bus import SignalBus
from dashd.nav.favorites import Favorites
from dashd.nav.service import NavService
from dashd.server import create_app
from dashd.settings import Settings, mask

from test_nav import FakeRouter, fix_at, make_places, route


def setup(tmp_path=None, client=None):
    sig = SignalBus()
    sig.set("coolant", 91.6, "C")
    sig.set("voltage", 14.18, "V")
    sig.set_gps({**fix_at(route(), 0, speed=30)})
    favs = Favorites(None)
    favs.add("Home", 49.17, -121.96)
    nav = NavService(sig, None, router=FakeRouter(), places=make_places(), favorites=favs)
    settings = Settings(None)
    a = Assistant(sig, settings, nav=nav, client=client)
    return sig, nav, a


def run(coro):
    return asyncio.run(coro)


def test_local_commands():
    sig, nav, a = setup()
    assert run(a.handle("What's the coolant temp?"))["reply"] == "Coolant temperature is 92 degrees."
    assert "14.2 volts" in run(a.handle("how's the battery"))["reply"]
    assert "don't have a current oil pressure" in run(a.handle("what is the oil pressure"))["reply"]
    out = run(a.handle("Take me home"))
    assert out["source"] == "local" and out["reply"].startswith("Navigating to Home") and nav.nav is not None
    assert "recenter" in out["actions"]
    nav.publish()
    assert "minutes" in run(a.handle("how far is it"))["reply"]
    assert run(a.handle("stop navigation"))["reply"] == "Navigation stopped." and nav.nav is None
    assert run(a.handle("stop navigation"))["reply"] == "Navigation isn't running."
    sig.set_gps({"lat": 49.157, "lon": -121.951, "heading": 0, "speed": 0})  # in Chilliwack
    assert run(a.handle("navigate to tim hortons"))["reply"].startswith("Navigating to Tim Hortons in Chilliwack")
    assert run(a.handle("recenter the map"))["actions"] == ["recenter"]
    assert run(a.handle("Hey truck, zoom in"))["actions"] == ["zoom_in"]
    assert run(a.handle("show diagnostics"))["actions"] == ["show_diagnostics"]
    assert run(a.handle("go to trips"))["actions"] == ["show_trips"]
    assert run(a.handle("show the tow view"))["actions"] == ["view_tow"]
    assert run(a.handle("switch to all gauges"))["actions"] == ["view_engine"]
    assert run(a.handle("navigate to zzqx"))["reply"] == "I couldn't find zzqx."


def test_without_key_falls_back_politely():
    _, _, a = setup()
    out = run(a.handle("is my truck ok to tow 7000 pounds up the Coquihalla?"))
    assert out["source"] == "claude" and "API key" in out["reply"]


class FakeMessages:
    """Scripted replies: first asks for the truck status, then answers."""

    def __init__(self):
        self.calls = []

    async def create(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})  # the caller keeps appending to its list
        if len(self.calls) == 1:
            return NS(stop_reason="tool_use", content=[
                NS(type="tool_use", id="t1", name="get_truck_status", input={}),
                NS(type="tool_use", id="t2", name="control_screen", input={"action": "show_truck"})])
        return NS(stop_reason="end_turn", content=[NS(type="text", text="Coolant is 92 degrees, all normal.")])


def test_claude_loop_with_tools():
    fake = FakeMessages()
    client = NS(beta=NS(messages=fake))
    sig, nav, a = setup(client=client)
    out = run(a.handle("give me a quick health check"))
    assert out == {"actions": ["show_truck"], "reply": "Coolant is 92 degrees, all normal.", "source": "claude"}
    first, second = fake.calls
    assert first["model"] == "claude-haiku-5-5" and first["fallbacks"] == "default"
    assert first["output_config"] == {"effort": "low"} and first["tools"] == TOOLS
    results = second["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"] and '"coolant"' in results[0]["content"]
    # Follow-up questions keep the conversation for a few minutes.
    run(a.handle("and the transmission?"))
    assert len(fake.calls[-1]["messages"]) == 5
    a.last_used = time.time() - 600
    run(a.handle("new topic"))
    assert len(fake.calls[-1]["messages"]) == 1


def test_tools_are_strict_and_clear_codes_is_not_one():
    names = {t["name"] for t in TOOLS}
    assert "clear_trouble_codes" not in names and all(t["strict"] for t in TOOLS)
    assert all(t["input_schema"]["additionalProperties"] is False for t in TOOLS)


def test_api_key_is_masked(tmp_path):
    s = Settings(tmp_path / "settings.json")
    client = TestClient(create_app(SignalBus(), settings=s))
    r = client.put("/api/settings", json={"anthropic_key": "sk-ant-api03-secretsecret-WXYZ"}).json()
    assert r["anthropic_key"] == "…WXYZ"
    assert client.get("/api/settings").json()["anthropic_key"] == "…WXYZ"
    client.put("/api/settings", json={"anthropic_key": "…WXYZ", "voice": False})  # masked value round-trips
    assert s["anthropic_key"].endswith("secretsecret-WXYZ") and s["voice"] is False
    assert mask({"anthropic_key": ""})["anthropic_key"] == ""
    assert client.post("/api/assistant", json={"text": "recenter"}).json()["actions"] == ["recenter"]
