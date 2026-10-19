from mcp.server.fastmcp import FastMCP, Context
import socket
import json
import asyncio
import logging
from dataclasses import dataclass
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any, List

# Configure logging
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("SketchupMCPServer")

import os as _os
# Seconds to wait for SketchUp to answer one command (KUAS builds can take a while).
SKETCHUP_TIMEOUT = float(_os.environ.get("SKETCHUP_MCP_TIMEOUT", "120"))

# Define version directly to avoid pkg_resources dependency
__version__ = "0.1.17"
logger.info(f"SketchupMCP Server version {__version__} starting up")

@dataclass
class SketchupConnection:
    host: str
    port: int
    sock: socket.socket = None
    
    def connect(self) -> bool:
        """Connect to the Sketchup extension socket server"""
        if self.sock:
            try:
                # Test if connection is still alive
                self.sock.settimeout(0.1)
                self.sock.send(b'')
                return True
            except (socket.error, BrokenPipeError, ConnectionResetError):
                # Connection is dead, close it and reconnect
                logger.info("Connection test failed, reconnecting...")
                self.disconnect()
            
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            logger.info(f"Connected to Sketchup at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Sketchup: {str(e)}")
            self.sock = None
            return False
    
    def disconnect(self):
        """Disconnect from the Sketchup extension"""
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Error disconnecting from Sketchup: {str(e)}")
            finally:
                self.sock = None

    def receive_full_response(self, sock, buffer_size=8192):
        """Receive the complete response, potentially in multiple chunks"""
        chunks = []
        sock.settimeout(SKETCHUP_TIMEOUT)
        
        try:
            while True:
                try:
                    chunk = sock.recv(buffer_size)
                    if not chunk:
                        if not chunks:
                            raise Exception("Connection closed before receiving any data")
                        break
                    
                    chunks.append(chunk)
                    
                    try:
                        data = b''.join(chunks)
                        json.loads(data.decode('utf-8'))
                        logger.info(f"Received complete response ({len(data)} bytes)")
                        return data
                    except json.JSONDecodeError:
                        continue
                except socket.timeout:
                    logger.warning("Socket timeout during chunked receive")
                    break
                except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                    logger.error(f"Socket connection error during receive: {str(e)}")
                    raise
        except socket.timeout:
            logger.warning("Socket timeout during chunked receive")
        except Exception as e:
            logger.error(f"Error during receive: {str(e)}")
            raise
            
        if chunks:
            data = b''.join(chunks)
            logger.info(f"Returning data after receive completion ({len(data)} bytes)")
            try:
                json.loads(data.decode('utf-8'))
                return data
            except json.JSONDecodeError:
                raise Exception("Incomplete JSON response received")
        else:
            raise Exception("No data received")

    def send_command(self, method: str, params: Dict[str, Any] = None, request_id: Any = None) -> Dict[str, Any]:
        """Send a JSON-RPC request to Sketchup and return the response"""

        # Ensure we're sending a proper JSON-RPC request
        if method == "tools/call" and params and "name" in params and "arguments" in params:
            # This is already in the correct format
            request = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
                "id": request_id
            }
        else:
            # This is a direct command - convert to JSON-RPC
            command_name = method
            command_params = params or {}
            
            # Log the conversion
            logger.info(f"Converting direct command '{command_name}' to JSON-RPC format")
            
            request = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": command_name,
                    "arguments": command_params
                },
                "id": request_id
            }
        
        # Maximum number of retries
        max_retries = 2
        retry_count = 0
        
        while retry_count <= max_retries:
            try:
                logger.info(f"Sending JSON-RPC request: {request}")
                
                # Log the exact bytes being sent
                request_bytes = json.dumps(request).encode('utf-8') + b'\n'
                logger.info(f"Raw bytes being sent: {request_bytes}")
                
                # The SketchUp extension answers exactly one request per connection
                # and then closes it, so open a fresh connection for every request.
                self.disconnect()
                if not self.connect():
                    raise ConnectionError("Not connected to Sketchup")
                self.sock.sendall(request_bytes)
                logger.info(f"Request sent, waiting for response...")

                self.sock.settimeout(SKETCHUP_TIMEOUT)

                response_data = self.receive_full_response(self.sock)
                logger.info(f"Received {len(response_data)} bytes of data")
                self.disconnect()

                response = json.loads(response_data.decode('utf-8'))
                logger.info(f"Response parsed: {response}")

                if not isinstance(response, dict):
                    return response

                if "error" in response:
                    logger.error(f"Sketchup error: {response['error']}")
                    raise Exception(response["error"].get("message", "Unknown error from Sketchup"))

                return response.get("result", {})
                
            except (socket.timeout, ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                logger.warning(f"Connection error (attempt {retry_count+1}/{max_retries+1}): {str(e)}")
                retry_count += 1
                
                if retry_count <= max_retries:
                    logger.info(f"Retrying connection...")
                    self.disconnect()
                    if not self.connect():
                        logger.error("Failed to reconnect")
                        break
                else:
                    logger.error(f"Max retries reached, giving up")
                    self.sock = None
                    raise Exception(f"Connection to Sketchup lost after {max_retries+1} attempts: {str(e)}")
            
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON response from Sketchup: {str(e)}")
                if 'response_data' in locals() and response_data:
                    logger.error(f"Raw response (first 200 bytes): {response_data[:200]}")
                raise Exception(f"Invalid response from Sketchup: {str(e)}")
            
            except Exception as e:
                logger.error(f"Error communicating with Sketchup: {str(e)}")
                self.sock = None
                raise Exception(f"Communication error with Sketchup: {str(e)}")

# Global connection management
_sketchup_connection = None

def get_sketchup_connection():
    """Get or create a persistent Sketchup connection"""
    global _sketchup_connection
    
    # No ping here: the extension does not know "ping", and its unread
    # "Method not found" reply used to be mistaken for the next command's answer.
    # send_command opens a fresh connection per request anyway.
    if _sketchup_connection is not None:
        return _sketchup_connection

    if _sketchup_connection is None:
        _sketchup_connection = SketchupConnection(host="localhost", port=9876)
        if not _sketchup_connection.connect():
            logger.error("Failed to connect to Sketchup")
            _sketchup_connection = None
            raise Exception("Could not connect to Sketchup. Make sure the Sketchup extension is running.")
        # Close the test connection right away: an idle open connection blocks the
        # extension, which waits for a request line on SketchUp's main loop.
        _sketchup_connection.disconnect()
        logger.info("Sketchup is reachable")
    
    return _sketchup_connection

@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    """Manage server startup and shutdown lifecycle"""
    try:
        logger.info("SketchupMCP server starting up")
        try:
            sketchup = get_sketchup_connection()
            logger.info("Successfully connected to Sketchup on startup")
        except Exception as e:
            logger.warning(f"Could not connect to Sketchup on startup: {str(e)}")
            logger.warning("Make sure the Sketchup extension is running")
        yield {}
    finally:
        global _sketchup_connection
        if _sketchup_connection:
            logger.info("Disconnecting from Sketchup")
            _sketchup_connection.disconnect()
            _sketchup_connection = None
        logger.info("SketchupMCP server shut down")

# Create MCP server with lifespan support
mcp = FastMCP(
    "SketchupMCP",
    instructions="Sketchup integration through the Model Context Protocol",
    lifespan=server_lifespan
)

# Tool endpoints
@mcp.tool()
def create_component(
    ctx: Context,
    type: str = "cube",
    position: List[float] = None,
    dimensions: List[float] = None
) -> str:
    """Create a new component in Sketchup"""
    try:
        logger.info(f"create_component called with type={type}, position={position}, dimensions={dimensions}, request_id={ctx.request_id}")
        
        sketchup = get_sketchup_connection()
        
        params = {
            "name": "create_component",
            "arguments": {
                "type": type,
                "position": position or [0,0,0],
                "dimensions": dimensions or [1,1,1]
            }
        }
        
        logger.info(f"Calling send_command with method='tools/call', params={params}, request_id={ctx.request_id}")
        
        result = sketchup.send_command(
            method="tools/call",
            params=params,
            request_id=ctx.request_id
        )
        
        logger.info(f"create_component result: {result}")
        return json.dumps(result)
    except Exception as e:
        logger.error(f"Error in create_component: {str(e)}")
        return f"Error creating component: {str(e)}"

@mcp.tool()
def delete_component(
    ctx: Context,
    id: str
) -> str:
    """Delete a component by ID"""
    try:
        sketchup = get_sketchup_connection()
        result = sketchup.send_command(
            method="tools/call",
            params={
                "name": "delete_component",
                "arguments": {"id": id}
            },
            request_id=ctx.request_id
        )
        return json.dumps(result)
    except Exception as e:
        return f"Error deleting component: {str(e)}"

@mcp.tool()
def transform_component(
    ctx: Context,
    id: str,
    position: List[float] = None,
    rotation: List[float] = None,
    scale: List[float] = None
) -> str:
    """Transform a component's position, rotation, or scale"""
    try:
        sketchup = get_sketchup_connection()
        arguments = {"id": id}
        if position is not None:
            arguments["position"] = position
        if rotation is not None:
            arguments["rotation"] = rotation
        if scale is not None:
            arguments["scale"] = scale
            
        result = sketchup.send_command(
            method="tools/call",
            params={
                "name": "transform_component",
                "arguments": arguments
            },
            request_id=ctx.request_id
        )
        return json.dumps(result)
    except Exception as e:
        return f"Error transforming component: {str(e)}"

@mcp.tool()
def get_selection(ctx: Context) -> str:
    """Get currently selected components"""
    try:
        sketchup = get_sketchup_connection()
        result = sketchup.send_command(
            method="tools/call",
            params={
                "name": "get_selection",
                "arguments": {}
            },
            request_id=ctx.request_id
        )
        return json.dumps(result)
    except Exception as e:
        return f"Error getting selection: {str(e)}"

@mcp.tool()
def set_material(
    ctx: Context,
    id: str,
    material: str
) -> str:
    """Set material for a component"""
    try:
        sketchup = get_sketchup_connection()
        result = sketchup.send_command(
            method="tools/call",
            params={
                "name": "set_material",
                "arguments": {
                    "id": id,
                    "material": material
                }
            },
            request_id=ctx.request_id
        )
        return json.dumps(result)
    except Exception as e:
        return f"Error setting material: {str(e)}"

@mcp.tool()
def export_scene(
    ctx: Context,
    format: str = "skp"
) -> str:
    """Export the current scene"""
    try:
        sketchup = get_sketchup_connection()
        result = sketchup.send_command(
            method="tools/call",
            params={
                "name": "export",
                "arguments": {
                    "format": format
                }
            },
            request_id=ctx.request_id
        )
        return json.dumps(result)
    except Exception as e:
        return f"Error exporting scene: {str(e)}"

@mcp.tool()
def create_mortise_tenon(
    ctx: Context,
    mortise_id: str,
    tenon_id: str,
    width: float = 1.0,
    height: float = 1.0,
    depth: float = 1.0,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    offset_z: float = 0.0
) -> str:
    """Create a mortise and tenon joint between two components"""
    try:
        logger.info(f"create_mortise_tenon called with mortise_id={mortise_id}, tenon_id={tenon_id}, width={width}, height={height}, depth={depth}, offsets=({offset_x}, {offset_y}, {offset_z})")
        
        sketchup = get_sketchup_connection()
        
        result = sketchup.send_command(
            method="tools/call",
            params={
                "name": "create_mortise_tenon",
                "arguments": {
                    "mortise_id": mortise_id,
                    "tenon_id": tenon_id,
                    "width": width,
                    "height": height,
                    "depth": depth,
                    "offset_x": offset_x,
                    "offset_y": offset_y,
                    "offset_z": offset_z
                }
            },
            request_id=ctx.request_id
        )
        
        logger.info(f"create_mortise_tenon result: {result}")
        return json.dumps(result)
    except Exception as e:
        logger.error(f"Error in create_mortise_tenon: {str(e)}")
        return f"Error creating mortise and tenon joint: {str(e)}"

@mcp.tool()
def create_dovetail(
    ctx: Context,
    tail_id: str,
    pin_id: str,
    width: float = 1.0,
    height: float = 1.0,
    depth: float = 1.0,
    angle: float = 15.0,
    num_tails: int = 3,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    offset_z: float = 0.0
) -> str:
    """Create a dovetail joint between two components"""
    try:
        logger.info(f"create_dovetail called with tail_id={tail_id}, pin_id={pin_id}, width={width}, height={height}, depth={depth}, angle={angle}, num_tails={num_tails}")
        
        sketchup = get_sketchup_connection()
        
        result = sketchup.send_command(
            method="tools/call",
            params={
                "name": "create_dovetail",
                "arguments": {
                    "tail_id": tail_id,
                    "pin_id": pin_id,
                    "width": width,
                    "height": height,
                    "depth": depth,
                    "angle": angle,
                    "num_tails": num_tails,
                    "offset_x": offset_x,
                    "offset_y": offset_y,
                    "offset_z": offset_z
                }
            },
            request_id=ctx.request_id
        )
        
        logger.info(f"create_dovetail result: {result}")
        return json.dumps(result)
    except Exception as e:
        logger.error(f"Error in create_dovetail: {str(e)}")
        return f"Error creating dovetail joint: {str(e)}"

@mcp.tool()
def create_finger_joint(
    ctx: Context,
    board1_id: str,
    board2_id: str,
    width: float = 1.0,
    height: float = 1.0,
    depth: float = 1.0,
    num_fingers: int = 5,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    offset_z: float = 0.0
) -> str:
    """Create a finger joint (box joint) between two components"""
    try:
        logger.info(f"create_finger_joint called with board1_id={board1_id}, board2_id={board2_id}, width={width}, height={height}, depth={depth}, num_fingers={num_fingers}")
        
        sketchup = get_sketchup_connection()
        
        result = sketchup.send_command(
            method="tools/call",
            params={
                "name": "create_finger_joint",
                "arguments": {
                    "board1_id": board1_id,
                    "board2_id": board2_id,
                    "width": width,
                    "height": height,
                    "depth": depth,
                    "num_fingers": num_fingers,
                    "offset_x": offset_x,
                    "offset_y": offset_y,
                    "offset_z": offset_z
                }
            },
            request_id=ctx.request_id
        )
        
        logger.info(f"create_finger_joint result: {result}")
        return json.dumps(result)
    except Exception as e:
        logger.error(f"Error in create_finger_joint: {str(e)}")
        return f"Error creating finger joint: {str(e)}"

@mcp.tool()
def eval_ruby(
    ctx: Context,
    code: str
) -> str:
    """Evaluate arbitrary Ruby code in Sketchup"""
    try:
        logger.info(f"eval_ruby called with code length: {len(code)}")
        
        sketchup = get_sketchup_connection()
        
        result = sketchup.send_command(
            method="tools/call",
            params={
                "name": "eval_ruby",
                "arguments": {
                    "code": code
                }
            },
            request_id=ctx.request_id
        )
        
        logger.info(f"eval_ruby result: {result}")
        
        # Format the response to include the result
        response = {
            "success": True,
            "result": result.get("content", [{"text": "Success"}])[0].get("text", "Success") if isinstance(result.get("content"), list) and len(result.get("content", [])) > 0 else "Success"
        }
        
        return json.dumps(response)
    except Exception as e:
        logger.error(f"Error in eval_ruby: {str(e)}")
        return json.dumps({
            "success": False,
            "error": str(e)
        })

# ---------------------------------------------------------------------------
# Tools for remote agents (KUAS workflow): model inspection, images and a
# small project folder for specs. The agent may run on another machine, so
# images are returned as MCP image content instead of local file paths.
# ---------------------------------------------------------------------------
import os
import re
import tempfile
from pathlib import Path
from mcp.server.fastmcp import Image

WORK_DIR = Path(os.environ.get("SKETCHUP_MCP_WORKDIR", Path.home() / "sketchup-connector" / "work"))
KUAS_DIR = Path(os.environ.get("SKETCHUP_KUAS_DIR", Path.home() / "sketchup-connector" / "kuas"))
PROJECT_FILE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}\.(json|md|txt)$")
IMAGE_NAME_RE = re.compile(r"^(kuas|mcp)_[A-Za-z0-9_.-]+\.png$")
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _ruby(ctx: Context, code: str) -> str:
    """Run Ruby in SketchUp and return its result as text; raise on failure."""
    result = get_sketchup_connection().send_command(
        method="tools/call",
        params={"name": "eval_ruby", "arguments": {"code": code}},
        request_id=ctx.request_id,
    )
    if isinstance(result, dict) and result.get("isError"):
        raise RuntimeError(str(result.get("content")))
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list) and content:
        return content[0].get("text", "")
    return ""


