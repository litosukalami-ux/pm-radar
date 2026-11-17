@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在抓取最新信息...
python radar.py
echo.
echo 看板已生成：public\index.html
echo 按任意键打开看板...
pause >nul
start "" "public\index.html"
