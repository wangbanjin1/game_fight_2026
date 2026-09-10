#!/bin/bash
# 判题系统启动入口：bash run.sh <port>
cd "$(dirname "$0")"
python3 src/main.py "$1" || python src/main.py "$1"
