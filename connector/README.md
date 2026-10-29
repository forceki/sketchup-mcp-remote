# SketchUp connector and installer

The connector links SketchUp on a user's computer to the **SketchUp Claude** website.
It starts the SketchUp MCP server from this repository on `127.0.0.1` and connects **out**
to the website over a WebSocket, so the computer needs no open port.

| File | Purpose |
|---|---|
| `connector.py` | The connector (`pair` once, then `run`). Dependencies install automatically with `uv run` |
| `install.ps1` | Windows one-line installer template |
| `install.sh` | macOS/Linux one-line installer template |

## Use

Users don't download these files by hand. On the website they click **+ Add computer** and run
the one command it shows. The website serves `install.ps1` / `install.sh` with
`__SERVER__`, `__CODE__` and `__REPO_ZIP__` filled in:

```powershell
irm "http://<website>/install.ps1?code=ABCD-EFGH" | iex          # Windows
```
```bash
curl -fsSL "http://<website>/install.sh?code=ABCD-EFGH" | sh     # macOS/Linux
```

The installer:
1. installs `uv` if it's missing,
2. downloads `connector.py` and this repository (MCP server and `su_mcp.rbz`),
3. pairs the computer with the one-time code,
4. creates a **Start SketchUp Connector** shortcut on the Desktop,
5. starts the connector.

## Manual use

```bash
uv run connector.py pair --server http://<website> --code ABCD-EFGH
uv run connector.py run
```

The connector keeps its settings in `connector.json` next to itself. Never share that file:
it contains the computer's access token.
