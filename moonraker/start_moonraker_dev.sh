#!/bin/bash

# Set environment variables
export MOONRAKER_DATA_PATH=$(pwd)/data
export MOONRAKER_CONFIG_PATH=$(pwd)

# Create necessary directories
mkdir -p $MOONRAKER_DATA_PATH

# Start Moonraker in development mode
cd $(pwd)/moonraker
python3 -m moonraker \
  --config $(pwd)/moonraker.conf \
  --nologfile \
  --debug
