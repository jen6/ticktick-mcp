import datetime
import json
import logging
from typing import Optional, List, Dict, Any, Union, Literal, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, Field, validator

# Import the shared MCP instance for the decorator
from ..mcp_instance import mcp
# Import the global client instance
from ..client import ticktick_client
# Import helpers
from ..helpers import (
    format_response, require_client,
    _get_all_tasks_from_ticktick
)

# Type Hints (can be shared or moved)
TagLabel = str
TaskStatus = Literal['uncompleted', 'completed']
TaskDict = Dict[str, Any]

class PeriodFilter(BaseModel):
    start_date: Optional[datetime.datetime] = Field(None, description="Start date/time for filtering period")
    end_date: Optional[datetime.datetime] = Field(None, description="End date/time for filtering period")
    tz: Optional[ZoneInfo] = Field(None, description="Timezone for date/time interpretation")

    @validator('start_date', 'end_date', pre=True, always=True)
    def format_time(cls, v: Optional[str], values: Dict[str, Any]) -> Optional[datetime.datetime]:
        if not v:
            return None
        
        timezone = values.get('tz')
        try:
            converted_dt = datetime.datetime.fromisoformat(v)
            if timezone and converted_dt.tzinfo is None:
                 converted_dt = timezone.localize(converted_dt)
            elif not timezone and converted_dt.tzinfo is not None:
                logging.warning(f"Timezone provided in date string '{v}' but no 'tz' parameter specified. Converting to local time.")
                converted_dt = converted_dt.astimezone(None).replace(tzinfo=None)
            return converted_dt
        except ValueError:
            try:
                date_only = datetime.date.fromisoformat(v)
                dt_start_of_day = datetime.datetime.combine(date_only, datetime.time.min)
                if timezone:
                    return timezone.localize(dt_start_of_day)
                else:
                    return dt_start_of_day
            except ValueError:
                logging.warning(f"Invalid ISO date/datetime format '{v}', cannot parse.")
                return None
        except Exception as e:
            logging.error(f"Unexpected error parsing datetime '{v}': {e}", exc_info=True)
            return None


    def contains(self,
                 date_str: Optional[str]
                 ) -> bool:
        """Checks if the date_str falls within the filter's period [start_date, end_date]."""
        if not date_str:
            return not (self.start_date or self.end_date)

        task_date = self._parse_task_date(date_str)
        if not task_date:
            return not (self.start_date or self.end_date)

        compare_task_date = task_date.date()
        compare_start_date = self.start_date.date() if self.start_date else None
        compare_end_date = self.end_date.date() if self.end_date else None

        if compare_start_date and compare_task_date < compare_start_date:
            return False

        if compare_end_date and compare_task_date > compare_end_date:
            return False

        return True

    def _parse_task_date(self, date_str: str) -> Optional[datetime.datetime]:
        try:
            if 'T' in date_str:
                 try:
                      if date_str.endswith('Z'):
                          date_str = date_str[:-1] + '+00:00'
                      dt = datetime.datetime.fromisoformat(date_str.replace(".000", ""))
                 except ValueError:
                      logging.warning(f"Could not parse task date '{date_str}' with fromisoformat, trying without offset.")
                      # Try parsing without timezone if fromisoformat fails with it
                      dt_str_no_offset = date_str.split('+')[0].split('Z')[0].replace(".000", "")
                      dt = datetime.datetime.fromisoformat(dt_str_no_offset)

            else:
                date_only = datetime.date.fromisoformat(date_str)
                dt = datetime.datetime.combine(date_only, datetime.time.min)

            # Apply filter's timezone if task date is naive
            if self.tz and dt.tzinfo is None:
                 dt = self.tz.localize(dt)
            # Convert task's timezone to filter's timezone if both exist
            elif self.tz and dt.tzinfo is not None:
                 dt = dt.astimezone(self.tz)
            # If no filter timezone, make task date naive (use system's local time)
            elif not self.tz and dt.tzinfo is not None:
                 dt = dt.astimezone(None).replace(tzinfo=None)

            return dt
        except Exception as e:
            logging.warning(f"Failed to parse task date string '{date_str}': {e}")
            return None

