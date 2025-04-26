import logging
from typing import Any

# Import the shared MCP instance for the decorator
from ..mcp_instance import mcp
# Import the global client instance
from ..client import ticktick_client
# Import helpers
from ..helpers import format_response, require_client, _get_all_tasks_from_ticktick

# ================== #
# Generic Getters    #
# ================== #

@mcp.tool()
@require_client
async def ticktick_get_by_id(obj_id: str) -> str:
    """
    Retrieves a single TickTick object (task, project, tag, etc.) using its unique ID.
    (Agent Usage Guide in docstring)
    """
    if not isinstance(obj_id, str):
         return format_response({"error": "Invalid input: obj_id must be a string."})
    try:
        obj = ticktick_client.get_by_id(obj_id)
        return format_response(obj) # Returns None if not found, format_response handles it
    except Exception as e:
        logging.error(f"Failed to get object by ID {obj_id}: {e}", exc_info=True)
        return format_response({"error": f"Failed to get object by ID {obj_id}: {e}", "result": None, "status": "error"})

@mcp.tool()
@require_client
async def ticktick_get_all(search: str) -> str:
    """
    Retrieves a list of all TickTick objects of a specified type.
    (Agent Usage Guide in docstring)
    """
    VALID_SEARCH_TYPES = ['tasks', 'projects', 'tags', 'project_folders']
    if not isinstance(search, str) or search not in VALID_SEARCH_TYPES:
         return format_response({"error": f"Invalid input: 'search' must be one of {VALID_SEARCH_TYPES}."})

    try:
        all_objs = None
        if search == 'tasks':
            # Use the internal helper to get UNCOMPLETED tasks from all projects
            all_objs = _get_all_tasks_from_ticktick()
        # Use client.state for others, assuming it's populated after init
        elif search == 'projects':
             all_objs = ticktick_client.state.get('projects')
        elif search == 'tags':
             all_objs = ticktick_client.state.get('tags')
        elif search == 'project_folders':
             all_objs = ticktick_client.state.get('project_folders')

        # Ensure the result is always a list, even if state returns None
        if all_objs is None:
            all_objs = []
            logging.warning(f"Got None when trying to get all '{search}', returning empty list. Client state might be incomplete.")
        elif not isinstance(all_objs, list):
             logging.warning(f"Expected list for 'get_all({search})' but got {type(all_objs)}. Coercing to list.")
             # Attempt to handle unexpected single dict? Unlikely for state lists.
             all_objs = [all_objs] if isinstance(all_objs, dict) else []


        return format_response(all_objs)
    except ConnectionError as ce:
         # This might be raised by _get_all_tasks_from_ticktick if client is gone
         logging.error(f"ConnectionError in get_all({search}): {ce}", exc_info=True)
         return format_response({"error": str(ce), "status": "error"})
    except Exception as e:
        logging.error(f"Failed to get all {search}: {e}", exc_info=True)
 