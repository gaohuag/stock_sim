#!/bin/bash
# 模拟炒股系统 - 定时运行脚本
cd /workspace/stock_sim
/usr/bin/python3.11 simulator.py >> /workspace/stock_sim/cron.log 2>&1