class PropertyFilter(BaseModel):
    """Defines the criteria for filtering TickTick tasks.

    Attributes:
        status: Task status to filter by ('uncompleted' or 'completed').
        project_id: Optional project ID string.
        priority: Optional priority integer (0, 1, 3, 5).
        tag_label: Optional tag string.
        due_date_filter: Optional PeriodFilter object for uncompleted tasks.
        completion_date_filter: Optional PeriodFilter object for completed tasks.
    """
    tag_label: Optional[TagLabel] = Field(None, description="Filter tasks by specific tag")
    project_id: Optional[str] = Field(None, description="Filter tasks by project ID")
    priority: Optional[int] = Field(None, description="Filter tasks by priority level (0=None, 1=Low, 3=Medium, 5=High)")
    due_date_filter: Optional[PeriodFilter] = Field(None, description="Filter for task due dates")
    completion_date_filter: Optional[PeriodFilter] = Field(None, description="Filter for task completion dates")
    status: TaskStatus = Field("uncompleted", description="Task status to filter by (uncompleted or completed)")


    def matches(self, task: TaskDict) -> bool:
        task_tags = task.get('tags', [])
        if self.tag_label and self.tag_label not in task_tags:
            return False
        if self.project_id and task.get('projectId') != self.project_id:
            return False
        if self.priority is not None and task.get('priority') != self.priority:
            return False

        # Check status match AFTER property checks
        task_status_value = task.get('status', 0) # 0=uncompleted, 2=completed in TickTick API
        task_is_completed = task_status_value == 2
        filter_wants_completed = self.status == 'completed'

        if filter_wants_completed != task_is_completed:
             # If the basic status doesn't match, no need to check dates
             return False

        # Now check date filters based on the *matched* status
        if not task_is_completed and self.due_date_filter: # Uncompleted task, check due date
            task_due_date = task.get("dueDate")
            if not self.due_date_filter.contains(task_due_date):
                return False
        elif task_is_completed and self.completion_date_filter: # Completed task, check completion date
            if not self.completion_date_filter.contains(task.get("completedTime")):
                return False

        # All relevant checks passed
        return True

