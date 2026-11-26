import asyncio
import json

from dashd.gpsd import follow, tpv_to_fix


def test_tpv_to_fix():
    fix = tpv_to_fix({"class": "TPV", "mode": 3, "lat": 49.13, "lon": -122.31, "track": 271.04, "speed": 25.0})
    assert fix == {"lat": 49.13, "lon": -122.31, "mode": "3d", "source": "gpsd", "heading": 271.0, "speed": 90.0}
    assert tpv_to_fix({"class": "TPV", "mode": 1}) is None  # no fix yet
    assert tpv_to_fix({"class": "SKY", "mode": 3, "lat": 1, "lon": 2}) is None
    assert tpv_to_fix({"class": "TPV", "mode": 2, "lat": 1, "lon": 2})["mode"] == "2d"


def test_follow_streams_from_fake_gpsd():
    async def run():
        got = []
        stop = asyncio.Event()
        watched = asyncio.Event()

        async def gpsd(reader, writer):
            assert (await reader.readline()).startswith(b"?WATCH=")
            watched.set()
            for r in ({"class": "VERSION"}, {"class": "TPV", "mode": 1}, "not json",
                      {"class": "TPV", "mode": 3, "lat": 49.1, "lon": -122.3, "speed": 10}):
                writer.write((r if isinstance(r, str) else json.dumps(r)).encode() + b"\n")
            await writer.drain()

        server = await asyncio.start_server(gpsd, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        def on_fix(f):
            got.append(f)
            stop.set()

        await asyncio.wait_for(follow(on_fix, stop, port=port, retry_s=0.1), timeout=5)
        server.close()
        assert watched.is_set()
        assert got == [{"lat": 49.1, "lon": -122.3, "mode": "3d", "source": "gpsd", "speed": 36.0}]

    asyncio.run(run())


def test_follow_survives_no_gpsd():
    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(follow(lambda f: None, stop, port=1, retry_s=0.05))
        await asyncio.sleep(0.2)
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(run())
