"""Wake-on-LAN with the socket faked: no test sends a packet."""

import pytest
from app.tools import wol

DEVICES = {
    "Masaüstü": "AA:BB:CC:DD:EE:FF",
    "Sunucu": "11-22-33-44-55-66@192.168.1.255",
    "İş bilgisayarı": "001122334455",
}


@pytest.fixture
def sent(monkeypatch):
    packets = []
    monkeypatch.setattr(wol, "_send", lambda p, host, port: packets.append((p, host, port)))
    return packets


@pytest.fixture
def wol_settings(settings):
    settings.wol_devices = dict(DEVICES)
    return settings


def test_magic_packet_layout():
    mac = wol.parse_mac("aa:bb:cc:dd:ee:ff")
    packet = wol.magic_packet(mac)
    assert len(packet) == 102
    assert packet[:6] == b"\xff" * 6
    assert packet[6:] == mac * 16


@pytest.mark.parametrize("text", ["AA:BB:CC:DD:EE:FF", "aa-bb-cc-dd-ee-ff", "aabbccddeeff"])
def test_parse_mac_formats(text):
    assert wol.parse_mac(text) == bytes.fromhex("aabbccddeeff")


@pytest.mark.parametrize("text", ["", "AA:BB:CC:DD:EE", "AA:BB-CC:DD:EE:FF", "GG:BB:CC:DD:EE:FF"])
def test_parse_mac_rejects(text):
    assert wol.parse_mac(text) is None


async def test_wakes_by_name_case_insensitive(wol_settings, sent):
    res = await wol.wake_device({"name": "masaüstü"}, wol_settings)
    assert res["status"] == "ok"
    packet, host, port = sent[0]
    assert packet == wol.magic_packet(bytes.fromhex("aabbccddeeff"))
    assert (host, port) == ("255.255.255.255", 9)


async def test_turkish_dotted_i_and_partial_match(wol_settings, sent):
    res = await wol.wake_device({"name": "iş"}, wol_settings)
    assert res["status"] == "ok" and "İş bilgisayarı" in res["message"]


async def test_per_device_broadcast(wol_settings, sent):
    await wol.wake_device({"name": "sunucu"}, wol_settings)
    assert sent[0][1] == "192.168.1.255"


async def test_unknown_device_lists_known_ones(wol_settings, sent):
    res = await wol.wake_device({"name": "yazıcı"}, wol_settings)
    assert res["status"] == "error" and "Masaüstü" in res["message"]
    assert sent == []


async def test_ambiguous(wol_settings, sent):
    wol_settings.wol_devices = {"Masaüstü 1": "AABBCCDDEEFF", "Masaüstü 2": "AABBCCDDEE00"}
    res = await wol.wake_device({"name": "masaüstü"}, wol_settings)
    assert res["status"] == "ambiguous"
    assert sent == []


async def test_empty_name_lists_devices(wol_settings, sent):
    res = await wol.wake_device({}, wol_settings)
    assert res["devices"] == list(DEVICES)
    assert sent == []


async def test_no_devices_offers_a_scan(settings, sent):
    res = await wol.wake_device({"name": "masaüstü"}, settings)
    assert res["status"] == "error" and "Ağı tara" in res["message"]


async def test_bad_mac_in_config(wol_settings, sent):
    wol_settings.wol_devices = {"Bozuk": "nope"}
    res = await wol.wake_device({"name": "bozuk"}, wol_settings)
    assert res["status"] == "error"
    assert sent == []


async def test_dry_run(wol_settings, sent):
    wol_settings.dry_run = True
    res = await wol.wake_device({"name": "masaüstü"}, wol_settings)
    assert res["message"].startswith("(deneme)")
    assert sent == []