class TaskFilterer:
    """Encapsulates logic for filtering TickTick tasks based on various criteria."""

    async def _fetch_tasks_by_status(
        self,
        status: TaskStatus,
        completion_date_filter: Optional[PeriodFilter], # Pass the filter object
        tz_info: Optional[ZoneInfo] # Use ZoneInfo object
    ) -> List[TaskDict]:
        """Fetches tasks based on status and completion date filters."""

        if status == 'completed':
            if not completion_date_filter or (not completion_date_filter.start_date and not completion_date_filter.end_date):
                 # Require at least a start or end date for completed tasks fetch if filter provided
                 # If no date filter is intended, completion_date_filter should be None
                logging.warning("Fetching completed tasks requires a start or end date in the completion_date_filter.")
                 # Decide behavior: fetch all completed? Return empty? Raise error?
                 # Let's return empty to avoid fetching potentially huge amounts of data without date bounds.
                return []
                 # Alternative: raise ValueError("A start or end date must be provided for filtering completed tasks.")

            try:
                # Use the dates directly from the PeriodFilter object
                # The ticktick client might expect date objects or string representations
                # Adapt based on ticktick_client.task.get_completed signature
                start_dt = completion_date_filter.start_date
                end_dt = completion_date_filter.end_date

                # Convert to required format (YYYY-MM-DD string), potentially adjusting for timezone
                # The client expects dates relative to the user's *account* timezone.
                # For simplicity here, we'll format based on the provided tz_info.
                # If tz_info is None, use the date as is (assuming local time).
                start_arg = start_dt.strftime('%Y-%m-%d') if start_dt else None
                end_arg = end_dt.strftime('%Y-%m-%d') if end_dt else None


                # ticktick-py get_completed takes datetime objects, not strings
                # Let's pass the datetime objects directly
                # It handles timezone conversion internally based on client settings
                tasks = await ticktick_client.task.get_completed(
                    from_date=start_dt, # Use datetime object
                    to_date=end_dt,     # Use datetime object
                    # Removed tz argument as client handles it
                )

                logging.debug(f"Retrieved {len(tasks)} completed tasks in date range from API")

                # Re-apply the period filter for precise time matching if needed
                # (API might only filter by day)
                precise_filtered_tasks = [t for t in tasks if completion_date_filter.contains(t.get("completedTime"))]

                if len(precise_filtered_tasks) < len(tasks):
                    logging.debug(f"Filtered down to {len(precise_filtered_tasks)} completed tasks matching time component using PeriodFilter")

                return precise_filtered_tasks

            except Exception as e: # Catch broader exceptions from API call
                logging.error(f"Error fetching completed tasks: {e}", exc_info=True)
                # Propagate or handle error (e.g., return empty list with warning)
                raise ConnectionError(f"Failed to fetch completed tasks from TickTick: {e}") from e

        else: # status == 'uncompleted'
            # Fetch all uncompleted tasks; filtering happens later
            tasks = _get_all_tasks_from_ticktick()
            logging.debug(f"Retrieved {len(tasks)} uncompleted tasks")
            return tasks

    async def filter(
        self,
        property_filter: PropertyFilter, # Pass the unified filter object
        sort_by_priority: bool,
        tz_info: Optional[ZoneInfo] # Pass ZoneInfo
    ) -> List[TaskDict]:
        """Orchestrates the task filtering process using PropertyFilter."""

        # 1. Fetch Tasks based on status and completion date range (if applicable)
        # Pass the relevant date filter object to fetcher
        completion_filter = property_filter.completion_date_filter if property_filter.status == 'completed' else None

        tasks = await self._fetch_tasks_by_status(
            status=property_filter.status,
            completion_date_filter=completion_filter,
            tz_info=tz_info # Pass ZoneInfo
        )

        # 2. Filter Tasks using the comprehensive property_filter
        filtered_tasks = [t for t in tasks if property_filter.matches(t)]
        logging.info(f"Filtered {len(tasks)} fetched tasks down to {len(filtered_tasks)} matching criteria.")


        # 3. Sort Results (if requested)
        if sort_by_priority:
            filtered_tasks.sort(
                key=lambda t: t.get('priority', 0), # Sort 0 (None) lowest
                reverse=True # High priority first
            )
            logging.debug("Sorted tasks by priority (descending).")


        return filtered_tasks


# --- Helper Function to Build Filter --- #

