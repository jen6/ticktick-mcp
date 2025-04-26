import datetime
import json
import logging
from typing import Optional, List, Dict, Any, Union, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
TaskStatus = Literal['uncompleted', 'completed']
TaskDict = Dict[str, Any]

# ================================= #
# Task Filterer Class              #
# ================================= #

class TaskFilterer:
    """Encapsulates logic for filtering TickTick tasks based on various criteria."""

    # Note: We are keeping access to global helpers like _get_all_tasks_from_ticktick, 
    # _parse_due_date, ticktick_client, logging, format_response for simplicity.
    # A more robust design might inject these as dependencies.

    def _get_current_date(self, tz: Optional[str]) -> datetime.date:
        """Helper to get the current date in the specified or local timezone."""
        timezone = None
        if tz:
            try:
                timezone = ZoneInfo(tz)
            except ZoneInfoNotFoundError:
                logging.warning(f"Invalid timezone '{tz}' provided for current date, falling back to local.")
                timezone = None
        return datetime.datetime.now(timezone).date()

    def _parse_optional_iso_date(self, date_iso: Optional[str], tz: Optional[str]) -> Optional[datetime.date]:
        """Helper to parse optional ISO date string, returning None if input is None or invalid."""
        if not date_iso:
            return None

        timezone = None
        if tz:
            try:
                timezone = ZoneInfo(tz)
            except ZoneInfoNotFoundError:
                logging.warning(f"Invalid timezone '{tz}' provided for date parsing, timezone info might be local or missing.")

        try:
            dt = datetime.datetime.fromisoformat(date_iso)
            if timezone and dt.tzinfo is None:
                return dt.replace(tzinfo=timezone).date()
            else:
                return dt.date()
        except ValueError:
            try:
                return datetime.date.fromisoformat(date_iso)
            except ValueError:
                logging.warning(f"Invalid ISO date format '{date_iso}', cannot parse.")
                return None
            except Exception as e:
                logging.error(f"Unexpected error parsing date '{date_iso}': {e}", exc_info=True)
                return None
        except Exception as e:
            logging.error(f"Unexpected error parsing datetime '{date_iso}': {e}", exc_info=True)
            return None

    def _apply_json_filters(self, tasks: List[TaskDict], filters: Dict[str, Any]) -> List[TaskDict]:
        """Apply JSON filters to a list of tasks, returning only tasks that match all criteria."""
        if not filters:
            return tasks
            
        filtered_tasks = []
        for task in tasks:
            if not isinstance(task, dict):
                logging.warning(f"Skipping invalid task data during filtering: {task}")
                continue

            match = True
            for key, value in filters.items():
                if key == 'tags':
                    task_tags = task.get('tags', [])
                    if not isinstance(task_tags, list) or set(task_tags) != set(value):
                        match = False
                        break
                elif key not in task or task[key] != value:
                    match = False
                    break
            if match:
                filtered_tasks.append(task)
                
        return filtered_tasks

    def _parse_filter_inputs(
        self,
        status: TaskStatus,
        filters_json: Optional[str],
        due_start_date_iso: Optional[str],
        due_end_date_iso: Optional[str],
        include_overdue: bool,
        tz: Optional[str]
    ) -> Dict[str, Any]:
        """Parses and validates input parameters for filtering."""
        parsed_inputs = {
            "filters": {},
            "filter_due_start_date": None,
            "filter_due_end_date": None,
            "error": None
        }

        if filters_json:
            try:
                filters = json.loads(filters_json)
                if not isinstance(filters, dict):
                    raise ValueError("filters_json must be a JSON object (dictionary).")
                parsed_inputs["filters"] = filters
            except json.JSONDecodeError:
                msg = f"Invalid JSON provided to filter_tasks: {filters_json}"
                logging.warning(msg)
                parsed_inputs["error"] = format_response({"error": "Invalid JSON format for filters.", "status": "error"})
                return parsed_inputs
            except ValueError as e:
                msg = f"Invalid filter structure: {e} - JSON: {filters_json}"
                logging.warning(msg)
                parsed_inputs["error"] = format_response({"error": str(e), "status": "error"})
                return parsed_inputs

        if status == 'uncompleted':
            filter_due_start_date = self._parse_optional_iso_date(due_start_date_iso, tz)
            filter_due_end_date = self._parse_optional_iso_date(due_end_date_iso, tz)

            if filter_due_start_date is None and filter_due_end_date is None and (due_start_date_iso is not None or due_end_date_iso is not None):
                logging.warning("Due date filters were provided but couldn't be parsed. No due date filtering will be applied.")
            elif filter_due_start_date is None and filter_due_end_date is None and due_start_date_iso is None and due_end_date_iso is None:
                today = self._get_current_date(tz)
                if include_overdue:
                    logging.info("No due dates provided with include_overdue=True, defaulting to include all due dates (past, present, future).")
                else:
                    logging.info("No due dates provided with include_overdue=False, defaulting to tasks due from today forward.")
                    filter_due_start_date = today

            parsed_inputs["filter_due_start_date"] = filter_due_start_date
            parsed_inputs["filter_due_end_date"] = filter_due_end_date

        return parsed_inputs

    async def _fetch_tasks_by_status(
        self,
        status: TaskStatus,
        completion_start_date_iso: Optional[str],
        completion_end_date_iso: Optional[str],
        include_time: bool,
        tz: Optional[str]
    ) -> Union[List[TaskDict], str]:
        """Fetches tasks based on status and completion date filters."""
        if status == 'completed':
            timezone = None
            if tz:
                try:
                    timezone = ZoneInfo(tz)
                except ZoneInfoNotFoundError:
                    msg = f"Invalid timezone '{tz}' provided for completed task fetching, falling back to local."
                    logging.warning(msg)
                    return format_response({"error": f"Invalid timezone specified: {tz}", "status": "error"})

            if not completion_start_date_iso and not completion_end_date_iso:
                return format_response({
                    "error": "At least one of completion_start_date_iso or completion_end_date_iso must be provided when filtering completed tasks.",
                    "status": "error"
                })

            try:
                start_dt = None
                if completion_start_date_iso:
                    start_dt = datetime.datetime.fromisoformat(completion_start_date_iso)
                    if timezone and start_dt.tzinfo is None:
                        start_dt = start_dt.replace(tzinfo=timezone)

                end_dt = None
                if completion_end_date_iso:
                    end_dt = datetime.datetime.fromisoformat(completion_end_date_iso)
                    if timezone and end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=timezone)

                full_param = not include_time
                completed_tasks = ticktick_client.task.get_completed(start=start_dt, end=end_dt, full=full_param, tz=tz)

                tasks = []
                if completed_tasks is None:
                    tasks = []
                elif isinstance(completed_tasks, dict):
                    tasks = [completed_tasks]
                else:
                    tasks = completed_tasks
                    
                logging.info(f"Retrieved {len(tasks)} completed tasks in date range")
                return tasks

            except ValueError as e:
                logging.warning(f"Invalid date format for completed tasks: {e}")
                return format_response({
                    "error": f"Invalid date format: {e}. Use ISO format (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS).",
                    "status": "error"
                })
        else:
            tasks = _get_all_tasks_from_ticktick() # Still uses global helper
            logging.info(f"Retrieved {len(tasks)} uncompleted tasks")
            return tasks

    def _filter_task_list(
        self,
        tasks: List[TaskDict],
        status: TaskStatus,
        filters: Dict[str, Any],
        filter_due_start_date: Optional[datetime.date],
        filter_due_end_date: Optional[datetime.date],
        tag_label: Optional[TagLabel]
    ) -> List[TaskDict]:
        """Applies JSON, due date, and tag filters to a list of tasks."""
        original_count = len(tasks)
        logging.info(f"Starting filtering with {original_count} tasks.")

        if filters:
            tasks = self._apply_json_filters(tasks, filters)
            logging.info(f"After JSON filters: {len(tasks)} tasks remain")

        if status == 'uncompleted':
            filtered_tasks_uncompleted = []
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                
                due_date = _parse_due_date(task.get("dueDate")) # Still uses global helper
                
                if (filter_due_start_date or filter_due_end_date) and not due_date:
                    continue
                if filter_due_start_date and due_date < filter_due_start_date:
                    continue
                if filter_due_end_date and due_date > filter_due_end_date:
                    continue
                
                if tag_label:
                    task_tags = task.get("tags", [])
                    if tag_label not in task_tags:
                        continue
                
                filtered_tasks_uncompleted.append(task)
            
            tasks = filtered_tasks_uncompleted
            logging.info(f"After due date & tag filtering (uncompleted): {len(tasks)} tasks remain")
            
        elif status == 'completed' and tag_label:
            filtered_tasks_completed = []
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                task_tags = task.get("tags", [])
                if tag_label not in task_tags:
                    continue
                filtered_tasks_completed.append(task)
            tasks = filtered_tasks_completed
            logging.info(f"After tag filtering (completed): {len(tasks)} tasks remain")

        reduction = original_count - len(tasks)
        if original_count > 0:
            percent_reduction = (reduction / original_count) * 100
            logging.info(f"Filtering reduced tasks by {reduction} ({percent_reduction:.1f}%) from {original_count} to {len(tasks)}")

        return tasks

    async def filter(
        self,
        status: TaskStatus,
        filters_json: Optional[str],
        due_start_date_iso: Optional[str],
        due_end_date_iso: Optional[str],
        completion_start_date_iso: Optional[str],
        completion_end_date_iso: Optional[str],
        include_time: bool,
        include_overdue: bool,
        tag_label: Optional[TagLabel],
        sort_by_priority: bool,
        tz: Optional[str]
    ) -> Union[List[TaskDict], str]: # Return list or error string
        """Orchestrates the task filtering process."""
        # 1. Parse Inputs
        parsed_inputs = self._parse_filter_inputs(
            status=status,
            filters_json=filters_json,
            due_start_date_iso=due_start_date_iso,
            due_end_date_iso=due_end_date_iso,
            include_overdue=include_overdue,
            tz=tz
        )
        if parsed_inputs.get("error"):
            return parsed_inputs["error"]
        filters = parsed_inputs["filters"]
        filter_due_start_date = parsed_inputs["filter_due_start_date"]
        filter_due_end_date = parsed_inputs["filter_due_end_date"]

        # 2. Fetch Tasks
        fetched_result = await self._fetch_tasks_by_status(
            status=status,
            completion_start_date_iso=completion_start_date_iso,
            completion_end_date_iso=completion_end_date_iso,
            include_time=include_time,
            tz=tz
        )
        if isinstance(fetched_result, str): # Check for error string
            return fetched_result
        tasks: List[TaskDict] = fetched_result

        # 3. Filter Tasks
        tasks = self._filter_task_list(
            tasks=tasks,
            status=status,
            filters=filters,
            filter_due_start_date=filter_due_start_date,
            filter_due_end_date=filter_due_end_date,
            tag_label=tag_label
        )

        # 4. Sort Results
        if sort_by_priority:
            tasks.sort(key=lambda t: t.get('priority', 0) if isinstance(t, dict) else 0, reverse=True)
            logging.info("Sorted tasks by priority (high to low)")

        return tasks # Return the final list of tasks


