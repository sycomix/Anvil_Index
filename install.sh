#!/bin/bash

# Configuration
# UPDATED: Pointing to Anvil_Index repo
ANVIL_SOURCE_URL="https://raw.githubusercontent.com/sycomix/Anvil_Index/main/anvil.py"

ANVIL_ROOT="$HOME/.anvil"
CORE_DIR="$ANVIL_ROOT/core"
BIN_DIR="$ANVIL_ROOT/bin"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== Installing Anvil Package Manager ===${NC}"

# 1. Check for Python 3
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python 3 is required but not installed.${NC}"
    echo "Please install python3 first (e.g., 'apt install python3' or 'pkg install python')."
    exit 1
fi

# 2. Create Directories
echo -e "${BLUE}Creating directory structure...${NC}"
mkdir -p "$CORE_DIR"
mkdir -p "$BIN_DIR"

# 3. Download Anvil
echo -e "${BLUE}Downloading Anvil core...${NC}"
if command -v curl &> /dev/null; then
    curl -fsSL "$ANVIL_SOURCE_URL" -o "$CORE_DIR/anvil.py"
elif command -v wget &> /dev/null; then
    wget -q "$ANVIL_SOURCE_URL" -O "$CORE_DIR/anvil.py"
else
    echo -e "${RED}Error: Neither curl nor wget found.${NC}"
    exit 1
fi

if [ ! -f "$CORE_DIR/anvil.py" ]; then
    echo -e "${RED}Download failed.${NC}"
    exit 1
fi

# 4. Create Executable Shim
echo -e "${BLUE}Creating executable shim...${NC}"
cat > "$BIN_DIR/anvil" <<EOF
#!/bin/sh
exec python3 "$CORE_DIR/anvil.py" "\$@"
EOF

chmod +x "$BIN_DIR/anvil"

# 5. Initialize (Create default taps/index)
echo -e "${BLUE}Initializing Anvil...${NC}"
# Attempt an index repair first to auto-clean corrupted files (safe/no-op if not needed)
python3 "$CORE_DIR/anvil.py" index repair || true
"$BIN_DIR/anvil" update

# 6. Path Configuration
echo -e "${GREEN}=== Installation Complete! ===${NC}"
echo ""

# Check if already in path
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) 
    echo "Please add the following line to your shell profile (.bashrc, .zshrc, etc.):"
    echo ""
    echo "    export PATH=\"\$HOME/.anvil/bin:\$PATH\""
    echo ""
    ;;
esac

echo "Run 'anvil --help' to get started."