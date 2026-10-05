@echo off
chcp 65001 >nul
rem 启动 whichllm 图形界面（无控制台窗口）
cd /d "%~dp0"
start "" pythonw run_gui.py