# ================================= #
# Main Filtering Tool (MCP Entry)  #
# ================================= #

@mcp.tool()
@require_client
async def ticktick_filter_tasks(
    status: TaskStatus = 'uncompleted',
    filters_json: Optional[str] = None,
    due_start_date_iso: Optional[str] = None,
    due_end_date_iso: Optional[str] = None,
    completion_start_date_iso: Optional[str] = None, 
    completion_end_date_iso: Optional[str] = None,
    include_time: bool = False,
    include_overdue: bool = True,
    tag_label: Optional[TagLabel] = None,
    sort_by_priority: bool = False,
    tz: Optional[str] = None
) -> str:
    """
    [Universal Task Filter] Retrieves and filters tasks based on multiple criteria.
    
    Agent Usage Guide:
    - Purpose: Single tool for retrieving and filtering tasks by status, date ranges, exact field matches, and common attributes.
    - Parameters:
      - 'status': Either 'uncompleted' (default) or 'completed'. Determines if you want active or completed tasks.
      - 'filters_json': Optional JSON object as string with exact field matches, e.g. `'{"priority": 5, "projectId": "inbox"}'`.
                       For complex field filtering (exact matches only).
      
      - Date Range Filters:
        - 'due_start_date_iso': Start of due date range (ISO format). Only for uncompleted tasks.
        - 'due_end_date_iso': End of due date range (ISO format). Only for uncompleted tasks.
        - 'completion_start_date_iso': Start of completion date range (ISO format). Only for completed tasks.
        - 'completion_end_date_iso': End of completion date range (ISO format). Only for completed tasks.
      
      - Behavior Settings:
        - 'include_time': If True, uses exact time comparisons for completion date ranges. Default False.
        - 'include_overdue': If True (default) and due_start_date_iso is None, includes overdue tasks. If False, strictly enforces range.
        - 'tag_label': Filter tasks by a specific tag name. Case-sensitive exact match.
        - 'sort_by_priority': If True, sorts results by priority (High to None). Default False.
        - 'tz': Timezone for date comparisons (e.g., 'America/New_York', 'UTC'). Default is system local time.
    
    - Common Use Cases:
      1. Get tasks due today: `status='uncompleted', due_start_date_iso=today, due_end_date_iso=today`
      2. Get tasks due today or overdue: `status='uncompleted', due_end_date_iso=today`
      3. Get completed tasks in date range: `status='completed', completion_start_date_iso=X, completion_end_date_iso=Y`
      4. Get high priority tasks in a project: `status='uncompleted', filters_json='{"priority":5,"projectId":"project123"}'`
      5. Get tasks with specific tag due this week: `status='uncompleted', tag_label="work", due_end_date_iso=friday`
    
    - Returns: JSON list of task objects matching criteria, or error information.
    """
    filterer = TaskFilterer()
    try:
        result = await filterer.filter(
            status=status,
            filters_json=filters_json,
            due_start_date_iso=due_start_date_iso,
            due_end_date_iso=due_end_date_iso,
            completion_start_date_iso=completion_start_date_iso,
            completion_end_date_iso=completion_end_date_iso,
            include_time=include_time,
            include_overdue=include_overdue,
            tag_label=tag_label,
            sort_by_priority=sort_by_priority,
            tz=tz
        )

        # The filter method now returns either the list or the error string directly
        if isinstance(result, str):
            return result # Return error string as is
        else:
            return format_response(result) # Format the list of tasks

    except ConnectionError as ce:
        logging.error(f"ConnectionError during filter_tasks: {ce}", exc_info=True)
        return format_response({"error": str(ce), "status": "error"})
    except Exception as e:
        # Detailed error logging
        logging.error(
            f"Unexpected error in ticktick_filter_tasks tool: status={status}, filters_json={filters_json}, "
            f"due_dates=[{due_start_date_iso}-{due_end_date_iso}], "
            f"completion_dates=[{completion_start_date_iso}-{completion_end_date_iso}], "
            f"tag={tag_label}, include_overdue={include_overdue}, tz={tz}: {e}",
            exc_info=True
        )
        return format_response({"error": f"Failed to filter tasks due to unexpected error: {e}", "status": "error"})
