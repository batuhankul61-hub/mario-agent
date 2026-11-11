"""Wake-on-LAN: finds computers on the local network and wakes them with a magic packet.

A computer that is off doesn't answer on the network, so the flow is: scan while
it is on (scan_network), save it under a name (save_wake_device), wake it later
(wake_device). MAC addresses only come from .env (WOL_DEVICES) or from this PC's
own neighbor table, never from text the model read somewhere."""

import asyncio
import ipaddress
import json
import re
import socket
import subprocess
from pathlib import Path

from ..config import Settings
from .system import NO_WINDOW, plain

_MAC = re.compile(r"^([0-9a-f]{2})([:\-]?)([0-9a-f]{2})(\2[0-9a-f]{2}){4}$", re.IGNORECASE)
# Neighbor states worth listing; Unreachable/Incomplete have no usable MAC.
_LIVE_STATES = {"Reachable", "Stale", "Delay", "Probe", "Permanent"}
_MAX_SWEEP = 1024  # hosts; wider subnets are cut to the /24 around this PC
_NAME_TIMEOUT = 2.5  # seconds for all reverse lookups together


def parse_mac(text: str) -> bytes | None:
    text = text.strip()
    if not _MAC.match(text):
        return None
    return bytes.fromhex(re.sub(r"[:\-]", "", text))


def format_mac(mac: bytes) -> str:
    return ":".join(f"{b:02X}" for b in mac)


def magic_packet(mac: bytes) -> bytes:
    return b"\xff" * 6 + mac * 16


def _split(entry: str, default_host: str) -> tuple[str, str]:
    """'AA:BB:..' or 'AA:BB:..@192.168.1.255' -> (mac, broadcast address)."""
    mac, _, host = entry.partition("@")
    return mac.strip(), (host.strip() or default_host)


# ---------- saved devices ----------


def _saved_file(settings: Settings) -> Path:
    return settings.data_dir / "wol_devices.json"


def _scan_file(settings: Settings) -> Path:
    return settings.data_dir / "wol_scan.json"


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def all_devices(settings: Settings) -> dict[str, str]:
    """Devices saved by voice, then .env ones (which win on a name clash)."""
    saved = _read_json(_saved_file(settings), {})
    return {**(saved if isinstance(saved, dict) else {}), **settings.wol_devices}


def find_device(name: str, devices: dict[str, str]) -> tuple[str | None, list[str]]:
    """Exact name first, then a unique partial match. Returns (key, candidates)."""
    wanted = plain(name)
    for key in devices:
        if plain(key) == wanted:
            return key, [key]
    hits = [k for k in devices if wanted and (wanted in plain(k) or plain(k) in wanted)]
    return (hits[0] if len(hits) == 1 else None), hits


# ---------- scanning ----------

_PS_NETWORKS = (
    "Get-NetIPConfiguration | ? { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | "
    "% { [pscustomobject]@{ip=$_.IPv4Address[0].IPAddress; prefix=$_.IPv4Address[0].PrefixLength; "
    "gw=$_.IPv4DefaultGateway[0].NextHop; idx=$_.InterfaceIndex; "
    "mac=$_.NetAdapter.MacAddress} } | ConvertTo-Json -Compress"
)
_PS_NEIGHBORS = (
    "Get-NetNeighbor -AddressFamily IPv4 | "
    "Select IPAddress, LinkLayerAddress, InterfaceIndex, @{n='State';e={\"$($_.State)\"}} | "
    "ConvertTo-Json -Compress"
)


def _ps_json(command: str) -> list[dict]:
    out = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        creationflags=NO_WINDOW,
    ).stdout.strip()
    if not out:
        return []
    data = json.loads(out)
    return data if isinstance(data, list) else [data]


def _networks_sync() -> list[dict]:
    return _ps_json(_PS_NETWORKS)


def _neighbors_sync() -> list[dict]:
    return _ps_json(_PS_NEIGHBORS)