def _allowed_image(path: str) -> Path:
    p = Path(os.path.realpath(path))
    roots = [Path(os.path.realpath(tempfile.gettempdir())), Path(os.path.realpath(WORK_DIR))]
    if not IMAGE_NAME_RE.match(p.name) or not any(r == p.parent or r in p.parents for r in roots):
        raise ValueError("only KUAS/MCP PNG images in the temp or work folder can be read")
    if not p.is_file():
        raise FileNotFoundError(f"image not found: {p.name}")
    if p.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("image too large (max 8 MB); use a smaller crop or scale")
    return p


def _project_file(name: str) -> Path:
    if not PROJECT_FILE_RE.match(name or ""):
        raise ValueError("name must be a plain file name ending in .json, .md or .txt (letters, digits, _ . -)")
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    return WORK_DIR / name


MODEL_INFO_RUBY = r"""
require 'json'
m = Sketchup.active_model
mm = lambda { |v| (v.to_f * 25.4).round(1) }
bb = lambda { |b| [mm.(b.min.x), mm.(b.min.y), mm.(b.min.z), mm.(b.max.x), mm.(b.max.y), mm.(b.max.z)] }
short = lambda { |v| s = v.to_s; s.size > 200 ? s[0, 200] + '...' : v }
kuas = lambda do |e|
  d = e.attribute_dictionary('kuas')
  d ? d.to_h.transform_values { |v| short.(v) } : nil
end
ents = m.entities
units = (m.options['UnitsOptions']['LengthUnit'] rescue nil)
{
  title: m.title, path: m.path, modified: m.modified?,
  length_unit: ({0 => 'inches', 1 => 'feet', 2 => 'mm', 3 => 'cm', 4 => 'm'}[units] || units),
  root_entities: ents.size,
  groups: ents.select { |e| e.is_a?(Sketchup::Group) || e.is_a?(Sketchup::ComponentInstance) }.first(300).map { |e|
    { pid: e.persistent_id, type: e.class.name.split('::').last, name: e.name,
      definition: e.definition.name, tag: e.layer.name, hidden: e.hidden?, bbox_mm: bb.(e.bounds), kuas: kuas.(e) }
  },
  images: ents.grep(Sketchup::Image).map { |i|
    { pid: i.persistent_id, path: i.path, tag: i.layer.name, hidden: i.hidden?,
      pixels: [i.pixelwidth, i.pixelheight], bbox_mm: bb.(i.bounds) }
  },
  tags: m.layers.map { |l| { name: l.name, visible: l.visible? } },
  scenes: m.pages.map(&:name),
  selection_count: m.selection.size,
  kuas_loaded: (defined?(Kuas) && Kuas.respond_to?(:version)) ? Kuas.version : nil
}.to_json
"""


