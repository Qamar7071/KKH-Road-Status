#!/usr/bin/env bash
# Download real data, rebuild the dataset, retrain and audit. macOS / Linux.
set -e
cd "$(dirname "$0")"
python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" || { echo "Need Python 3.11+"; exit 1; }
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q --upgrade pip && pip install -q -r requirements-dev.txt pillow
echo "[1/5] weather, landslides, map, photos"; python scripts/fetch_real_data.py --only weather,nasa,osm,terrain,photos || true
echo "[2/5] GDELT news archive (30-45 min)";   python scripts/fetch_real_data.py --only gdelt || true
echo "[3/5] dataset";  python -m kkh.dataset --mode real
echo "[4/5] train + audit + tests"; python -m kkh.model && python scripts/audit_model.py > models/audit_log.txt 2>&1; python -m pytest -q || true
echo "[5/5] packing"; zip -qr real_data_bundle.zip data/raw data/processed models
echo "Done. Upload real_data_bundle.zip to Claude."
