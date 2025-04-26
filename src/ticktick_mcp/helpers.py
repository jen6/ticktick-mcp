import datetime
import functools
import json
import logging
from typing import Any, Dict, List, Optional

# Import the global client instance
# We need this for the require_client decorator and _get_all_tasks
from .client import ticktick_client

# Type hints (can be shared or moved to a dedicated types file later if needed)
TaskObject = Dict[str, Any]

# --- Helper Function --- #
def format_response(result: Any) -> str:
    """Formats the result from ticktick-py into a JSON string for MCP."""
    if isinstance(result, (dict, list)):
        try:
            # Use default=str to handle potential datetime objects if any slip through
            return json.dumps(result, indent=2, default=str)
        except TypeError as e:
            # Log the serialization error
            logging.error(f"Failed to serialize response object: {e} - Object: {result}", exc_info=True)
            return json.dumps({"error": "Failed to serialize response", "details": str(e)})
    elif result is None:
         return json.dumps(None)
    else:
        # Fallback for unexpected types
        logging.warning(f"Formatting unexpected type: {type(result)} - Value: {result}")
        return json.dumps({"result": str(result)})

# --- Decorator for Client Check --- #
def require_client(func):
    """Decorator to check if ticktick_client is initialized before calling the tool."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        if not ticktick_client:
            # Maybe call initialize_ticktick_client() here?
            # For now, assume it must be initialized beforehand by the main script.
            logging.error("TickTick client accessed before initialization in tool function.")
            return format_response({"error": "TickTick client not initialized."})
        return await func(*args, **kwargs)
    return wrapper

# --- Internal Helper to Get All Tasks --- #
def _get_all_tasks_from_ticktick() -> List[TaskObject]:
    """Internal helper to fetch all *uncompleted* tasks from all projects."""
    if not ticktick_client:
        # This case should ideally not be hit if require_client is used properly
        # But added as a safeguard.
        logging.error("_get_all_tasks_from_ticktick called when client is not initialized.")
        raise ConnectionError("TickTick client not initialized.")

    all_tasks = []
    # Access projects directly from the client's state
    try:
        projects_state = ticktick_client.state.get('projects')
        if projects_state is None: projects_state = [] # Handle None case
    except Exception as e:
        logging.error(f"Error accessing client state for projects: {e}", exc_info=True)
        projects_state = [] # Default to empty list on error

    # Ensure projects_state is a list
    if not isinstance(projects_state, list):
        logging.warning(f"Expected list of projects in state, got {type(projects_state)}. Fetching inbox tasks only.")
        projects_state = [] # Reset to empty list if not a list

    # Get unique project IDs from state, add inbox ID
    project_ids = {p.get('id') for p in projects_state if isinstance(p, dict) and p.get('id')}
    try:
        if ticktick_client.inbox_id:
            project_ids.add(ticktick_client.inbox_id)
    except Exception as e:
        logging.error(f"Error accessing client inbox_id: {e}", exc_info=True)
        # Proceed without inbox if access fails

    if not project_ids:
        logging.warning("No project IDs found (including inbox) to fetch tasks from.")
        return []

    logging.info(f"Fetching uncompleted tasks from {len(project_ids)} projects...")
    for project_id in project_ids:
        try:
            # get_from_project fetches *uncompleted* tasks for a project
            tasks_in_project = ticktick_client.task.get_from_project(project_id)
            if tasks_in_project: # Can return None or empty list
                 # Ensure it's a list before extending
                 if isinstance(tasks_in_project, list):
                     all_tasks.extend(tasks_in_project)
                 elif isinstance(tasks_in_project, dict): # Handle single task case
                     all_tasks.append(tasks_in_project)
                 else:
                    logging.warning(f"Unexpected data type received from get_from_project for {project_id}: {type(tasks_in_project)}")
        except Exception as e:
            logging.warning(f"Failed to get tasks for project {project_id}: {e}")
            # Continue to next project even if one fails

    logging.info(f"Found {len(all_tasks)} total uncompleted tasks.")
    return all_tasks

# --- Helper for Due Date Parsing --- #
def _parse_due_date(due_date_str: Optional[str]) -> Optional[datetime.date]:
    """Parses TickTick's dueDate string (e.g., '2024-07-27T...') into a date object."""
    if not due_date_str or not isinstance(due_date_str, str):
        return None
    try:
        # Extract YYYY-MM-DD part.
        if len(due_date_str) >= 10:
            date_part = due_date_str[:10]
            return datetime.datetime.strptime(date_part, "%Y-%m-%d").date()
        else:
            logging.warning(f"dueDate string too short to parse: {due_date_str}")
            return None
    except (ValueError, TypeError) as e:
        logging.warning(f"Could not parse dueDate string '{due_date_str}': {e}")
        return None 