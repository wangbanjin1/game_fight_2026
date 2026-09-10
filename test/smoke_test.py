# -*- coding: utf-8 -*-
"""冒烟测试：启动服务，POST docs/request.txt，校验响应结构。"""
import json
import subprocess
import sys
import time
import urllib.request

PORT = 18231
ROOT = r"D:\coding_fight"


def main() -> int:
    proc = subprocess.Popen(
        [sys.executable, ROOT + r"\src\main.py", str(PORT)],
        cwd=ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        time.sleep(1.5)
        with open(ROOT + r"\docs\request.txt", encoding="utf-8") as f:
            body = f.read().encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8")
        data = json.loads(text)
        assert "roleCommandMap" in data, "missing roleCommandMap"
        assert "prompt" in data and "executeCmd" in data
        print("HTTP 200, response:")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    finally:
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
