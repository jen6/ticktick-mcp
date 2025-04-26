#!/usr/bin/env python3

import sys
import logging

from ticktick_mcp import config

# --- Core Imports --- #
# Import the MCP instance
from ticktick_mcp.mcp_instance import mcp

# Import the client initializer
from ticktick_mcp.client import initialize_ticktick_client

# --- Tool Registration --- #
# Import tool modules AFTER mcp instance is created.
# The @mcp.tool() decorators in these modules will register functions
# with the imported 'mcp' instance.
logging.info("Registering MCP tools...")
from ticktick_mcp.tools import task_tools
from ticktick_mcp.tools import generic_tools
from ticktick_mcp.tools import filter_tools
from ticktick_mcp.tools import conversion_tools
logging.info("Tool registration complete.")

# --- Main Execution Logic --- #
def main():
    logging.info("Initializing TickTick MCP Server...")
    # Initialize the TickTick client (this might involve OAuth flow on first run)
    client_instance = initialize_ticktick_client()

    if client_instance:
        logging.info("TickTick client ready. Starting MCP server on stdio...")
        # Run the MCP server using stdio transport
        try:
            mcp.run(transport="stdio")
        except Exception as e:
            logging.critical(f"MCP server encountered a critical error: {e}", exc_info=True)
            sys.exit(1)
    else:
        logging.error("MCP Server cannot start due to TickTick client initialization failure.")
        sys.exit(1)

# --- Script Entry Point --- #
if __name__ == "__main__":
    main()