def sweep_targets(ip: str, prefix: int) -> list[str]:
    net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    if net.num_addresses > _MAX_SWEEP:
        net = ipaddress.ip_network(f"{ip}/24", strict=False)
    return [str(h) for h in net.hosts() if str(h) != ip]


def _sweep_sync(targets: list[str]) -> None:
    """One tiny UDP datagram to every address: Windows has to ARP each one first,
    so every machine that is on lands in the neighbor table, firewall or not."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        for host in targets:
            try:
                s.sendto(b"", (host, 9))
            except OSError:
                pass


def _name_sync(ip: str) -> str | None:
    try:
        return socket.gethostbyaddr(ip)[0].split(".")[0] or None
    except OSError:
        return None


async def _names(ips: list[str]) -> dict[str, str | None]:
    tasks = {ip: asyncio.ensure_future(asyncio.to_thread(_name_sync, ip)) for ip in ips}
    await asyncio.wait(tasks.values(), timeout=_NAME_TIMEOUT)
    return {ip: (t.result() if t.done() and not t.exception() else None) for ip, t in tasks.items()}


def _is_unicast(mac: bytes) -> bool:
    return mac != b"\x00" * 6 and not mac[0] & 1  # low bit of first byte = multicast/broadcast


def pick_devices(networks: list[dict], neighbors: list[dict]) -> list[dict]:
    """Neighbors on this PC's networks, minus the router, this PC and multicast."""
    by_idx = {n["idx"]: n for n in networks}
    found: dict[bytes, dict] = {}
    for n in neighbors:
        net = by_idx.get(n.get("InterfaceIndex"))
        mac = parse_mac(n.get("LinkLayerAddress") or "")
        ip = n.get("IPAddress") or ""
        if not net or not mac or not _is_unicast(mac) or n.get("State") not in _LIVE_STATES:
            continue
        if ip in (net["gw"], net["ip"]) or mac == parse_mac(net.get("mac") or ""):
            continue
        if ipaddress.ip_address(ip) not in ipaddress.ip_network(
            f"{net['ip']}/{net['prefix']}", strict=False
        ):
            continue
        # Locally administered bit: phones and tablets use a random MAC per network.
        found.setdefault(mac, {"ip": ip, "mac": format_mac(mac), "random_mac": bool(mac[0] & 2)})
    # Real (vendor) MACs first: those are the computers worth waking.
    return sorted(found.values(), key=lambda d: (d["random_mac"], ipaddress.ip_address(d["ip"])))


async def scan_network(args: dict, settings: Settings) -> dict:
    try:
        networks = await asyncio.to_thread(_networks_sync)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {"status": "error", "message": f"Ağ bilgisi okunamadı: {exc}."}
    if not networks:
        return {"status": "error", "message": "Bu bilgisayar bir yerel ağa bağlı görünmüyor."}
    for net in networks:
        await asyncio.to_thread(_sweep_sync, sweep_targets(net["ip"], net["prefix"]))
    await asyncio.sleep(1.5)  # ARP replies
    try:
        neighbors = await asyncio.to_thread(_neighbors_sync)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {"status": "error", "message": f"Ağdaki cihazlar okunamadı: {exc}."}

    devices = pick_devices(networks, neighbors)
    names = await _names([d["ip"] for d in devices])
    saved = {parse_mac(_split(v, "")[0]): k for k, v in all_devices(settings).items()}
    for i, d in enumerate(devices, 1):
        d["number"] = i
        d["name"] = names.get(d["ip"])
        d["saved_as"] = saved.get(parse_mac(d["mac"]))
    _write_json(_scan_file(settings), devices)

    if not devices:
        return {"status": "ok", "message": "Ağda açık başka cihaz bulamadım.", "devices": []}

    def label(d: dict) -> str:
        unknown = "telefon ya da tablet olabilir" if d["random_mac"] else "adı bilinmeyen cihaz"
        text = f"{d['number']}) {d['name'] or unknown} ({d['ip']})"
        return text + (f", kayıtlı adı «{d['saved_as']}»" if d["saved_as"] else "")

    return {
        "status": "ok",
        "message": (
            f"Ağda {len(devices)} açık cihaz buldum: {'; '.join(label(d) for d in devices[:15])}. "
            "Hangisini uyandırmak ya da kaydetmek istersin?"
        ),
        "devices": devices,
    }