@mcp.tool()
def get_model_info(ctx: Context) -> str:
    """Describe the active SketchUp model: title/path, units, top-level groups/components
    (persistent id, name, tag, bbox in mm, KUAS attributes), imported reference images,
    tags, scenes and the loaded KUAS version. Read-only."""
    try:
        info = json.loads(_ruby(ctx, MODEL_INFO_RUBY))
        info["kuas_dir"] = KUAS_DIR.as_posix()
        info["work_dir"] = WORK_DIR.as_posix()
        return json.dumps(info)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
def load_kuas(ctx: Context) -> str:
    """Load (or reload) the KUAS Ruby tools into SketchUp from the folder the connector keeps
    up to date, and return Kuas.version. Call this before using any Kuas.* method."""
    try:
        entry = KUAS_DIR / "kuas_tools.rb"
        if not entry.is_file():
            raise FileNotFoundError(f"KUAS tools not found in {KUAS_DIR}; restart the connector to sync them")
        path = entry.as_posix().replace("\\", "\\\\").replace("'", "\\'")
        version = _ruby(ctx, f"load '{path}'; Kuas.version")
        return json.dumps({"success": True, "version": version.strip().strip('"'), "kuas_dir": KUAS_DIR.as_posix()})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
def get_viewport_screenshot(ctx: Context, width: int = 1400):
    """Capture the current SketchUp view and return it as a PNG image (width 400-2000 px).
    Does not change the camera or the model."""
    width = max(400, min(int(width), 2000))
    code = (
        "require 'tmpdir'; v = Sketchup.active_model.active_view; "
        f"w = {width}; h = (w * v.vpheight / v.vpwidth.to_f).round; "
        "path = File.join(Dir.tmpdir, \"mcp_view_#{Time.now.to_i}_#{rand(99999)}.png\"); "
        "v.write_image(filename: path, width: w, height: h, antialias: true, transparent: false); path"
    )
    try:
        p = _allowed_image(_ruby(ctx, code).strip().strip('"'))
        data = p.read_bytes()
        p.unlink(missing_ok=True)
        return Image(data=data, format="png")
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
def get_image(ctx: Context, path: str):
    """Return a PNG written by KUAS (e.g. Kuas.image_crop / view_region / overlay_check, files named
    kuas_*.png in the temp folder) as an image so a remote agent can see it."""
    try:
        return Image(data=_allowed_image(path).read_bytes(), format="png")
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
def put_project_file(ctx: Context, name: str, content: str, overwrite: bool = False) -> str:
    """Save a spec/mapping/report (.json, .md or .txt) in the project work folder on the SketchUp
    computer and return its absolute path for KUAS Ruby calls. JSON is validated. Existing files
    are kept unless overwrite=true (KUAS runs/passes should use new names)."""
    try:
        p = _project_file(name)
        if name.endswith(".json"):
            json.loads(content)
        if p.exists() and not overwrite:
            raise FileExistsError(f"{name} already exists; use a new run/pass name or overwrite=true")
        p.write_text(content, encoding="utf-8")
        return json.dumps({"success": True, "path": p.as_posix(), "bytes": p.stat().st_size})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
