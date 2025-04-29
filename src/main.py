#!/usr/bin/env python3

import sys
import logging
import uvicorn

from ticktick_mcp import config

# --- Core Imports --- #
# Import the MCP instance
from ticktick_mcp.mcp_instance import mcp

# TickTick Client Initialization (using the new singleton)
from ticktick_mcp.client import TickTickClientSingleton

# --- Tool Registration --- #
# Import tool modules AFTER mcp instance is created.
# The @mcp.tool() decorators in these modules will register functions
# with the imported 'mcp' instance.
logging.info("Registering MCP tools...")
from ticktick_mcp.tools import task_tools
from ticktick_mcp.tools import filter_tools
from ticktick_mcp.tools import conversion_tools
logging.info("Tool registration complete.")

# --- Main Execution Logic --- #
def main():
    mcp.run(transport="stdio")

# --- Script Entry Point --- #
if __name__ == "__main__":
    main()