def _from_scan(number, settings: Settings) -> dict | None:
    try:
        n = int(number)
    except (TypeError, ValueError):
        return None
    scan = _read_json(_scan_file(settings), [])
    return next((d for d in scan if isinstance(d, dict) and d.get("number") == n), None)


async def save_wake_device(args: dict, settings: Settings) -> dict:
    device = _from_scan(args.get("number"), settings)
    if device is None:
        return {
            "status": "error",
            "message": "Bu numarada cihaz yok. Önce ağı tarayayım mı?",
        }
    name = (args.get("name") or "").strip() or device.get("name") or device["ip"]
    if name in settings.wol_devices:
        return {
            "status": "error",
            "message": f"«{name}» adı .env'de zaten kullanılıyor, başka ad söyle.",
        }
    saved = _read_json(_saved_file(settings), {})
    saved = saved if isinstance(saved, dict) else {}
    # One name per machine: renaming replaces the old entry.
    saved = {
        k: v for k, v in saved.items() if parse_mac(_split(v, "")[0]) != parse_mac(device["mac"])
    }
    saved[name] = device["mac"]
    _write_json(_saved_file(settings), saved)
    return {
        "status": "ok",
        "message": f"{device.get('name') or device['ip']} cihazını «{name}» adıyla kaydettim; "
        f"artık kapalıyken de uyandırabilirim.",
    }


# ---------- waking ----------


def _send(packet: bytes, host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # A few copies: UDP may drop one and a sleeping NIC costs nothing to retry.
        for _ in range(3):
            s.sendto(packet, (host, port))


async def wake_device(args: dict, settings: Settings) -> dict:
    devices = all_devices(settings)
    name = (args.get("name") or "").strip()

    if args.get("number") is not None and not name:
        found = _from_scan(args["number"], settings)
        if found is None:
            return {"status": "error", "message": "Bu numarada cihaz yok. Önce ağı tarayayım mı?"}
        key, entry = (found.get("saved_as") or found.get("name") or found["ip"]), found["mac"]
    elif not devices:
        return {
            "status": "error",
            "message": "Kayıtlı cihaz yok. Ağı tarayıp açık cihazları sayayım mı?",
        }
    elif not name:
        return {
            "status": "ok",
            "message": f"Kayıtlı cihazlar: {', '.join(devices)}. Hangisini uyandırayım?",
            "devices": list(devices),
        }
    else:
        key, hits = find_device(name, devices)
        if key is None:
            if hits:
                return {
                    "status": "ambiguous",
                    "message": f"Birden fazla eşleşme var: {', '.join(hits)}. Hangisi?",
                }
            return {
                "status": "error",
                "message": f"'{name}' adında kayıtlı cihaz yok. Kayıtlılar: {', '.join(devices)}. "
                "Ağı tarayayım mı?",
            }
        entry = devices[key]

    mac_text, host = _split(entry, settings.wol_broadcast)
    mac = parse_mac(mac_text)
    if mac is None:
        return {"status": "error", "message": f"{key} için MAC adresi geçersiz: {mac_text}."}
    if settings.dry_run:
        return {"status": "ok", "message": f"(deneme) {key} uyandırılacaktı.", "host": host}
    try:
        await asyncio.to_thread(_send, magic_packet(mac), host, settings.wol_port)
    except OSError as exc:
        return {
            "status": "error",
            "message": f"Uyandırma paketi gönderilemedi: {exc.strerror or exc}.",
        }
    return {
        "status": "ok",
        "message": f"{key} için uyandırma paketi gönderdim; açılması birkaç saniye sürebilir.",
        "host": host,
    }
