@echo off
chcp 65001 >nul
rem 启动 whichllm 图形界面（保留控制台，便于查看报错）
cd /d "%~dp0"
python run_gui.py
if errorlevel 1 pause
