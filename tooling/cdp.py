"""Small Chrome DevTools Protocol client using only the standard library."""
from __future__ import annotations

import base64
import fcntl
import json
import os
import socket
import struct
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class CDPError(RuntimeError):
    pass


class CDPClient:
    def __init__(self, endpoint: str = "http://127.0.0.1:9222") -> None:
        self.endpoint = endpoint.rstrip("/")
        self.sock: socket.socket | None = None
        self._next_id = 0
        self.session_id: str | None = None
        self._lock_file: Any = None

    def __enter__(self) -> "CDPClient":
        self._lock_file = open("/tmp/judgementday_cdp.lock", "w", encoding="utf-8")
        fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX)
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
        if self._lock_file:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()
            self._lock_file = None

    def connect(self) -> None:
        targets = json.load(urllib.request.urlopen(f"{self.endpoint}/json/list", timeout=3))
        page = next((p for p in targets if p.get("type") == "page"), None)
        if not page:
            raise CDPError("no page target found")
        self.sock = self._open_ws(page["webSocketDebuggerUrl"])
        # Page-target websocket accepts Runtime/Page/DOM commands directly. Avoid Page.enable
        # because some remote Chrome builds stop responding to it after repeated attach/detach.
        self.session_id = None

    def close(self) -> None:
        if self.sock:
            self.sock.close()
            self.sock = None

    def _open_ws(self, ws_url: str) -> socket.socket:
        u = urllib.parse.urlparse(ws_url)
        sock = socket.create_connection((u.hostname, u.port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + (("?" + u.query) if u.query else "")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {u.hostname}:{u.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += sock.recv(4096)
        status = resp.split(b"\r\n", 1)[0]
        if b"101" not in status:
            raise CDPError(f"websocket handshake failed: {status.decode(errors='replace')}")
        sock.settimeout(1.0)
        return sock

    def _send_frame(self, payload: dict[str, Any]) -> None:
        if not self.sock:
            raise CDPError("not connected")
        data = json.dumps(payload).encode()
        header = bytearray([0x81])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", n))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", n))
        mask = os.urandom(4)
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(header + masked)

    def _recv_frame(self) -> str | None:
        if not self.sock:
            raise CDPError("not connected")
        try:
            head = self.sock.recv(2)
        except socket.timeout:
            return None
        if not head:
            raise CDPError("socket closed")
        b1, b2 = head
        opcode = b1 & 0x0F
        masked = b2 & 0x80
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack("!H", self._recv_exact(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else b""
        data = b""
        while len(data) < n:
            data += self._recv_exact(n - len(data))
        if masked:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        if opcode == 8:
            raise CDPError("websocket closed")
        if opcode in (1, 2):
            return data.decode(errors="replace")
        return None

    def _recv_exact(self, size: int) -> bytes:
        if not self.sock:
            raise CDPError("not connected")
        data = b""
        while len(data) < size:
            try:
                chunk = self.sock.recv(size - len(data))
            except socket.timeout:
                continue
            if not chunk:
                raise CDPError("socket closed")
            data += chunk
        return data

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = 10,
        *,
        use_session: bool = True,
    ) -> dict[str, Any]:
        self._next_id += 1
        msg_id = self._next_id
        payload: dict[str, Any] = {"id": msg_id, "method": method, "params": params or {}}
        if use_session and self.session_id:
            payload["sessionId"] = self.session_id
        self._send_frame(payload)
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self._recv_frame()
            if not msg:
                continue
            obj = json.loads(msg)
            if obj.get("id") == msg_id:
                if "error" in obj:
                    raise CDPError(json.dumps(obj["error"], ensure_ascii=False))
                return obj
        raise TimeoutError(method)

    def evaluate(self, expression: str, *, timeout: float = 10) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        return result.get("result", {}).get("result", {}).get("value")

    def navigate(self, url: str, wait: bool = True) -> None:
        self.call("Page.navigate", {"url": url})
        if wait:
            for _ in range(100):
                time.sleep(0.1)
                ready = self.evaluate("document.readyState + '|' + location.href", timeout=3)
                if isinstance(ready, str) and ready.startswith("complete|") and url.split("#")[0] in ready:
                    return
            raise TimeoutError(f"page did not load: {url}")

    def set_file_input(self, selector: str, path: str | Path) -> bool:
        root = self.call("DOM.getDocument", {"depth": 1})["result"]["root"]["nodeId"]
        node = self.call("DOM.querySelector", {"nodeId": root, "selector": selector})["result"]["nodeId"]
        if not node:
            return False
        self.call("DOM.setFileInputFiles", {"nodeId": node, "files": [str(Path(path).resolve())]})
        return True
