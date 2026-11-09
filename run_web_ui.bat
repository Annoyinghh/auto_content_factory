@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; Set-Location '%~dp0'; Write-Host '[ACF // STUDIO] Starting Broadcast Operations Workbench on http://127.0.0.1:8765 ...' -ForegroundColor Yellow; & '.\.venv\Scripts\python.exe' studio_web.py"
