import datetime
import functools
import json
import logging
from typing import Any, Dict, List, Optional

from . import client

TaskObject = Dict[str, Any]

# --- Helper Function --- #
def format_response(result: Any) -> str:
    """Formats the result from ticktick-py into a JSON string for MCP."""
    if isinstance(result, (dict, list)):
        try:
            return json.dumps(result, indent=2, default=str)
        except TypeError as e:
            logging.error(f"Failed to serialize response object: {e} - Object: {result}", exc_info=True)
            return json.dumps({"error": "Failed to serialize response", "details": str(e)})
    elif result is None:
         return json.dumps(None)
    else:
        logging.warning(f"Formatting unexpected type: {type(result)} - Value: {result}")
        return json.dumps({"result": str(result)})

# --- Decorator for Client Check --- #
def require_client(func):
    """Decorator to check if ticktick_client is initialized before calling the tool."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        logging.info(f"TickTick client: {client.ticktick_client}")
        if not client.ticktick_client:
            logging.error("TickTick client accessed before initialization in tool function.")
            return format_response({"error": "TickTick client not initialized."})
        return await func(*args, **kwargs)
    return wrapper

# --- Internal Helper to Get All Tasks --- #
def _get_all_tasks_from_ticktick() -> List[TaskObject]:
    """Internal helper to fetch all *uncompleted* tasks from all projects."""
    if not client.ticktick_client:
        logging.error("_get_all_tasks_from_ticktick called when client is not initialized.")
        raise ConnectionError("TickTick client not initialized.")

    all_tasks = []
    try:
        projects_state = client.ticktick_client.state.get('projects', [])
    except Exception as e:
        logging.error(f"Error accessing client state for projects: {e}", exc_info=True)
        projects_state = []

    # Get unique project IDs from state, add inbox ID
    project_ids = {p.get('id') for p in projects_state if p.get('id')}
    try:
        if client.ticktick_client.inbox_id:
            project_ids.add(client.ticktick_client.inbox_id)
    except Exception as e:
        logging.error(f"Error accessing client inbox_id: {e}", exc_info=True)

    logging.debug(f"Fetching uncompleted tasks from {len(project_ids)} projects...")
    for project_id in project_ids:
        try:
            # get_from_project fetches *uncompleted* tasks for a project
            tasks_in_project = client.ticktick_client.task.get_from_project(project_id)
            if tasks_in_project:
                 if isinstance(tasks_in_project, list):
                     all_tasks.extend(tasks_in_project)
                 elif isinstance(tasks_in_project, dict):
                     all_tasks.append(tasks_in_project)
                 else:
                    logging.warning(f"Unexpected data type received from get_from_project for {project_id}: {type(tasks_in_project)}")
        except Exception as e:
            logging.warning(f"Failed to get tasks for project {project_id}: {e}")

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