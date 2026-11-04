"""Keep protocol tests away from the user's running Anki and external services."""

import socket
from collections.abc import Generator

import pytest

from tests.network import OWNED_PORTS


@pytest.fixture(autouse=True)
def isolate_network(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    connect = socket.socket.connect
    OWNED_PORTS.clear()

    def guarded_connect(sock: socket.socket, address: object) -> None:
        if (
            not isinstance(address, tuple)
            or len(address) < 2
            or address[0] != "127.0.0.1"
            or address[1] not in OWNED_PORTS
        ):
            raise AssertionError("Tests can connect only to their own protocol servers")
        connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    try:
        yield
    finally:
        OWNED_PORTS.clear()
