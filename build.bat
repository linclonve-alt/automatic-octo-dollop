@echo off
rem Optional local build on a Windows PC. The easy way is GitHub Actions (.github/workflows/build.yml).
pip install fastapi uvicorn requests pyinstaller pywebview tzdata
set EXTRA=
if exist server.py set EXTRA=--hidden-import server
pyinstaller --noconfirm --onedir --noconsole --name DayTradeRiskManager --add-data "static;static" --hidden-import server_oanda --hidden-import daylock --hidden-import webview --collect-submodules uvicorn --collect-data tzdata %EXTRA% trader_app.py
echo Done: dist\DayTradeRiskManager\DayTradeRiskManager.exe
echo Now open installer.iss in Inno Setup and click Compile.
