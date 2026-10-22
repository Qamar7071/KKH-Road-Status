@echo off
setlocal
cd /d "%~dp0"
title KKH Road Status - real data build
echo.
echo  ===================================================
echo   KKH Road Status: download real data and retrain
echo   Keep the laptop plugged in and awake (about 45 min)
echo  ===================================================
echo.
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.12 or newer from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during setup. Then run this file again.
  pause & exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
  echo Your Python is too old. Install Python 3.12 or newer from https://www.python.org/downloads/
  pause & exit /b 1
)
if not exist .venv (
  echo Creating a private Python environment...
  python -m venv .venv || goto :fail
)
call .venv\Scripts\activate.bat
python -m pip install --quiet --upgrade pip
echo Installing libraries (first time only, a few minutes)...
pip install --quiet -r requirements-dev.txt pillow || goto :fail

echo.
echo [1/5] Weather 2015-today, NASA landslides, map, photos (about 5 min)...
python scripts\fetch_real_data.py --only weather,nasa,osm,terrain,photos
echo.
echo [2/5] KKH news archive 2017-today from GDELT (30-45 min)...
python scripts\fetch_real_data.py --only gdelt
echo.
echo [3/5] Building the dataset from real data...
python -m kkh.dataset --mode real || goto :fail
echo.
echo [4/5] Training, auditing and testing the model...
python -m kkh.model || goto :fail
python scripts\audit_model.py > models\audit_log.txt 2>&1
python -m pytest -q
echo.
echo [5/5] Packing the results...
powershell -NoProfile -Command "Compress-Archive -Force -Path data\raw,data\processed,models -DestinationPath real_data_bundle.zip" || goto :fail
echo.
echo  Done. Upload real_data_bundle.zip (in this folder) to Claude.
echo  To see the app now: run  flask --app app.main run  and open http://127.0.0.1:5000
pause
exit /b 0

:fail
echo.
echo  Something failed. Take a screenshot of the error above and send it to Claude.
pause
exit /b 1
