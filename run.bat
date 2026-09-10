@echo off
rem 本地调试启动入口：run.bat <port>
cd /d %~dp0
python src\main.py %1