def get_project_file(ctx: Context, name: str) -> str:
    """Read a file from the project work folder on the SketchUp computer."""
    try:
        p = _project_file(name)
        return json.dumps({"success": True, "path": p.as_posix(), "content": p.read_text(encoding="utf-8")})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
def list_project_files(ctx: Context) -> str:
    """List files in the project work folder on the SketchUp computer (name, size, modified)."""
    try:
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        files = [
            {"name": f.name, "bytes": f.stat().st_size, "modified": int(f.stat().st_mtime)}
            for f in sorted(WORK_DIR.iterdir()) if f.is_file()
        ]
        return json.dumps({"success": True, "folder": WORK_DIR.as_posix(), "files": files})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


def main():
    # Default is stdio. Set SKETCHUP_MCP_TRANSPORT=streamable-http (with
    # SKETCHUP_MCP_HOST / SKETCHUP_MCP_PORT) to serve over HTTP at /mcp.
    import os
    transport = os.environ.get("SKETCHUP_MCP_TRANSPORT", "stdio")
    if transport != "stdio":
        mcp.settings.host = os.environ.get("SKETCHUP_MCP_HOST", "127.0.0.1")
        mcp.settings.port = int(os.environ.get("SKETCHUP_MCP_PORT", "8001"))
        from mcp.server.transport_security import TransportSecuritySettings
        extra = [h for h in os.environ.get("SKETCHUP_MCP_ALLOWED_HOSTS", "").split(",") if h]
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*"] + extra,
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
        )
    mcp.run(transport=transport)

if __name__ == "__main__":
    main()