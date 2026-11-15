"""Tulle-designed panels for the MCP connector ("MCP Apps", 2026-11-15): UI the connector supplies and Claude renders
beside the chat. Flag-on only - mcp_server.build() is the one caller.

Written against (read 2026-11-15): claude.com/docs/connectors/building/mcp-apps/{getting-started,quickstart,
design-guidelines,display-modes,transparent-theming,external-links,instance-supersession,troubleshooting}.md and the
ext-apps spec types (protocol 2026-01-26).

HOW IT IS WIRED
    A panel is a `ui://` resource whose content is ONE self-contained HTML document, served as
    `text/html;profile=mcp-app`. A tool points at its panel with `_meta.ui.resourceUri` (tool_meta below; the legacy
    flat key `ui/resourceUri` is written too, as the TypeScript helper does). The host reads that on tools/list,
    fetches the document with resources/read - through the same /mcp gate and access token as every other request -
    and renders it in a sandboxed frame. The tool's result (structuredContent) is pushed into the frame by the host;
    the panel draws it. The SDK's `mcp.server.apps.Apps` extension registers the resources and advertises the
    `io.modelcontextprotocol/ui` capability.

        ui://tulle/my-wedding   <- get_my_wedding, budget_status
        ui://tulle/vendors      <- list_saved_vendors, find_vendor

RULES (keep them)
    - A panel renders ONLY what the tool result already holds under the data policy (mcp_server.py). It calls no
      endpoint and no tool, and NO field is added to a tool result for a panel's sake.
    - No network request of any kind from a panel: no CDN, no remote font, no image, no analytics. So the MCP Apps
      browser SDK (normally loaded from a CDN) is not used; the few postMessage calls are written out in each file.
      No `_meta.ui.csp` domains are declared: the host's default policy (everything external blocked) is what we want.
    - Every string from a tool result is untrusted in the panel: textContent only, never markup; a link is wired
      only for https://www.tulletogether.app/.
    - Not used: embedded websites and global entry points (both need a Verified directory listing), app tools,
      messages to Claude, model-context updates - the panels are read-only views.
    - rec_service/mcp_ui/ holds the HTML and has NO __init__.py on purpose (this module must stay `import mcp_ui`).
      preview.html there is for people - it is never served.
"""
import pathlib

MY_WEDDING = "ui://tulle/my-wedding"
VENDORS = "ui://tulle/vendors"
MIME = "text/html;profile=mcp-app"
DIR = pathlib.Path(__file__).resolve().parent / "mcp_ui"
# uri -> (file, name, title, description)
PANELS = {
    MY_WEDDING: ("my-wedding.html", "tulle-my-wedding", "My wedding",
                 "Panel for the couple's wedding plan: budget ring, booked items and saved vendors."),
    VENDORS: ("vendors.html", "tulle-vendors", "Vendors",
              "Panel for saved vendors and a vendor looked up by name: name, type, state and a Tulle page link."),
}
# tool -> the panel that draws its result
TOOL_UI = {"get_my_wedding": MY_WEDDING, "budget_status": MY_WEDDING,
           "list_saved_vendors": VENDORS, "find_vendor": VENDORS}


def html(uri):
    """The HTML document of a panel (read when the MCP server is built, once per process)."""
    return (DIR / PANELS[uri][0]).read_text(encoding="utf-8")


def tool_meta(tool):
    """The `_meta` of a tool that has a panel ({} for one that has none)."""
    uri = TOOL_UI.get(tool)
    return {"ui": {"resourceUri": uri}, "ui/resourceUri": uri} if uri else {}


def extension():
    """The SDK's MCP Apps extension carrying the two panels, for MCPServer(extensions=[...]).
    prefersBorder true: the host draws the card on web and mobile alike; the panel itself stays transparent."""
    from mcp.server.apps import Apps
    apps = Apps()
    for uri, (_, name, title, description) in PANELS.items():
        apps.add_html_resource(uri, html(uri), name=name, title=title, description=description, prefers_border=True)
    return apps
