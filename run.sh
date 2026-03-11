#!/bin/bash
# Delega startup script

cd "$(dirname "$0")"

# Install dependencies if needed
if [ ! -d "backend/.venv" ]; then
    echo "Setting up Python environment..."
    cd backend
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    cd ..
else
    source backend/.venv/bin/activate
fi

# Run the server
cd backend
echo "⚡ Starting Delega on port 18890..."
python main.py