def test_devices_parse_from_env_json(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("WOL_DEVICES", '{"masaüstü": "AA:BB:CC:DD:EE:FF"}')
    assert Settings(_env_file=None).wol_devices == {"masaüstü": "AA:BB:CC:DD:EE:FF"}


def test_tool_is_registered():
    from app.tools import TOOLS

    assert TOOLS["wake_device"].declaration()["parameters"]["properties"]["name"]


# ---------- scanning ----------

NETWORKS = [
    {
        "ip": "192.168.1.107",
        "prefix": 24,
        "gw": "192.168.1.1",
        "idx": 11,
        "mac": "00-11-22-33-44-55",
    }
]
NEIGHBORS = [
    {
        "IPAddress": "192.168.1.1",
        "LinkLayerAddress": "A8-AA-AA-AA-AA-01",
        "InterfaceIndex": 11,
        "State": "Reachable",
    },
    {
        "IPAddress": "192.168.1.30",
        "LinkLayerAddress": "A8-AA-AA-AA-AA-30",
        "InterfaceIndex": 11,
        "State": "Stale",
    },
    {
        "IPAddress": "192.168.1.20",
        "LinkLayerAddress": "A8-AA-AA-AA-AA-20",
        "InterfaceIndex": 11,
        "State": "Reachable",
    },
    {
        "IPAddress": "192.168.1.40",
        "LinkLayerAddress": "00-00-00-00-00-00",
        "InterfaceIndex": 11,
        "State": "Unreachable",
    },
    {
        "IPAddress": "192.168.1.255",
        "LinkLayerAddress": "FF-FF-FF-FF-FF-FF",
        "InterfaceIndex": 11,
        "State": "Permanent",
    },
    {
        "IPAddress": "224.0.0.251",
        "LinkLayerAddress": "01-00-5E-00-00-FB",
        "InterfaceIndex": 11,
        "State": "Permanent",
    },
    {
        "IPAddress": "10.0.0.5",
        "LinkLayerAddress": "BB-BB-BB-BB-BB-05",
        "InterfaceIndex": 20,
        "State": "Reachable",
    },
]


@pytest.fixture
def network(monkeypatch):
    swept = []
    monkeypatch.setattr(wol, "_networks_sync", lambda: NETWORKS)
    monkeypatch.setattr(wol, "_neighbors_sync", lambda: NEIGHBORS)
    monkeypatch.setattr(wol, "_sweep_sync", swept.extend)
    monkeypatch.setattr(wol, "_name_sync", {"192.168.1.20": "DESKTOP-ADA"}.get)

    async def no_wait(_):
        return None

    monkeypatch.setattr(wol.asyncio, "sleep", no_wait)
    return swept


def test_random_macs_listed_last():
    neighbors = [
        {
            "IPAddress": "192.168.1.5",
            "LinkLayerAddress": "8E-DE-F9-02-C1-5D",
            "InterfaceIndex": 11,
            "State": "Reachable",
        },
        {
            "IPAddress": "192.168.1.9",
            "LinkLayerAddress": "28-07-08-C6-93-10",
            "InterfaceIndex": 11,
            "State": "Reachable",
        },
    ]
    devices = wol.pick_devices(NETWORKS, neighbors)
    assert [(d["ip"], d["random_mac"]) for d in devices] == [
        ("192.168.1.9", False),
        ("192.168.1.5", True),
    ]


def test_pick_devices_drops_router_self_multicast_and_other_networks():
    devices = wol.pick_devices(NETWORKS, NEIGHBORS)
    assert [d["ip"] for d in devices] == ["192.168.1.20", "192.168.1.30"]


def test_sweep_skips_own_address_and_caps_wide_subnets():
    targets = wol.sweep_targets("10.1.2.3", 16)
    assert len(targets) == 253 and "10.1.2.3" not in targets


async def test_scan_lists_and_asks(settings, network):
    res = await wol.scan_network({}, settings)
    assert res["status"] == "ok"
    assert "1) DESKTOP-ADA (192.168.1.20)" in res["message"]
    assert "2) adı bilinmeyen cihaz (192.168.1.30)" in res["message"]
    assert "Hangisini" in res["message"]
    assert len(network) == 253


async def test_save_from_scan_then_wake_by_name(settings, network, sent):
    await wol.scan_network({}, settings)
    res = await wol.save_wake_device({"number": 1, "name": "Masaüstü"}, settings)
    assert res["status"] == "ok"
    await wol.wake_device({"name": "masaustu"}, settings)
    assert sent[0][0] == wol.magic_packet(bytes.fromhex("a8aaaaaaaa20"))
    again = await wol.scan_network({}, settings)
    assert "kayıtlı adı «Masaüstü»" in again["message"]


async def test_save_defaults_to_network_name_and_renaming_replaces(settings, network):
    await wol.scan_network({}, settings)
    await wol.save_wake_device({"number": 1}, settings)
    assert list(wol.all_devices(settings)) == ["DESKTOP-ADA"]
    await wol.save_wake_device({"number": 1, "name": "oyun pc"}, settings)
    assert wol.all_devices(settings) == {"oyun pc": "A8:AA:AA:AA:AA:20"}


async def test_wake_by_scan_number(settings, network, sent):
    await wol.scan_network({}, settings)
    res = await wol.wake_device({"number": 2}, settings)
    assert res["status"] == "ok"
    assert sent[0][0] == wol.magic_packet(bytes.fromhex("a8aaaaaaaa30"))


async def test_bad_scan_number(settings, network, sent):
    await wol.scan_network({}, settings)
    assert (await wol.wake_device({"number": 9}, settings))["status"] == "error"
    assert (await wol.save_wake_device({"number": 9}, settings))["status"] == "error"
    assert sent == []
