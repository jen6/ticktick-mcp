import datetime
import json
import logging
from typing import Optional

# Import the shared MCP instance for the decorator
from ..mcp_instance import mcp
# Import the global client instance
from ..client import ticktick_client
# Import helpers
from ..helpers import (
    format_response, require_client,
    _get_all_tasks_from_ticktick, _parse_due_date
)

# Type Hints (can be shared or moved)
TagLabel = str

# ================== #
# Filtering Tools    #
# ================== #

@mcp.tool()
@require_client
async def ticktick_get_completed_tasks(
    start_date: str, # ISO format string
    end_date: Optional[str] = None, # ISO format string
    include_time: bool = False, # Corresponds to 'full=False' in ticktick-py
    tz: Optional[str] = None
) -> str:
    """
    Retrieves a list of tasks completed within a specified date range.
    (Agent Usage Guide in docstring)
    """
    try:
        start_dt = datetime.datetime.fromisoformat(start_date)
        end_dt = datetime.datetime.fromisoformat(end_date) if end_date else None

        # ticktick-py uses 'full=True' to ignore time, 'full=False' to include time.
        # So we invert the boolean logic here.
        full_param = not include_time

        completed_tasks = ticktick_client.task.get_completed(start=start_dt, end=end_dt, full=full_param, tz=tz)
        # Ensure result is a list
        if completed_tasks is None:
             completed_tasks = []
        elif isinstance(completed_tasks, dict):
             completed_tasks = [completed_tasks]

        return format_response(completed_tasks)
    except ValueError as e:
         # Error specific to date parsing
         logging.warning(f"Invalid date format in get_completed_tasks: {e}")
         return format_response({"error": f"Invalid date format: {e}. Use ISO format (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS).", "status": "error" })
    except Exception as e:
        logging.error(f"Failed to get completed tasks (range: {start_date}-{end_date}): {e}", exc_info=True)
        return format_response({"error": f"Failed to get completed tasks: {e}", "status": "error"})

@mcp.tool()
@require_client
async def ticktick_filter_tasks(filters_json: str) -> str:
    """
    [Filtering Tool] Returns a list of *uncompleted* tasks that match the given filter conditions.
    (Agent Usage Guide in docstring)
    """
    try:
        # Parse filter JSON
        filters = json.loads(filters_json)
        if not isinstance(filters, dict):
            raise ValueError("Filters must be a JSON object (dictionary).")
    except json.JSONDecodeError:
        logging.warning(f"Invalid JSON provided to filter_tasks: {filters_json}")
        return format_response({"error": "Invalid JSON format for filters.", "status": "error"})
    except ValueError as e:
        logging.warning(f"Invalid filter structure: {e} - JSON: {filters_json}")
        return format_response({"error": str(e), "status": "error"})
    except Exception as e:
        # Catch potential other errors during filter processing
        logging.error(f"Error processing filters JSON '{filters_json}': {e}", exc_info=True)
        return format_response({"error": f"Error processing filters: {e}", "status": "error"})

    try:
        # Get all *uncompleted* tasks
        all_uncompleted_tasks = _get_all_tasks_from_ticktick()

        filtered_tasks = []
        for task in all_uncompleted_tasks:
             # Skip invalid task data (log warning)
            if not isinstance(task, dict):
                logging.warning(f"Skipping invalid task data during filtering: {task}")
                continue

            match = True
            for key, value in filters.items():
                # Check if key exists and value matches
                # Special handling for tags (list comparison)
                if key == 'tags':
                    # Requires exact match of the tag list
                    task_tags = task.get('tags', [])
                    if not isinstance(task_tags, list) or set(task_tags) != set(value):
                        match = False
                        break
                elif key not in task or task[key] != value:
                    match = False
                    break
            if match:
                filtered_tasks.append(task)

        logging.info(f"Filtered {len(all_uncompleted_tasks)} tasks down to {len(filtered_tasks)} using filters: {filters_json}")
        return format_response(filtered_tasks)
    except ConnectionError as ce:
         logging.error(f"ConnectionError during filter_tasks: {ce}", exc_info=True)
         return format_response({"error": str(ce), "status": "error"})
    except Exception as e:
        logging.error(f"Failed to get or filter tasks with filters '{filters_json}': {e}", exc_info=True)
        return format_response({"error": f"Failed to get or filter tasks: {e}", "status": "error"})

