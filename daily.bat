@echo off
rem 每日静默抓取（给 Windows 任务计划程序调用，不弹窗、不暂停）
chcp 65001 >nul
cd /d "%~dp0"
if not exist logs mkdir logs

set PYTHON="C:\Users\grey\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe"
if not exist %PYTHON% set PYTHON=python

%PYTHON% radar.py >> logs\radar.log 2>&1
