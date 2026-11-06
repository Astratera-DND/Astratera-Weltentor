@echo off
REM Local build of the portable Windows version (same steps as the GitHub workflow).
REM Needs: Python 3.11+ on PATH. VLC is downloaded automatically into vlc_bundle\ (tools\fetch_vlc.py).
python -m pip install -r requirements-dev.txt || exit /b 1
if not exist vlc_bundle\libvlc.dll python tools/fetch_vlc.py || exit /b 1
python -m PyInstaller --noconfirm --clean --noconsole --onefile --name Astratera-Weltentor ^
  --icon assets/icon.ico --add-data "assets;assets" ^
  --add-data "vlc_bundle;vlc" --collect-all imageio_ffmpeg --hidden-import quickfire_engine main.py || exit /b 1
echo.
echo Done: dist\Astratera-Weltentor.exe
