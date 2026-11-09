@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; Set-Location '%~dp0'; Write-Host '[ACF // CLI] Launching Desktop Operations Workstation...' -ForegroundColor Cyan; & '.\.venv\Scripts\python.exe' desktop.py"
