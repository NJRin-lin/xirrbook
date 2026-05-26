#!/bin/bash
cd "$(dirname "$0")"
echo "正在启动投资回报率记录工具..."
python3 -m uvicorn backend.main:app --host 127.0.0.1 --port 8123 --reload
