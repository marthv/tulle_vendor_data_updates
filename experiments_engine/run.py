"""Start the experiments-engine HTTP server on one dual-stack socket.

`uvicorn --host ::` can end up IPv6-only (IPV6_V6ONLY=1). Railway's healthcheck connects over IPv4,
and older private networks use IPv6. Binding one AF_INET6 socket with IPV6_V6ONLY=0 serves both.
Falls back to IPv4-only if the host has no IPv6.
"""
import os
import socket

import uvicorn


def _socket(port: int) -> socket.socket:
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("::", port))
    except OSError:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
    s.listen(128)
    s.set_inheritable(True)
    return s


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    sock = _socket(port)
    print(f"experiments-engine listening on [::]:{port} (dual-stack)", flush=True)
    config = uvicorn.Config("experiments_engine.service:app", workers=1, access_log=False, log_level="info")
    uvicorn.Server(config).run(sockets=[sock])


if __name__ == "__main__":
    main()
