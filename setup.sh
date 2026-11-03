#!/usr/bin/env bash
set -e
python3 -m venv .venv
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
[ -f .env ] || cp .env.example .env
echo ""
echo "Setup done. Now: 1) put your API key in .env  2) start with:"
echo "  source .venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port 8000"