def _build_property_filter(
    filter_criteria: Union[str, Dict[str, Any]]
) -> Tuple[PropertyFilter, Optional[ZoneInfo], bool]:
    """Constructs PeriodFilter, PropertyFilter objects, and extracts sort flag from raw filter criteria."""
    criteria: Dict[str, Any] = {}

    # Parse filter_criteria if it's a string
    if isinstance(filter_criteria, str):
        try:
            criteria = json.loads(filter_criteria)
        except json.JSONDecodeError as e:
            logging.error(f"Invalid JSON string provided for filter_criteria: {e}")
            # Re-raise as ValueError to be caught by the main tool function
            raise ValueError(f"Invalid JSON string provided: {e}") from e
    elif isinstance(filter_criteria, dict):
        criteria = filter_criteria
    else:
        raise ValueError("filter_criteria must be a JSON string or a dictionary")

    # Extract parameters from the criteria dictionary
    status = criteria.get("status", "uncompleted")
    project_id = criteria.get("project_id")
    tag_label = criteria.get("tag_label")
    priority = criteria.get("priority")
    due_start_date = criteria.get("due_start_date")
    due_end_date = criteria.get("due_end_date")
    completion_start_date = criteria.get("completion_start_date")
    completion_end_date = criteria.get("completion_end_date")
    sort_by_priority = criteria.get("sort_by_priority", False)
    tz = criteria.get("tz")

    # Validate status type
    if status not in ["uncompleted", "completed"]:
        raise ValueError("Invalid status value. Must be 'uncompleted' or 'completed'.")

    # Build ZoneInfo
    tz_info: Optional[ZoneInfo] = None
    if tz:
        try:
            tz_info = ZoneInfo(tz)
        except ZoneInfoNotFoundError:
            logging.warning(f"Invalid timezone '{tz}' provided. Using local time.")
            # Continue without tz_info

    # Build Period Filters
    due_filter = None
    if due_start_date or due_end_date:
        due_filter = PeriodFilter(
            start_date=due_start_date,
            end_date=due_end_date,
            tz=tz_info
        )

    completion_filter = None
    if completion_start_date or completion_end_date:
         if status != 'completed':
              logging.warning("Completion date filter provided but status is not 'completed'. Ignoring completion dates.")
         else:
            completion_filter = PeriodFilter(
                start_date=completion_start_date,
                end_date=completion_end_date,
                tz=tz_info
            )

    # Build Property Filter
    property_filter = PropertyFilter(
        status=status,
        project_id=project_id,
        tag_label=tag_label,
        priority=priority,
        due_date_filter=due_filter if status == 'uncompleted' else None,
        completion_date_filter=completion_filter if status == 'completed' else None
    )

    return property_filter, tz_info, sort_by_priority


# ================================= #
# Main Filtering Tool (MCP Entry)  #
# ================================= #

@mcp.tool()
@require_client
async def ticktick_filter_tasks(
    filter_criteria: Dict[str, Any]
) -> str:
    """
    Filters TickTick tasks based on specified criteria provided as a JSON string or dictionary,
    and returns a list of matching tasks as a JSON string.

    Args:
        filter_criteria: A JSON dictionary containing filter parameters.
            Expected keys:
            - status (str): Task status ('uncompleted' or 'completed'). Defaults to 'uncompleted'.
            - project_id (str, optional): Project ID to filter by.
            - tag_label (str, optional): Tag name to filter by.
            - priority (int, optional): Priority level (0=None, 1=Low, 3=Medium, 5=High).
            - due_start_date (str, optional): ISO format start date/time for due date filter.
            - due_end_date (str, optional): ISO format end date/time for due date filter.
            - completion_start_date (str, optional): ISO format start date/time for completion date filter (requires status='completed').
            - completion_end_date (str, optional): ISO format end date/time for completion date filter (requires status='completed').
            - sort_by_priority (bool, optional): Sort results by priority (descending). Defaults to False.
            - tz (str, optional): Timezone name (e.g., 'America/New_York') for date interpretation.
    Example:
        {
            "status": "uncompleted",
            "due_end_date": "2024-01-31",
            "tz": "Asia/Seoul"
        }
    Returns:
        A JSON string representing a list of task objects matching the filter criteria,
        or a JSON object with an error message.
    """
    filterer = TaskFilterer()

    try:
        # Build the filter objects and get sort flag using the helper function
        property_filter, tz_info, sort_by_priority = _build_property_filter(filter_criteria)

        # Execute the filter
        result = await filterer.filter(
            property_filter=property_filter,
            sort_by_priority=sort_by_priority, # Use value from helper
            tz_info=tz_info
        )

        # Format success response
        return format_response(result)

    except (ConnectionError, ValueError) as e: # Catch errors from filterer/fetch/parsing
        logging.error(f"Error during filter_tasks execution: {e}", exc_info=True)
        return format_response({"error": str(e), "status": "error"})
    except Exception as e:
        # Detailed error logging for unexpected issues
        logging.error(
            f"Unexpected error in ticktick_filter_tasks tool: filter_criteria={filter_criteria}: {e}",
            exc_info=True
        )
        return format_response({"error": f"Failed to filter tasks due to unexpected error: {e}", "status": "error"})
