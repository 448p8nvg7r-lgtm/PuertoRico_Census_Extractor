@echo off
rem Double-click to build the Windows installer (dist\installer\CensusExtractor-Setup-*.exe)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows.ps1" %*
echo.
pause
