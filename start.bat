@echo off
REM SysMon — 桌面系统监控条启动脚本
REM 双击运行，或放入 shell:startup 开机自启
cd /d "%~dp0"
start /min "" .venv\Scripts\pythonw.exe sysmon.pyw
