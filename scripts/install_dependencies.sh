#!/bin/bash
# Script to install dependencies for the LMNT Marketplace Plugin

set -e

echo "LMNT Marketplace Plugin Dependencies Installer"
echo "=============================================="

# Define directories
MOONRAKER_ENV="${HOME}/moonraker-env"

# Check if the Moonraker virtual environment exists
if [ ! -d "${MOONRAKER_ENV}" ]; then
    echo "ERROR: Moonraker virtual environment not found at ${MOONRAKER_ENV}"
    echo "Please make sure Moonraker is installed correctly."
    exit 1
fi

echo "Installing dependencies in Moonraker virtual environment..."
source "${MOONRAKER_ENV}/bin/activate"

# Install PyJWT for JSON Web Token handling
echo "Installing PyJWT..."
pip install PyJWT

# Install any other dependencies your plugin needs
echo "Installing other dependencies (including PyNaCl for DLT crypto)..."
pip install requests cryptography PyNaCl>=1.5.0

# Deactivate the virtual environment
deactivate

echo "Dependencies installed successfully."
echo "Restarting Moonraker..."
sudo systemctl restart moonraker

echo "Waiting for Moonraker to start..."
sleep 5

echo "Checking Moonraker logs for errors..."
tail -n 20 ~/printer_data/logs/moonraker.log

echo "Installation completed. If you still see errors, please check the logs for more details."
