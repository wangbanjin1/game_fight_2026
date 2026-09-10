# -*- coding: utf-8 -*-
"""HTTP 服务入口。

判题系统通过 HTTP POST 下发每回合地图状态，本服务返回角色调度指令。
任何内部异常都必须兜底为合法的空响应，避免被判为"响应格式错误"。

用法：python main.py <port>
"""
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from models import parse
from strategy import Strategy

strategy = Strategy()

_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def _loads_tolerant(raw: bytes) -> dict:
    """宽松解析 JSON：样例/异常报文中可能存在尾逗号"""
    text = raw.decode("utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(_TRAILING_COMMA.sub(r"\1", text))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            data = _loads_tolerant(raw)
            resp = strategy.decide(parse(data))
        except Exception:
            resp = {"roleCommandMap": {}, "prompt": "", "executeCmd": ""}
        self._send(resp)

    def do_GET(self) -> None:
        self._send({"status": "ok"})

    def _send(self, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # 静默访问日志
        pass


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"future-war player listening on 0.0.0.0:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
