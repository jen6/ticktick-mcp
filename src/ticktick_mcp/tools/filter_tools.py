import datetime
import json
import logging
from typing import Optional, List, Dict, Any, Union, Literal
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
                      dt = datetime.datetime.fromisoformat(date_str.split('+')[0].split('Z')[0].replace(".000", ""))

            else:
                date_only = datetime.date.fromisoformat(date_str)
                dt = datetime.datetime.combine(date_only, datetime.time.min)

            if self.tz and dt.tzinfo is None:
                 dt = self.tz.localize(dt)
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
    tag_label: Optional[str] = Field(None, description="Filter tasks by specific tag")
    project_id: Optional[str] = Field(None, description="Filter tasks by project ID")
    priority: Optional[int] = Field(None, description="Filter tasks by priority level")
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

        if self.status == 'uncompleted' and self.due_date_filter:
            task_due_date = task.get("dueDate")
            if not self.due_date_filter.contains(task_due_date):
                return False

        elif self.status == 'completed' and self.completion_date_filter:
            if not self.completion_date_filter.contains(task.get("completedTime")): 
                return False
                
        return True

class TaskFilterer:
    """Encapsulates logic for filtering TickTick tasks based on various criteria."""

    async def _fetch_tasks_by_status(
        self,
        status: TaskStatus,
        completion_date_filter: Optional[PeriodFilter], # Pass the filter object
        tz: Optional[str] # Keep tz for get_completed
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

                # Convert to required format if necessary (e.g., YYYY-MM-DD string)
                start_arg = start_dt.strftime('%Y-%m-%d') if start_dt else None
                end_arg = end_dt.strftime('%Y-%m-%d') if end_dt else None

                tasks = await ticktick_client.task.get_completed(
                    startDay=start_arg, # Assuming client uses 'startDay', 'endDay'
                    endDay=end_arg,
                    # 'full' param might be related to include_time logic, TBD based on client details
                    # tz=tz # Pass timezone if client supports it
                )

                logging.debug(f"Retrieved {len(tasks)} completed tasks in date range")
                # Filter further by time component if needed, as get_completed might only filter by day
                if completion_date_filter.contains(tasks[0].get("completedTime")):
                     tasks = [t for t in tasks if completion_date_filter.contains(t.get("completedTime"))]
                     logging.debug(f"Filtered down to {len(tasks)} completed tasks matching time component")
                return tasks

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
        # Remove parameters now handled by PropertyFilter/PeriodFilter
        # status is in property_filter
        # filters_json is parsed before creating property_filter
        # date ranges are in property_filter.due_date_filter/completion_date_filter
        # tag_label is in property_filter
        # include_overdue is in property_filter.*_date_filter.use_time_component
        tz: Optional[str] # Keep tz for fetch logic if needed separately
    ) -> List[TaskDict]:
        """Orchestrates the task filtering process using PropertyFilter."""

        # 1. Fetch Tasks based on status and completion date range (if applicable)
        # Pass the relevant date filter object to fetcher
        completion_filter = property_filter.completion_date_filter if property_filter.status == 'completed' else None
        tasks = await self._fetch_tasks_by_status(
            status=property_filter.status,
            completion_date_filter=completion_filter,
            tz=tz # Pass tz if needed by fetcher
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


# ================================= #
# Main Filtering Tool (MCP Entry)  #
# ================================= #

@mcp.tool()
@require_client
async def ticktick_filter_tasks(
    # Replace individual parameters with the Pydantic model
    property_filter: PropertyFilter,
    # Keep sort_by_priority and tz as they are not part of PropertyFilter
    sort_by_priority: bool = False,
    tz: Optional[str] = None # tz might still be useful for API calls if client needs it
) -> str:
    """
    [Universal Task Filter] Retrieves and filters tasks based on a PropertyFilter object.
*   **Purpose:** Use this tool to find TickTick tasks matching specific conditions like status, due date, priority, project, etc.
*   **IMPORTANT:** Provide filter criteria directly as **top-level key-value pairs** in the function call. **Do NOT nest filters under a `property_filter` key.** The function expects individual filter arguments.
*   **Filter Parameters:**
    *   `status`: (Optional[str]) Filter by completion status (e.g., "uncompleted", "completed").
    *   `priority`: (Optional[int]) Filter by priority level (e.g., 0=None, 1=Low, 3=Medium, 5=High).
    *   `project_id`: (Optional[str]) Filter by project ID (e.g., "p12345").
    *   `due_date_filter`: (Optional[dict]) Filter by due date range. Must be a dictionary containing:
        *   `start_date`: (Optional[str]) Start date (YYYY-MM-DD). Inclusive.
        *   `end_date`: (Optional[str]) End date (YYYY-MM-DD). Inclusive.
        *   *Note:* At least one of `start_date` or `end_date` must be provided if using `due_date_filter`. All date comparisons ignore time.
    *   `start_date_filter`: (Optional[dict]) Filter by start date range (same structure as `due_date_filter`).
    *   `list_id`: (Optional[str]) Filter by list ID.
    *   `tag`: (Optional[str]) Filter by a specific tag name.
    *   `created_date_filter`: (Optional[dict]) Filter by creation date range (same structure as `due_date_filter`).
    *   `completed_date_filter`: (Optional[dict]) Filter by completion date range (same structure as `due_date_filter`).
*   **Other Parameters:**
    *   `sort_by_priority`: (Optional[bool]) If True, sorts results by priority (High to None) after filtering. Default: False.
    *   `tz`: (Optional[str]) Timezone for date operations (e.g., 'America/New_York', 'UTC'). Affects date parsing if dates are ambiguous. Default: System local time.
*   **Examples:**
    *   **High priority, uncompleted tasks in project "p123":**
        Call with: `status="uncompleted", priority=5, project_id="p123"`
    *   **Uncompleted tasks due today (e.g., "2023-10-27"):**
        Call with: `status="uncompleted", due_date_filter={"start_date": "2023-10-27", "end_date": "2023-10-27"}`
    *   **Uncompleted tasks due up to today (e.g., "2024-09-30"):**
        Call with: `status="uncompleted", due_date_filter={"end_date": "2024-09-30"}`
*   **Returns:** JSON list of task objects matching criteria, or error information.
    """
    filterer = TaskFilterer()

    try:
        # Execute the filter
        result = await filterer.filter(
            property_filter=property_filter,
            sort_by_priority=sort_by_priority,
            tz=tz # Pass tz mainly for fetcher/date context
        )

        # Format success response
        return format_response(result)

    except (ConnectionError, ValueError) as e: # Catch errors from filterer/fetch
        logging.error(f"Error during filter_tasks execution: {e}", exc_info=True)
        return format_response({"error": str(e), "status": "error"})
    except Exception as e:
        # Detailed error logging for unexpected issues
        logging.error(
            f"Unexpected error in ticktick_filter_tasks tool: filter={property_filter}, sort={sort_by_priority}, tz={tz}: {e}",
            exc_info=True
        )
        return format_response({"error": f"Failed to filter tasks due to unexpected error: {e}", "status": "error"})
