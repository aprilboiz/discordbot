#!/bin/sh

# Entrypoint script to fix permissions and create directories as needed
# This allows the app to create any folder it wants, even with mounted volumes

echo "Starting Discord Bot with permission fixes..."

# Function to create directory with proper permissions
create_dir_with_perms() {
    local dir="$1"
    if [ ! -d "$dir" ]; then
        echo "Creating directory: $dir"
        mkdir -p "$dir" 2>/dev/null || true
    fi
    
    # Try to fix ownership (will fail if not root, but that's OK)
    chown -R appuser:appgroup "$dir" 2>/dev/null || true
    
    # Ensure the directory is writable by the user
    chmod -R 755 "$dir" 2>/dev/null || true
}

# Create and fix permissions for known directories
create_dir_with_perms "/app/logs"
create_dir_with_perms "/app/temp_folder"

# For any additional directories the app might create
# Give write permissions to the entire /app directory structure
find /app -type d -exec chmod 755 {} + 2>/dev/null || true
find /app -type f -name "*.py" -exec chmod 644 {} + 2>/dev/null || true

# If running as root (e.g., in some deployment scenarios), 
# ensure ownership is correct before switching
if [ "$(id -u)" = "0" ]; then
    echo "Running as root, fixing ownership and switching to appuser..."
    chown -R appuser:appgroup /app
    # Switch to appuser and run the command
    exec su-exec appuser "$@"
else
    echo "Running as non-root user ($(whoami))"
    # Just execute the command directly
    exec "$@"
fi
