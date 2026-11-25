#!/usr/bin/python3
"""Root-owned refresh for the narrow nftables DeepInfra destination set."""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

HOST = "api.deepinfra.com"
NFT = "/usr/sbin/nft"
RULES = "/etc/yachay-deepinfra.nft"
OUTPUT = Path("/run/yachay-deepinfra-firewall/provider-ips.json")
MAX_ADDRESSES = 32


def _addresses() -> tuple[list[str], list[str]]:
    answers = socket.getaddrinfo(HOST, 443, type=socket.SOCK_STREAM)
    addresses = sorted({answer[4][0] for answer in answers})
    if not addresses or len(addresses) > MAX_ADDRESSES:
        raise RuntimeError("DeepInfra DNS returned an invalid address count")
    parsed = [ipaddress.ip_address(value) for value in addresses]
    if any(not address.is_global for address in parsed):
        raise RuntimeError("DeepInfra DNS returned a non-public address")
    ipv4 = [str(address) for address in parsed if address.version == 4]
    ipv6 = [str(address) for address in parsed if address.version == 6]
    return ipv4, ipv6


def _ensure_table() -> None:
    present = subprocess.run(
        [NFT, "list", "table", "inet", "yachay_deepinfra"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if present.returncode == 0:
        return
    subprocess.run([NFT, "-f", RULES], check=True, timeout=10)


def _update_sets(ipv4: list[str], ipv6: list[str]) -> None:
    commands = [
        "flush set inet yachay_deepinfra provider_v4",
        "flush set inet yachay_deepinfra provider_v6",
    ]
    if ipv4:
        commands.append(
            "add element inet yachay_deepinfra provider_v4 { "
            + ", ".join(ipv4) + " }"
        )
    if ipv6:
        commands.append(
            "add element inet yachay_deepinfra provider_v6 { "
            + ", ".join(ipv6) + " }"
        )
    subprocess.run(
        [NFT, "-f", "-"],
        input=("\n".join(commands) + "\n").encode("ascii"),
        check=True,
        timeout=10,
    )


def _publish_ip_file(ipv4: list[str], ipv6: list[str]) -> None:
    OUTPUT.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    document = {
        "host": HOST,
        "addresses": ipv4 + ipv6,
        "refreshed_at": time.time(),
    }
    descriptor, temporary = tempfile.mkstemp(
        prefix=".provider-ips-", dir=OUTPUT.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o644)
        os.replace(temporary, OUTPUT)
        directory_fd = os.open(OUTPUT.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main() -> int:
    ipv4, ipv6 = _addresses()
    _ensure_table()
    _update_sets(ipv4, ipv6)
    _publish_ip_file(ipv4, ipv6)
    print(json.dumps({
        "component": "yachay-deepinfra-firewall",
        "addresses": len(ipv4) + len(ipv6),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
