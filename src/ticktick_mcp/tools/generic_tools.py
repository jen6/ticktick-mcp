import logging
from typing import Any
import json
from ticktick.api import TickTickClient

# Import the shared MCP instance for the decorator
from ..mcp_instance import mcp
# Import the singleton class
from ..client import TickTickClientSingleton
# Import helpers
from ..helpers import format_response, require_ticktick_client, _get_all_tasks_from_ticktick, ToolLogicError

# ================== #
# Generic Getters    #
# ================== #

@mcp.tool()
@require_ticktick_client
async def mcp_ticktick_ticktick_get_by_id(obj_id: str) -> str:
    """
    Retrieves a single TickTick object (task, project, tag, etc.) using its unique ID.
    (Agent Usage Guide in docstring)

    Args:
        obj_id: The unique ID of the TickTick object (e.g., task ID, project ID).

    Returns:
        A JSON string containing the retrieved object or an error message if not found.

    Example:
        Getting a task by its ID:
        {
            "obj_id": "task_abcdef12345"
        }

        Getting a project by its ID:
        {
            "obj_id": "project_ghijk67890"
        }
    """
    logging.info(f"Attempting to get object by ID: {obj_id}")
    if not isinstance(obj_id, str):
         return format_response({"error": "Invalid input: obj_id must be a string."})
    try:
        client = TickTickClientSingleton.get_client()
        if not client:
            raise ToolLogicError("TickTick client is not available.")
        obj = client.get_by_id(obj_id)
        if obj:
            logging.info(f"Successfully retrieved object by ID: {obj_id}")
            return format_response(obj) # Format for MCP
    except Exception as e:
        logging.error(f"Failed to get object by ID {obj_id}: {e}", exc_info=True)
        return format_response({"error": f"Failed to get object by ID {obj_id}: {e}", "result": None, "status": "error"})

@mcp.tool()
@require_ticktick_client
async def mcp_ticktick_ticktick_get_all(search: str) -> str:
    """
    Retrieves a list of all TickTick objects of a specified type.
    (Agent Usage Guide in docstring)

    Args:
        search: The type of object to retrieve (e.g., 'tasks', 'projects', 'tags').
               Currently supported: 'projects', 'tags'. 'tasks' may retrieve a large list and filtering is preferred.

    Returns:
        A JSON string containing a list of the requested objects or an error message.

    Example:
        Getting all projects:
        {
            "search": "projects"
        }

        Getting all tags:
        {
            "search": "tags"
        }
    """
    search = search.lower().strip()
    logging.info(f"Attempting to get all objects of type: {search}")
    VALID_SEARCH_TYPES = ['tasks', 'projects', 'tags', 'project_folders']
    if not isinstance(search, str) or search not in VALID_SEARCH_TYPES:
         return format_response({"error": f"Invalid input: 'search' must be one of {VALID_SEARCH_TYPES}."})

    try:
        client = TickTickClientSingleton.get_client()
        if not client:
            raise ToolLogicError("TickTick client is not available.")

        all_objs = None
        if search == 'tasks':
            # Use the internal helper to get UNCOMPLETED tasks from all projects
            all_objs = _get_all_tasks_from_ticktick()
        elif search == 'projects':
             all_objs = client.state.get('projects')
        elif search == 'tags':
             all_objs = client.state.get('tags')
        elif search == 'project_folders':
             all_objs = client.state.get('project_folders')
        else:
            logging.warning(f"Unsupported object type for get_all: {search}")
            raise ToolLogicError(f"Unsupported object type: {search}. Valid types: 'projects', 'tags', 'project_folders'.")

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
 