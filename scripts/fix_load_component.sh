#!/bin/bash
# Script to fix the load_component function in the LMNT Marketplace Plugin

set -e

# Define directories
PLUGIN_NAME="lmnt-marketplace"
PLUGIN_DIR="${HOME}/${PLUGIN_NAME}"
MOONRAKER_DIR="${HOME}/moonraker"
COMPONENT_DIR="${MOONRAKER_DIR}/moonraker/components"

# Check if the plugin file exists
if [ ! -f "${COMPONENT_DIR}/lmnt_marketplace_plugin.py" ]; then
    echo "ERROR: Plugin file not found at ${COMPONENT_DIR}/lmnt_marketplace_plugin.py"
    exit 1
fi

# Check if the load_component function exists in the file
if ! grep -q "def load_component" "${COMPONENT_DIR}/lmnt_marketplace_plugin.py"; then
    echo "Adding load_component function to the plugin file..."
    
    # Add the load_component function to the end of the file
    cat << 'EOF' >> "${COMPONENT_DIR}/lmnt_marketplace_plugin.py"

def load_component(config):
    """Load the LMNT Marketplace Plugin component
    
    Args:
        config: component configuration
    
    Returns:
        LmntMarketplacePlugin: initialized plugin instance
    """
    return LmntMarketplacePlugin(config)
EOF
    
    echo "load_component function added successfully."
else
    echo "load_component function already exists in the plugin file."
fi

# Check the symlink
if [ -L "${COMPONENT_DIR}/lmnt_marketplace_plugin.py" ]; then
    echo "Plugin file is a symlink pointing to: $(readlink -f ${COMPONENT_DIR}/lmnt_marketplace_plugin.py)"
    
    # Check if the target file exists
    TARGET=$(readlink -f "${COMPONENT_DIR}/lmnt_marketplace_plugin.py")
    if [ ! -f "$TARGET" ]; then
        echo "ERROR: Symlink target does not exist: $TARGET"
        exit 1
    fi
    
    # Check if the load_component function exists in the target file
    if ! grep -q "def load_component" "$TARGET"; then
        echo "Adding load_component function to the target file..."
        
        # Add the load_component function to the end of the file
        cat << 'EOF' >> "$TARGET"

def load_component(config):
    """Load the LMNT Marketplace Plugin component
    
    Args:
        config: component configuration
    
    Returns:
        LmntMarketplacePlugin: initialized plugin instance
    """
    return LmntMarketplacePlugin(config)
EOF
        
        echo "load_component function added to target file successfully."
    else
        echo "load_component function already exists in the target file."
    fi
else
    echo "Plugin file is not a symlink."
fi

# Check Python cache files and remove them
echo "Removing Python cache files..."
find "${COMPONENT_DIR}" -name "*.pyc" -delete
find "${COMPONENT_DIR}" -name "__pycache__" -exec rm -rf {} +

echo "Restarting Moonraker..."
sudo systemctl restart moonraker

echo "Waiting for Moonraker to start..."
sleep 5

echo "Checking Moonraker logs for errors..."
tail -n 20 ~/printer_data/logs/moonraker.log

echo "Fix completed. If you still see errors, please check the logs for more details."
