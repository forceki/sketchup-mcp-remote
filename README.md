# SketchupMCP - Sketchup Model Context Protocol Integration

SketchupMCP connects Sketchup to Claude AI through the Model Context Protocol (MCP), allowing Claude to directly interact with and control Sketchup. This integration enables prompt-assisted 3D modeling, scene creation, and manipulation in Sketchup.

Big Shoutout to [Blender MCP](https://github.com/ahujasid/blender-mcp) for the inspiration and structure.

## Features

* **Two-way communication**: Connect Claude AI to Sketchup through a TCP socket connection
* **Component manipulation**: Create, modify, delete, and transform components in Sketchup
* **Material control**: Apply and modify materials and colors
* **Scene inspection**: Get detailed information about the current Sketchup scene
* **Selection handling**: Get and manipulate selected components
* **Ruby code evaluation**: Execute arbitrary Ruby code directly in SketchUp for advanced operations

## Components

The system consists of two main components:

1. **Sketchup Extension**: A Sketchup extension that creates a TCP server within Sketchup to receive and execute commands
2. **MCP Server (`sketchup_mcp/server.py`)**: A Python server that implements the Model Context Protocol and connects to the Sketchup extension

## Installation (step by step)

You need a PC with SketchUp. Everything below is run on that PC.

### 1. Install the tools

| Tool | Windows | macOS |
|---|---|---|
| git | `winget install --id Git.Git -e` | `brew install git` |
| uv (Python runner) | `winget install --id=astral-sh.uv -e` | `brew install uv` |

Check with `git --version` and `uv --version`.

### 2. Get this repository

```bash
git clone https://github.com/forceki/sketchup-mcp-remote.git
cd sketchup-mcp-remote
uv sync
```

### 3. Install the SketchUp extension

The extension runs a small TCP server inside SketchUp (port 9876).

1. Use the prebuilt `su_mcp.rbz` in the root of this repository. (To rebuild it from source, run
   `powershell -ExecutionPolicy Bypass -File .\build-rbz.ps1`.)
2. In SketchUp, go to Window > Extension Manager.
3. Click "Install Extension" and select `su_mcp.rbz`.
4. Restart SketchUp.
5. Go to Extensions > MCP Server > Start Server. Do this each time you open SketchUp.

### 4. Pick how Claude connects

**Option A: Claude on the same PC (stdio, simplest)**

With Claude Code, run this once (it needs no clone):

```bash
claude mcp add sketchup -- uvx --from git+https://github.com/forceki/sketchup-mcp-remote sketchup-mcp
```

Or add this to your Claude Desktop configuration:

```json
"mcpServers": {
    "sketchup": {
        "command": "uvx",
        "args": ["--from", "git+https://github.com/forceki/sketchup-mcp-remote", "sketchup-mcp"]
    }
}
```

Do not use plain `uvx sketchup-mcp`. That installs the original version from PyPI, which has
no HTTP mode.

**Option B: Claude on another machine such as a VPS (HTTP over Tailscale)**

See [Remote use over HTTP](#remote-use-over-http-vps--tailscale) below.

### 5. Check that it works

- `netstat -ano | findstr :9876` shows SketchUp listening (Windows).
- Ask Claude: "Create a simple house with a roof and windows in a named group."
- If a command fails, check the Ruby Console in SketchUp (Window > Ruby Console).

## Usage

### Starting the Connection

1. In Sketchup, go to Extensions > MCP Server > Start Server
2. The server will start on the default port (9876)
3. Make sure the MCP server is running in your terminal

### Using with Claude

Configure Claude as described in [Installation, step 4](#4-pick-how-claude-connects).

Once connected, Claude can interact with Sketchup using the following capabilities:

#### Tools

* `get_scene_info` - Gets information about the current Sketchup scene
* `get_selected_components` - Gets information about currently selected components
* `create_component` - Create a new component with specified parameters
* `delete_component` - Remove a component from the scene
* `transform_component` - Move, rotate, or scale a component
* `set_material` - Apply materials to components
* `export_scene` - Export the current scene to various formats
* `eval_ruby` - Execute arbitrary Ruby code in SketchUp for advanced operations

### Example Commands

Here are some examples of what you can ask Claude to do:

* "Create a simple house model with a roof and windows"
* "Select all components and get their information"
* "Make the selected component red"
* "Move the selected component 10 units up"
* "Export the current scene as a 3D model"
* "Create a complex arts and crafts cabinet using Ruby code"

## Troubleshooting

* **Connection issues**: Make sure both the Sketchup extension server and the MCP server are running
* **Command failures**: Check the Ruby Console in Sketchup for error messages
* **Timeout errors**: Try simplifying your requests or breaking them into smaller steps

## Technical Details

### Communication Protocol

The system uses a simple JSON-based protocol over TCP sockets:

* **Commands** are sent as JSON objects with a `type` and optional `params`
* **Responses** are JSON objects with a `status` and `result` or `message`

## Remote use over HTTP (VPS + Tailscale)

This fork can also serve MCP over HTTP so Claude Code on another machine can control the
SketchUp on your PC. Start it with:

```powershell
powershell -ExecutionPolicy Bypass -File .\start-mcp-http.ps1 -Port 8001
```

Then on the remote machine:

```bash
claude mcp add --scope project --transport http sketchup http://<PC_TAILSCALE_IP>:8001/mcp
```

The server has **no authentication**. Bind it to a private network (Tailscale) only and never
expose it publicly. See [CHANGES.md](CHANGES.md) for what differs from upstream.

## Connector for the SketchUp Claude website

The `connector/` folder contains the connector and the one-line installers used by the
SketchUp Claude website (**+ Add computer**). See [connector/README.md](connector/README.md).

## Credits

Based on [mhyrr/sketchup-mcp](https://github.com/mhyrr/sketchup-mcp), MIT licensed.
## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

MIT 
