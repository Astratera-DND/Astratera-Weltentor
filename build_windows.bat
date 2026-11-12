@echo off
REM Local build of the portable Windows version (same steps as the GitHub workflow).
REM Needs: Python 3.11+ (64-bit) on PATH. VLC is downloaded automatically into vlc_bundle\ (tools\fetch_vlc.py).
REM The window stays open at the end, and everything is also written to build_log.txt.
setlocal
cd /d "%~dp0"
echo Building Astratera Weltentor... (details are written to build_log.txt)
echo Build started %date% %time% > build_log.txt

python --version >> build_log.txt 2>&1
if errorlevel 1 (
  echo.
  echo ERROR: Python was not found. Install 64-bit Python 3.11 or newer and tick "Add python.exe to PATH".
  goto :failed
)

echo [1/3] Installing the Python packages...
python -m pip install -r requirements-dev.txt >> build_log.txt 2>&1
if errorlevel 1 (
  echo.
  echo ERROR: installing the packages failed. See the end of build_log.txt.
  goto :failed
)

echo [2/3] Getting VLC...
if not exist vlc_bundle\libvlc.dll (
  python tools/fetch_vlc.py >> build_log.txt 2>&1
  if errorlevel 1 (
    echo.
    echo ERROR: could not download VLC. See the end of build_log.txt.
    goto :failed
  )
)

echo [3/3] Building the exe, this takes a few minutes...
python -m PyInstaller --noconfirm --clean --noconsole --onefile --name Astratera-Weltentor ^
  --icon assets/icon.ico --add-data "assets;assets" ^
  --add-data "vlc_bundle;vlc" --collect-all imageio_ffmpeg --hidden-import quickfire_engine --hidden-import lights --hidden-import light_designer --hidden-import transcriber --hidden-import transcribe_dialog --hidden-import vocab --hidden-import dnd_terms --hidden-import vocab_dialog --hidden-import speakers --hidden-import speakers_dialog --collect-binaries sherpa_onnx --collect-data sherpa_onnx --hidden-import sherpa_onnx --exclude-module sentencepiece main.py >> build_log.txt 2>&1
if errorlevel 1 (
  echo.
  echo ERROR: PyInstaller failed. See the end of build_log.txt.
  goto :failed
)

if not exist dist\Astratera-Weltentor.exe (
  echo.
  echo ERROR: the build finished but dist\Astratera-Weltentor.exe is missing.
  echo Your antivirus probably removed it. Allow the dist folder in your antivirus and run this again.
  goto :failed
)

echo.
echo Done: dist\Astratera-Weltentor.exe
pause
exit /b 0

:failed
echo.
echo ---- last lines of build_log.txt ----
powershell -NoProfile -Command "Get-Content build_log.txt -Tail 15"
echo -------------------------------------
pause
exit /b 1
