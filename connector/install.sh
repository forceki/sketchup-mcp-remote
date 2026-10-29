#!/bin/sh
# SketchUp connector installer for macOS (and Linux).
# Served by the website with the server address and pairing code filled in. Run:
#   curl -fsSL "https://your-site/install.sh?code=ABCD-EFGH" | sh
set -eu

SERVER="__SERVER__"
CODE="__CODE__"
REPO_ZIP="__REPO_ZIP__"
DIR="$HOME/sketchup-connector"

echo ""
echo "=== SketchUp connector installer ==="

find_uv() {
  if command -v uv >/dev/null 2>&1; then command -v uv; return; fi
  for p in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
    if [ -x "$p" ]; then echo "$p"; return; fi
  done
}

# 1. uv (runs Python and installs what the connector needs)
UV="$(find_uv || true)"
if [ -z "$UV" ]; then
  echo "[1/5] Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  UV="$(find_uv || true)"
  [ -n "$UV" ] || { echo "Could not install uv. See https://docs.astral.sh/uv/"; exit 1; }
else
  echo "[1/5] uv found"
fi

# 2. connector (stop an older copy first)
echo "[2/5] Downloading the connector to $DIR"
pkill -f "$DIR/" 2>/dev/null || true
mkdir -p "$DIR"
curl -fsSL "$SERVER/connector/connector.py" -o "$DIR/connector.py"

# 3. SketchUp MCP server + SketchUp extension (.rbz)
echo "[3/5] Downloading the SketchUp MCP server"
TMP="$(mktemp -d)"
curl -fsSL "$REPO_ZIP" -o "$TMP/mcp.zip"
unzip -q "$TMP/mcp.zip" -d "$TMP"
SRC="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
if [ -d "$DIR/sketchup-mcp-remote" ]; then
  # update in place and keep the already-built .venv
  find "$DIR/sketchup-mcp-remote" -mindepth 1 -maxdepth 1 ! -name .venv -exec rm -rf {} +
  cp -R "$SRC/." "$DIR/sketchup-mcp-remote/"
else
  mv "$SRC" "$DIR/sketchup-mcp-remote"
fi
rm -rf "$TMP"

# 4. pair with the website
if [ -n "$CODE" ]; then
  echo "[4/5] Pairing this computer"
  "$UV" run "$DIR/connector.py" pair --server "$SERVER" --code "$CODE" || {
    echo "Pairing failed. Click 'Add computer' on the website for a new code and run this again."; exit 1; }
else
  echo "[4/5] No code given; skipping pairing"
fi

# 5. start shortcut (double-click in Finder)
echo "[5/5] Creating the 'Start SketchUp Connector' shortcut"
START="$DIR/Start SketchUp Connector.command"
cat > "$START" <<EOF
#!/bin/sh
cd "$DIR"
exec "$UV" run connector.py run
EOF
chmod +x "$START"
if [ -d "$HOME/Desktop" ]; then cp "$START" "$HOME/Desktop/" && chmod +x "$HOME/Desktop/Start SketchUp Connector.command"; fi

echo ""
echo "Done."
echo "If SketchUp doesn't have the extension yet: Window > Extension Manager > Install Extension, pick:"
echo "    $DIR/sketchup-mcp-remote/su_mcp.rbz"
echo "Then in SketchUp: Extensions > MCP Server > Start Server."
echo "Next time, start the connector with 'Start SketchUp Connector' on your Desktop."
echo ""
echo "Starting the connector now (Ctrl+C to stop)..."
cd "$DIR"
exec "$UV" run connector.py run