@mcp.tool()
@require_client
async def ticktick_get_due_today_or_overdue_tasks(
    tag_label: Optional[TagLabel] = None,
    sort_by_priority: bool = True
) -> str:
    """
    [Special Filtering Tool] Retrieves *uncompleted* tasks due today or overdue.
    (Agent Usage Guide in docstring)
    """
    try:
        all_uncompleted_tasks = _get_all_tasks_from_ticktick()
        today = datetime.date.today()
        filtered_tasks = []

        for task in all_uncompleted_tasks:
            if not isinstance(task, dict): continue # Skip invalid data

            due_date = _parse_due_date(task.get("dueDate"))

            # Filter by due date (due date exists and is <= today)
            if not (due_date and due_date <= today):
                continue

            # Filter by tag
            if tag_label:
                task_tags = task.get("tags", [])
                if tag_label not in task_tags:
                    continue

            filtered_tasks.append(task)

        # Sort by priority
        if sort_by_priority:
            # Sorts High (5) to None (0)
            filtered_tasks.sort(key=lambda t: t.get('priority', 0), reverse=True)

        logging.info(f"Found {len(filtered_tasks)} tasks due today/overdue (tag: {tag_label}, sorted: {sort_by_priority}).")
        return format_response(filtered_tasks)
    except ConnectionError as ce:
         logging.error(f"ConnectionError during get_due_today_or_overdue_tasks: {ce}", exc_info=True)
         return format_response({"error": str(ce), "status": "error"})
    except Exception as e:
        logging.error(f"Failed to get/filter due today/overdue tasks: {e}", exc_info=True)
        return format_response({"error": f"Failed to get/filter due today/overdue tasks: {e}", "status": "error"})

@mcp.tool()
@require_client
async def ticktick_get_due_this_friday_or_overdue_tasks(
    tag_label: Optional[TagLabel] = None,
    sort_by_priority: bool = True
) -> str:
    """
    [Special Filtering Tool] Retrieves *uncompleted* tasks due by Friday or overdue.
    (Agent Usage Guide in docstring)
    """
    try:
        all_uncompleted_tasks = _get_all_tasks_from_ticktick()
        today = datetime.date.today()
        # Calculate days until upcoming Friday (0=Mon, 4=Fri)
        days_until_friday = (4 - today.weekday() + 7) % 7
        this_friday = today + datetime.timedelta(days=days_until_friday)

        filtered_tasks = []

        for task in all_uncompleted_tasks:
            if not isinstance(task, dict): continue # Skip invalid data

            due_date = _parse_due_date(task.get("dueDate"))

            # Filter by due date (due date exists and is <= this Friday)
            if not (due_date and due_date <= this_friday):
                continue

            # Filter by tag
            if tag_label:
                task_tags = task.get("tags", [])
                if tag_label not in task_tags:
                    continue

            filtered_tasks.append(task)

        # Sort by priority
        if sort_by_priority:
            # Sorts High (5) to None (0)
            filtered_tasks.sort(key=lambda t: t.get('priority', 0), reverse=True)

        logging.info(f"Found {len(filtered_tasks)} tasks due by Friday/overdue (tag: {tag_label}, sorted: {sort_by_priority}).")
        return format_response(filtered_tasks)
    except ConnectionError as ce:
         logging.error(f"ConnectionError during get_due_this_friday_or_overdue_tasks: {ce}", exc_info=True)
         return format_response({"error": str(ce), "status": "error"})
    except Exception as e:
        logging.error(f"Failed to get/filter due by Friday/overdue tasks: {e}", exc_info=True)
        return format_response({"error": f"Failed to get/filter due by Friday/overdue tasks: {e}", "status": "error"}) 