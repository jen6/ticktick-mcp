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

    Args:
        obj_id (str): The unique ID string of the object to retrieve. Required.
                     Can be a task ID, project ID, tag ID, or any other valid TickTick object ID.

    Returns:
        A JSON string with one of the following structures:
        - Success: The complete object with all its properties
        - Not Found: null or empty response if the object doesn't exist
        - Error: {"error": "Error message describing what went wrong", "result": null, "status": "error"}

    Limitations:
        - Can only retrieve objects the user has access to
        - Different object types (tasks, projects, tags) have different property structures
        - Does not provide information about related objects (e.g., a project's tasks)
        - The ID must be valid and refer to an existing object
        - Some fields may not be populated depending on the object type and user permissions

    Examples:
        Get a task by ID:
        {
            "obj_id": "task_abcdef12345"
        }

        Get a project by ID:
        {
            "obj_id": "project_ghijk67890"
        }

        Get a tag by ID:
        {
            "obj_id": "tag_lmnop54321"
        }

    Agent Usage Guide:
        - Use this tool when you need specific details about a known object with its ID
        - Helpful for getting full details before updating an object
        - For tasks, this retrieves a single task including any subtasks
        - For projects, this retrieves the project details (name, color) but not its tasks
        - IDs are typically retrieved from other API calls like ticktick_get_all or ticktick_filter_tasks
        - Example mapping:
          "Get details of my quarterly report task" →
          First find the task ID using ticktick_filter_tasks
          Then: {"obj_id": "[found task ID]"}
        - If the object is not found (null response), explain to the user it might not exist or they might not have access
        - Very useful in workflows that require multiple steps, like update or delete operations
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

    Args:
        search (str): The type of objects to retrieve. Required.
               Must be one of: 'tasks', 'projects', 'tags', 'project_folders'.
               Case insensitive (e.g., 'Projects' is the same as 'projects').

    Returns:
        A JSON string with one of the following structures:
        - Success: A list of objects of the requested type (may be empty)
        - Error: {"error": "Error message describing what went wrong", "status": "error"}

    Limitations:
        - For 'tasks', only uncompleted tasks are returned by default
        - For large accounts, response size might be very large 
        - Different object types have different property structures
        - Some object types might not be available depending on the user's subscription level
        - The API might limit the number of results for performance reasons
        - For a large number of tasks, consider using ticktick_filter_tasks instead
        - Does not retrieve nested structures (e.g., tasks within projects)

    Examples:
        Get all projects:
        {
            "search": "projects"
        }

        Get all tags:
        {
            "search": "tags"
        }

        Get all uncompleted tasks:
        {
            "search": "tasks"
        }

        Get all project folders:
        {
            "search": "project_folders"
        }

    Agent Usage Guide:
        - Use this tool to get a comprehensive list of a specific object type
        - Particularly useful for:
          1. Discovering available projects to organize tasks
          2. Finding existing tags to categorize tasks
          3. Getting an overview of project folders for organization
        - For tasks, consider using ticktick_filter_tasks for more targeted results
        - When user asks for "all my projects" or "list of projects", use {"search": "projects"}
        - When user asks for "all my tags" or "available tags", use {"search": "tags"}
        - When user asks for "all my tasks", consider if they really need ALL tasks or just:
          - Tasks from a specific project
          - Tasks with certain due dates
          - Tasks with specific priorities
          In these cases, ticktick_filter_tasks would be more appropriate
        - When retrieving all tasks, warn the user if there are many results
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
 