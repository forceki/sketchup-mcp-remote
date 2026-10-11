# Changes compared with upstream (mhyrr/sketchup-mcp)

- `server.py`: optional **streamable-HTTP transport** so a remote machine (for example a VPS
  running Claude Code) can use the server over a private network such as Tailscale.
  - Controlled by environment variables: `SKETCHUP_MCP_TRANSPORT`, `SKETCHUP_MCP_HOST`,
    `SKETCHUP_MCP_PORT`, `SKETCHUP_MCP_ALLOWED_HOSTS`.
  - Default is unchanged (`stdio`).
  - DNS-rebinding / Host-header protection stays on; only localhost and the hosts listed in
    `SKETCHUP_MCP_ALLOWED_HOSTS` are accepted.
- `pyproject.toml`: `mcp[cli]` pinned to `<2`.
- `start-mcp-http.ps1`: Windows launcher that binds to the Tailscale IP only.
