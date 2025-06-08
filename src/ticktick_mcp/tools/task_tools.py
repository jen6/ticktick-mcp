import datetime
import logging
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field, GetCoreSchemaHandler, field_serializer, model_validator
from ticktick.helpers.time_methods import convert_date_to_tick_tick_format
from tzlocal import get_localzone

# Import the shared MCP instance for the decorator
from ..mcp_instance import mcp
# Import the singleton class
from ..client import TickTickClientSingleton
# Import helpers
from ..helpers import format_response, require_ticktick_client, _get_all_tasks_from_ticktick, ToolLogicError

# Type Hints (can be shared or moved)
TaskId = str
ProjectId = str
# TaskObject = Dict[str, Any] # Removed old type alias
ListOfTaskIds = List[TaskId]

# Pydantic Models based on user schema and common TickTick fields
class SubtaskItem(BaseModel):
    """Represents a subtask item within a TickTick task."""
    title: str
    startDate: Optional[datetime.datetime] = None
    isAllDay: Optional[bool] = None
    sortOrder: Optional[int] = None
    timeZone: Optional[str] = None
    status: Optional[int] = None # 0 = incomplete, 1 = complete? Check API docs
    completedTime: Optional[datetime.datetime] = None

    class Config:
        # Allow population by field name OR alias if needed later
        # populate_by_name = True
        pass

class TaskObject(BaseModel):
    """
    Represents a TickTick Task.
    Based on provided schema and common API fields.
    Note: Date fields expect datetime objects, conversion might be needed
    at API boundaries if ISO strings are used.
    """
    # --- Fields from User Schema ---
    title: Optional[str] = None
    content: Optional[str] = None
    desc: Optional[str] = None # Often used interchangeably with content
    isAllDay: Optional[bool] = Field(None, alias="allDay") # Use schema name, alias for potential API mismatch
    startDate: Optional[datetime.datetime] = None
    dueDate: Optional[datetime.datetime] = None
    timeZone: Optional[str] = None
    reminders: Optional[List[str]] = None # Structure might be more complex, check API
    repeatFlag: Optional[str] = Field(None, alias="repeat") # Use schema name, alias for potential API mismatch
    priority: Optional[int] = 0 # 0: None, 1: Low, 3: Medium, 5: High
    sortOrder: Optional[int] = None
    items: Optional[List[SubtaskItem]] = None

    # --- Common Fields from TickTick API ---
    id: Optional[str] = None # Task ID, usually present in responses/updates
    projectId: Optional[str] = None # Project ID task belongs to
    status: Optional[int] = None # 0: incomplete, 2: completed? Check API docs
    createdTime: Optional[datetime.datetime] = None
    modifiedTime: Optional[datetime.datetime] = None
    completedTime: Optional[datetime.datetime] = None
    tags: Optional[List[str]] = None # List of tag names
    etag: Optional[str] = None # Entity tag for caching/updates
    
    @field_serializer('startDate', 'dueDate')
    def serialize_datetime(self, value: datetime.datetime, _info: GetCoreSchemaHandler) -> str:
        if value is None:
            return None
        if self.timeZone is None:
            self.timeZone = get_localzone().key
        return convert_date_to_tick_tick_format(value, self.timeZone)

    class Config:
        # Allow population by field name OR alias
        populate_by_name = True
        # Allow arbitrary types if needed for complex nested structures from API
        # arbitrary_types_allowed = True
        pass
    
    def update(self, src: "TaskObject"):
        """
        Update the current task object with values from another TaskObject.
        Overwrites existing values with non-None values from src.
        """
        for field in self.__dict__:
            if getattr(src, field) is not None:
                setattr(self, field, getattr(src, field))

# ================== #
# Task Tools         #
# ================== #

@mcp.tool()
@require_ticktick_client
async def ticktick_create_task(
    title: str,
    projectId: Optional[str] = None,
    content: Optional[str] = None,
    desc: Optional[str] = None,
    allDay: Optional[bool] = None,
    startDate: Optional[str] = None,  # Expects ISO format string or datetime
    dueDate: Optional[str] = None,    # Expects ISO format string or datetime
    timeZone: Optional[str] = None,
    reminders: Optional[List[str]] = None,
    repeat: Optional[str] = None,
    priority: Optional[int] = None,
    sortOrder: Optional[int] = None,
    items: Optional[List[Dict]] = None,
) -> str:
    """
    Creates a new task in TickTick.

    Args:
        title (str): The title of the task. Required.
        projectId (str, optional): ID of the project to add the task to. Defaults to user's Inbox if not specified.
        content (str, optional): Additional details or notes for the task. HTML formatting is supported.
        desc (str, optional): Description for the task. Used as an alternative to content in some contexts.
        allDay (bool, optional): Set to True if the task spans the entire day. Defaults to False for tasks with time.
        startDate (str, optional): Start date/time in ISO 8601 format (e.g., '2024-07-26T10:00:00+09:00' or '2024-07-26').
                                  Use ticktick_convert_datetime_to_ticktick_format if needed.
        dueDate (str, optional): Due date/time in ISO 8601 format. If date only ('2024-07-26'), defaults to end of day.
                                Use ticktick_convert_datetime_to_ticktick_format if needed.
        timeZone (str, optional): IANA timezone name (e.g., 'Asia/Seoul'). Defaults to client's timezone if not specified.
        reminders (List[str], optional): List of reminder triggers in RFC 5545 format.
                                        Common values: ["TRIGGER:PT0S"] (on time), ["TRIGGER:-PT30M"] (30 min before).
        repeat (str, optional): Recurring rule in RFC 5545 format (e.g., "RRULE:FREQ=DAILY;INTERVAL=1").
                               Common values: "RRULE:FREQ=DAILY", "RRULE:FREQ=WEEKLY", "RRULE:FREQ=MONTHLY".
        priority (int, optional): Task priority level. 0=None (default), 1=Low, 3=Medium, 5=High.
        sortOrder (int, optional): Custom sort order value. Lower values appear higher in lists.
        items (List[Dict], optional): List of subtask dictionaries (checklists). Each dict needs at least 'title'.
                                     Note: Due to TickTick API limitations, subtasks can only have startDate, not dueDate.

    Returns:
        A JSON string with one of the following structures:
        - Success: Contains the complete task object with all properties including the newly assigned 'id'
        - Error: {"error": "Error message describing what went wrong"}

    Limitations:
        - Subtasks (items) can only have startDate, not dueDate due to TickTick API constraints
        - Certain fields may be ignored if they conflict with other settings (e.g., allDay=True with specific time)
        - Custom fields created in the TickTick UI cannot be set using this API

    Examples:
        Basic task with title only (goes to Inbox):
        {
            "title": "Buy Groceries"
        }

        Task with due date, priority, and project:
        {
            "title": "Quarterly Report",
            "projectId": "project123abc",
            "priority": 5,
            "dueDate": "2024-08-01"
        }

        Task with content, reminders, and specific time:
        {
            "title": "Team Meeting",
            "content": "Review project timelines and assign tasks",
            "startDate": "2024-07-27T09:00:00+09:00",
            "dueDate": "2024-07-27T10:30:00+09:00",
            "timeZone": "Asia/Seoul",
            "reminders": ["TRIGGER:-PT15M"]
        }

        Task with subtasks:
        {
            "title": "Weekly Shopping",
            "items": [
                {"title": "Milk"},
                {"title": "Bread"},
                {"title": "Eggs"}
            ],
            "dueDate": "2024-07-30"
        }

    Agent Usage Guide:
        - When users ask to "create a task/to-do/reminder", use this tool
        - Map natural language time expressions to ISO 8601 format:
          "tomorrow at 3pm" → calculate the date and use format "YYYY-MM-DDT15:00:00+09:00"
        - For tasks without specific times, set allDay=True
        - When users mention "remind me", populate the reminders field
        - When users list subtasks, create them as items
        - Example mapping:
          "Remind me to submit the report by Friday at 5pm" →
          {
              "title": "Submit the report",
              "dueDate": "2024-07-26T17:00:00+09:00",
              "reminders": ["TRIGGER:PT0S"]
          }
    """
    logging.info(f"Attempting to create task with title: '{title}'")
    try:
        client = TickTickClientSingleton.get_client()
        try:
            start_dt = datetime.datetime.fromisoformat(startDate) if startDate else None
            due_dt = datetime.datetime.fromisoformat(dueDate) if dueDate else None
        except ValueError as e:
             return format_response({"error": f"Invalid date format for startDate or dueDate: {e}. Use ISO format."})

        # Use the builder internally to construct the task dictionary
        task_dict = client.task.builder(
            title=title,
            projectId=projectId,
            content=content, # Use content if provided, else desc
            desc=desc,
            allDay=allDay,
            startDate=start_dt,
            dueDate=due_dt,
            timeZone=timeZone,
            reminders=reminders,
            repeat=repeat,
            priority=priority,
            sortOrder=sortOrder,
            items=items
        )
        created_task = client.task.create(task_dict)
        logging.info(f"Successfully created task: {created_task.get('id')}")
        return format_response(created_task)
    except Exception as e:
        logging.error(f"Failed to create task '{title}': {e}", exc_info=True)
        return format_response({"error": f"Failed to create task: {e}"})

@mcp.tool(name="ticktick_update_task") # Explicitly name tool to avoid conflict if class is renamed
@require_ticktick_client
async def update_task(
    task_object: TaskObject # Use the Pydantic model for validation
) -> str:
    """
    Updates the content of an existing task using its ID.

    Args:
        task_object (TaskObject): A dictionary representing the task to update. Must include the 'id' field.
                      All other fields are optional and represent what you want to change.
                      Date fields (startDate, dueDate) should be in ISO 8601 format.

    Returns:
        A JSON string with one of the following structures:
        - Success: Contains the complete updated task object
        - Error: {"error": "Error message describing what went wrong"}

    Limitations:
        - You can only update top-level task properties. Existing subtasks (items) can be replaced entirely,
          but individual properties of subtasks cannot be selectively modified
        - The task ID must be valid and refer to an existing task
        - The updated task maintains its relationship with parent tasks (if any)
        - Certain fields cannot be modified once set (e.g., created time)
        - Due to TickTick API limitations, subtasks can only have startDate, not dueDate

    Examples:
        Update a task's title and priority:
        {
            "task_object": {
                "id": "task_id_12345",
                "title": "Revised Report Title",
                "priority": 5
            }
        }

        Change the due date, add content, and set reminders:
        {
            "task_object": {
                "id": "task_id_67890",
                "dueDate": "2024-07-27T10:30:00+09:00",
                "content": "Added details about project requirements",
                "reminders": ["TRIGGER:-PT1H", "TRIGGER:-P1D"]
            }
        }

        Replace all subtasks:
        {
            "task_object": {
                "id": "task_id_abcde",
                "items": [
                    {"title": "New subtask 1"},
                    {"title": "New subtask 2"}
                ]
            }
        }

    Agent Usage Guide:
        - When users ask to "update/change/modify/edit a task", use this tool
        - Always include the task_id in the task_object
        - Only include fields that need to be changed
        - Use ticktick_get_by_id first to retrieve the current task if needed
        - Example mapping:
          "Change the due date of my quarterly report task to next Friday" →
          First use ticktick_get_by_id or ticktick_filter_tasks to find the task ID
          Then: {
              "task_object": {
                  "id": "[found task ID]",
                  "startDate": "2024-07-27T09:00:00+09:00",
                  "dueDate": "2024-07-27T10:30:00+09:00"
              }
          }
        - For updating subtasks, you must include the entire items array with all subtasks
    """
    task_id = task_object.id
    logging.info(f"Attempting to update task ID: {task_id}")

    try:
        client = TickTickClientSingleton.get_client()
        task_obj = client.get_by_id(task_id)
        task_obj.update(task_object)

        updated_task = client.task.update(task_object.model_dump(mode='json'))
        logging.info(f"Successfully updated task ID: {task_id}")
        return format_response(updated_task)
    except Exception as e:
        logging.error(f"Failed to update task {task_id}: {e}", exc_info=True)
        return format_response({"error": f"Failed to update task {task_id}: {e}"})

@mcp.tool()
@require_ticktick_client
async def ticktick_delete_tasks(task_ids: Union[str, List[str]]) -> str:
    """
    Deletes one or more tasks using their IDs.

    Args:
        task_ids (Union[str, List[str]]): A single task ID string or a list of task ID strings.
                                         Required. Each ID must be a valid TickTick task ID.

    Returns:
        A JSON string with one of the following structures:
        - Success: {
            "status": "success",
            "deleted_count": number of tasks deleted,
            "tasks_deleted_ids": list of deleted task IDs,
            "api_response": original API response
          }
        - Partial success: Same as success, with additional "warnings" field listing IDs not found
        - Error: {"error": "Error message", "status": "error"}

    Limitations:
        - Deleted tasks cannot be recovered through the API
        - Deleting a parent task will also delete all of its subtasks
        - Task IDs must be valid; invalid IDs will be reported in the warning message
        - Requires the user to have delete permissions for the specified tasks

    Examples:
        Delete a single task:
        {
            "task_ids": "task_id_to_delete_123"
        }

        Delete multiple tasks:
        {
            "task_ids": ["task_id_abc", "task_id_def", "task_id_ghi"]
        }

    Agent Usage Guide:
        - Use this tool when users request to "delete/remove/clear a task or tasks"
        - Always confirm with the user before deleting multiple tasks
        - If some IDs can't be found, explain to the user which tasks couldn't be deleted
        - Example mapping:
          "Delete my grocery shopping task" →
          First find the task ID using ticktick_filter_tasks with appropriate criteria
          Then: {"task_ids": "[found task ID]"}
    """
    tasks_to_delete = []
    ids_to_process = task_ids if isinstance(task_ids, list) else [task_ids]

    # ticktick-py delete expects task *objects*, not just IDs. We need to fetch them first.
    try:
        client = TickTickClientSingleton.get_client()
        missing_ids = []
        invalid_ids = [] # Track IDs that returned an object but wasn't a task
        for tid in ids_to_process:
            # Using the client's generic get_by_id
            obj = client.get_by_id(tid)
            # Check if it looks like a task object (has projectId and title)
            if obj and isinstance(obj, dict) and obj.get('projectId') and obj.get('title') is not None:
                tasks_to_delete.append(obj)
            else:
                if obj is None:
                    missing_ids.append(tid)
                else:
                    # Found something, but it doesn't look like a task
                    invalid_ids.append(tid)
                    logging.warning(f"Object found for ID {tid} but it does not appear to be a valid task object: {obj}")

        warning_message = ""
        if missing_ids:
            logging.warning(f"Could not find tasks with IDs: {missing_ids}")
            warning_message += f"Could not find objects for IDs: {missing_ids}. "
        if invalid_ids:
             logging.warning(f"Found objects for IDs but they were not valid tasks: {invalid_ids}")
             warning_message += f"Found objects for IDs but they were not valid tasks: {invalid_ids}."

        if not tasks_to_delete:
            if not ids_to_process:
                 return format_response({"message": "No task IDs provided.", "status": "error"})
            else:
                 return format_response({
                     "message": "No valid tasks found for the provided ID(s) to delete.",
                     "status": "not_found",
                     "missing_ids": missing_ids,
                     "invalid_ids": invalid_ids
                 })

        input_is_single = isinstance(task_ids, str)
        delete_input = tasks_to_delete[0] if input_is_single else tasks_to_delete

        deleted_result = client.task.delete(delete_input)

        response_data = {
            "status": "success",
            "deleted_count": len(tasks_to_delete),
            "api_response": deleted_result,
            "tasks_deleted_ids": [t['id'] for t in tasks_to_delete]
        }
        if warning_message:
            response_data["warnings"] = warning_message.strip()
        return format_response(response_data)

    except ConnectionError as ce:
        logging.error(f"ConnectionError during task deletion for {task_ids}: {ce}", exc_info=True)
        return format_response({"error": str(ce), "status": "error"})
    except Exception as e:
        logging.error(f"Exception during task deletion for {task_ids}: {e}", exc_info=True)
        return format_response({"error": f"Failed to delete tasks {task_ids}: {e}", "status": "error"})

def _filter_unneeded_properties(tasks: List[Dict]) -> List[Dict]:
    """
    Filters out unneeded properties from each task dictionary in the input list.

    The function performs the following cleanups on each task:
    - Removes integer properties with zero values from a predefined list.
    - Removes certain properties always, regardless of their value.
    - Removes certain properties if they are empty (e.g., empty lists, empty strings).
    - Removes the 'kind' property if its value is the default "TEXT".
    - Removes the 'isAllDay' property if it is False.
    - Removes 'repeatTaskId' if it is equal to the task's own 'id'.

    Args:
        tasks (List[Dict]): A list of task dictionaries to be filtered.

    Returns:
        List[Dict]: A new list of task dictionaries with unneeded properties removed.
    """
    filtered = []

    # Loop over the tasks and remove unneeded information
    for task in tasks:
        # Make a copy to avoid mutating the original task dict
        task = task.copy()

        # Remove zero value integer properties
        zeroValueProps = ["deleted", "imgMode", "priority", "progress", "status"]
        for prop in zeroValueProps:
            if prop in task and task.get(prop, 0) == 0:
                del task[prop]

        # Remove some properties always
        remove_props = [
            "columnId", "commentCount", "completedUserId", "creator", "createdTime", "etag",
            "focusSummaries", "isFloating", "modifiedTime", "pomodoroSummaries", "repeatFirstData",
            "repeatFrom", "sortOrder"
        ]
        for prop in remove_props:
            if prop in task:
                del task[prop]

        # Remove some properties when they are empty
        remove_empty_props = [
            "attachments", "childIds", "desc", "exDate",
            "items", "reminder", "reminders", "repeatFlag", "tags"
        ]
        for prop in remove_empty_props:
            if prop in task and not task[prop]:
                del task[prop]

        # Remove the default 'kind' property if it is the default "TEXT"
        if task.get("kind") == "TEXT":
            del task["kind"]

        # Remove 'isAllDay' if False
        if task.get("isAllDay") is False:
            del task["isAllDay"]

        # Remove 'repeatTaskId' if it equals the task's own 'id'
        if "repeatTaskId" in task and task.get("id") == task.get("repeatTaskId"):
            del task["repeatTaskId"]

        filtered.append(task)

    return filtered

@mcp.tool()
@require_ticktick_client
async def ticktick_get_tasks_from_project(project_id: str) -> str:
    """
    Retrieves a list of all *uncompleted* tasks belonging to a specific project ID.

    Args:
        project_id (str): The ID string of the project. Required.
                         Must be a valid TickTick project ID.

    Returns:
        A JSON string with one of the following structures:
        - Success: A list of task objects (can be empty if project has no tasks)
        - Error: {"error": "Error message describing what went wrong"}

    Limitations:
        - Only returns uncompleted tasks by default (completed tasks are not included)
        - To get completed tasks, use ticktick_filter_tasks with "status": "completed"
        - The project must exist and be accessible to the user
        - Does not return tasks in nested projects (if the project has sub-projects)

    Examples:
        Get all tasks from a work project:
        {
            "project_id": "project_work_456"
        }

    Agent Usage Guide:
        - Use this tool when users ask to "list/show tasks in [project]" or "what tasks are in [project]"
        - First find the project ID using ticktick_get_all("projects") if needed
        - Always specify that only uncompleted tasks are returned
        - Example mapping:
          "Show me my work tasks" →
          First determine work project ID from ticktick_get_all("projects")
          Then: {"project_id": "[found project ID]"}
        - If user wants completed tasks, use ticktick_filter_tasks instead
    """

    try:
        client = TickTickClientSingleton.get_client()
        tasks = client.task.get_from_project(project_id)
        # Ensure result is a list even if API returns None or single dict
        if tasks is None:
             tasks = []
        elif isinstance(tasks, dict):
             tasks = [tasks]
        # Remove unneeded information
        tasks = _filter_unneeded_properties(tasks)
        return format_response(tasks)
    except Exception as e:
        logging.error(f"Failed to get tasks from project {project_id}: {e}", exc_info=True)
        return format_response({"error": f"Failed to get tasks from project {project_id}: {e}"})

@mcp.tool()
@require_ticktick_client
async def ticktick_complete_task(task_id: str) -> str:
    """
    Marks a specific task as complete using its ID.

    Args:
        task_id (str): The ID string of the task to mark as complete. Required.
                      Must be a valid TickTick task ID.

    Returns:
        A JSON string with one of the following structures:
        - Success: The task object with updated status (completed)
        - Not Found: {"error": "Task with ID {task_id} not found or invalid.", "status": "not_found"}
        - Error: {"error": "Error message describing what went wrong"}

    Limitations:
        - Only works for tasks, not projects or tags
        - Completing a parent task will not automatically complete its subtasks
        - Completing recurring tasks will create the next occurrence based on the recurrence rule
        - A completed task may still appear in filter results if "status": "completed" is specified

    Examples:
        Mark a task as complete:
        {
            "task_id": "task_to_complete_789"
        }

    Agent Usage Guide:
        - Use this tool when users say "complete/finish/mark done/check off task X"
        - First find the task ID using ticktick_filter_tasks or other search methods
        - Provide confirmation to the user when task is successfully completed
        - Example mapping:
          "Mark my dentist appointment as done" →
          First find the task ID for the dentist appointment
          Then: {"task_id": "[found task ID]"}
        - If the operation fails with "not_found", inform the user that the task couldn't be found
    """
    try:
        client = TickTickClientSingleton.get_client()

        task_obj = client.get_by_id(task_id)
        if not task_obj or not isinstance(task_obj, dict) or not task_obj.get('projectId'):
            return format_response({"error": f"Task with ID {task_id} not found or invalid.", "status": "not_found"})

        completed_task_result = client.task.complete(task_obj)

        updated_task_obj = client.get_by_id(task_id)
        if updated_task_obj and isinstance(updated_task_obj, dict) and updated_task_obj.get('status', 0) != 0:
             return format_response(updated_task_obj)
        else:
             logging.warning(f"Completed task {task_id}, but refetch failed or status unchanged. Result: {updated_task_obj}")
             return format_response(completed_task_result if completed_task_result else {"warning": "Completion API call succeeded but task status verification failed.", "task_id": task_id})

    except Exception as e:
        logging.error(f"Failed to complete task {task_id}: {e}", exc_info=True)
        return format_response({"error": f"Failed to complete task {task_id}: {e}"})

@mcp.tool()
@require_ticktick_client
async def ticktick_move_task(task_id: str, new_project_id: str) -> str:
    """
    Moves a specific task to a different project.

    Args:
        task_id (str): The ID string of the task to move. Required.
                      Must be a valid TickTick task ID.
        new_project_id (str): The ID string of the destination project. Required.
                             Must be a valid TickTick project ID.

    Returns:
        A JSON string with one of the following structures:
        - Success: The updated task object with the new project ID
        - Not Found (Task): {"error": "Task with ID {task_id} not found or invalid.", "status": "not_found"}
        - Not Found (Project): Warning log if project not found (API may still attempt the move)
        - Error: {"error": "Error message describing what went wrong"}

    Limitations:
        - Moving a task with subtasks will move all subtasks with it
        - Moving a task that is itself a subtask will detach it from its parent
        - The destination project must exist and be accessible to the user
        - Cannot move a task to a project that doesn't exist (will fail)
        - Task properties that depend on project settings might change after move

    Examples:
        Move a task to a different project:
        {
            "task_id": "task_xyz_111",
            "new_project_id": "project_personal_222"
        }

    Agent Usage Guide:
        - Use this tool when users say "move task X to project Y"
        - First find both the task ID and project ID if not already known
        - Example mapping:
          "Move my tax filing task to the Finance project" →
          First find the task ID for "tax filing" and the project ID for "Finance"
          Then: {
              "task_id": "[found task ID]",
              "new_project_id": "[found project ID]"
          }
        - If the project doesn't exist, suggest creating it first
    """
    try:
        client = TickTickClientSingleton.get_client()

        task_obj = client.get_by_id(task_id)
        if not task_obj.get('projectId'):
            return format_response({"error": f"Task with ID {task_id} not found or invalid.", "status": "not_found"})

        # Check if the target project exists? (Optional, API might handle it)
        target_proj = client.get_by_id(new_project_id)
        if not target_proj:
            logging.warning(f"Target project {new_project_id} for moving task {task_id} not found or invalid.")
            # Allow the move attempt anyway, the API might handle this case.
            # return format_response({"error": f"Target project with ID {new_project_id} not found or invalid.", "status": "not_found"})

        moved_task = client.task.move(task_obj, new_project_id)
        # Fetch again to confirm project ID change? API response might be sufficient.
        return format_response(moved_task)
    except Exception as e:
        logging.error(f"Failed to move task {task_id} to project {new_project_id}: {e}", exc_info=True)
        return format_response({"error": f"Failed to move task {task_id} to project {new_project_id}: {e}"})

@mcp.tool()
@require_ticktick_client
async def ticktick_make_subtask(parent_task_id: str, child_task_id: str) -> str:
    """
    Makes one task (child) a subtask of another task (parent).

    Args:
        parent_task_id (str): The ID string of the task that will become the parent. Required.
                             Must be a valid TickTick task ID.
        child_task_id (str): The ID string of the task that will become the subtask. Required.
                            Must be a valid TickTick task ID.

    Returns:
        A JSON string with one of the following structures:
        - Success: {
            "message": "Task {child_id} successfully made a subtask of {parent_id}.",
            "status": "success",
            "updated_parent_task": The updated parent task object with new subtask,
            "api_response": original API response
          }
        - Not Found: {"error": "Child/Parent task with ID {task_id} not found or invalid.", "status": "not_found"}
        - Project Mismatch: {"error": "Tasks must be in the same project to create a subtask relationship."}
        - Error: {"error": "Error message describing what went wrong"}

    Limitations:
        - Both tasks must exist and be in the same project
        - A task cannot be made a subtask of itself
        - Parent and child tasks must be normal tasks (not projects, tags, etc.)
        - A task that is already a subtask of another task will be moved to the new parent
        - Due to TickTick API limitations, subtasks inherit certain properties from parent tasks
          and may not maintain all their original properties
        - Subtasks can have startDate but may not properly maintain dueDate values

    Examples:
        Make "Draft outline" a subtask of "Write report":
        {
            "parent_task_id": "write_report_task_id",
            "child_task_id": "draft_outline_task_id"
        }

    Agent Usage Guide:
        - Use this tool when users say "make task X a subtask of Y" or "add task X under Y"
        - First find both task IDs using appropriate search methods
        - Verify both tasks exist in the same project before attempting the operation
        - Example mapping:
          "Add the 'buy milk' task as a subtask of my 'grocery shopping' task" →
          First find both task IDs
          Then: {
              "parent_task_id": "[grocery shopping task ID]",
              "child_task_id": "[buy milk task ID]"
          }
        - If tasks are in different projects, suggest moving them to the same project first
    """
    if not isinstance(child_task_id, str) or not isinstance(parent_task_id, str):
         return format_response({"error": "Invalid input: child_task_id and parent_task_id must be strings."})

    if child_task_id == parent_task_id:
         return format_response({"error": "Child and parent task IDs cannot be the same."})

    try:
        client = TickTickClientSingleton.get_client()

        child_task_obj = client.get_by_id(child_task_id)
        if not child_task_obj or not isinstance(child_task_obj, dict) or not child_task_obj.get('projectId'):
            return format_response({"error": f"Child task with ID {child_task_id} not found or invalid.", "status": "not_found"})

        parent_task_obj = client.get_by_id(parent_task_id)
        if not parent_task_obj or not isinstance(parent_task_obj, dict) or not parent_task_obj.get('projectId'):
            return format_response({"error": f"Parent task with ID {parent_task_id} not found or invalid.", "status": "not_found"})

        # Constraint check: Ensure tasks are in the same project
        if child_task_obj.get('projectId') != parent_task_obj.get('projectId'):
            return format_response({
                "error": "Tasks must be in the same project to create a subtask relationship.",
                "child_project": child_task_obj.get('projectId'),
                "parent_project": parent_task_obj.get('projectId')
            })

        # The API call uses the child object and the parent ID string
        result_subtask = client.task.make_subtask(child_task_obj, parent_task_id)

        # Fetch parent task again to show updated subtasks/structure in the response
        updated_parent_task_obj = client.get_by_id(parent_task_id)

        return format_response({
             "message": f"Task {child_task_id} successfully made a subtask of {parent_task_id}.",
             "status": "success",
             "updated_parent_task": updated_parent_task_obj,
             "api_response": result_subtask # Include raw API response if needed
        })
    except Exception as e:
        logging.error(f"Failed to make task {child_task_id} a subtask of {parent_task_id}: {e}", exc_info=True)
        return format_response({"error": f"Failed to make task {child_task_id} a subtask of {parent_task_id}: {e}"})

@mcp.tool()
@require_ticktick_client
async def ticktick_get_by_id(obj_id: str) -> str:
    """
    Retrieves a single TickTick object (task, project, tag, etc.) using its unique ID.

    Args:
        obj_id (str): The unique ID string of the object to retrieve. Required.
                     Can be a task ID, project ID, tag ID, or any other valid TickTick object ID.

    Returns:
        A JSON string with one of the following structures:
        - Success: The complete object with all its properties
        - Not Found: null or empty response
        - Error: {"error": "Error message describing what went wrong"}

    Limitations:
        - Can only retrieve objects the user has access to
        - Different object types (tasks, projects, tags) have different property structures
        - Does not provide information about related objects (e.g., a project's tasks)
        - The ID must be valid and refer to an existing object

    Examples:
        Get a task by ID:
        {
            "obj_id": "task_id_12345"
        }

        Get a project by ID:
        {
            "obj_id": "project_id_67890"
        }

    Agent Usage Guide:
        - Use this tool when you need specific details about a known object
        - Helpful for getting full details before updating an object
        - For tasks, this retrieves a single task including any subtasks
        - For projects, this retrieves the project details (name, color) but not its tasks
        - Example mapping:
          "Get details of my quarterly report task" →
          First find the task ID using ticktick_filter_tasks
          Then: {"obj_id": "[found task ID]"}
        - If the object is not found, explain to the user it might not exist or they might not have access
    """
    try:
        client = TickTickClientSingleton.get_client()
        obj = client.get_by_id(obj_id)
        return format_response(obj)
    except Exception as e:
        logging.error(f"Failed to get object with ID {obj_id}: {e}", exc_info=True)
        return format_response({"error": f"Failed to get object with ID {obj_id}: {e}"})

@mcp.tool()
@require_ticktick_client
async def ticktick_get_all(search: str) -> str:
    """
    Retrieves a list of all TickTick objects of a specified type.

    Args:
        search (str): The type of objects to retrieve. Required.
                     Common values: "tasks", "projects", "tags", "habits", "filters"
                     Case insensitive but should match one of the supported types.

    Returns:
        A JSON string with one of the following structures:
        - Success: A list of objects of the requested type (may be empty)
        - Error: {"error": "Error message describing what went wrong"}

    Limitations:
        - For "tasks", only uncompleted tasks are returned by default
        - For large accounts, response size might be very large 
        - Different object types have different property structures
        - Some object types might not be available depending on the user's subscription level
        - The API might limit the number of results for performance reasons

    Examples:
        Get all projects:
        {
            "search": "projects"
        }

        Get all tags:
        {
            "search": "tags"
        }

    Agent Usage Guide:
        - Use this tool to get a comprehensive list of a specific object type
        - Particularly useful for discovering available projects, tags
        - For tasks, consider using ticktick_filter_tasks for more targeted results
        - Common search terms mapping:
          "projects" → list all projects
          "tasks" → list all uncompleted tasks
          "tags" → list all tags
        - Example mapping:
          "Show me all my projects" → {"search": "projects"}
          "List all my tags" → {"search": "tags"}
    """
    try:
        client = TickTickClientSingleton.get_client()
        if not client:
            raise ToolLogicError("TickTick client is not available.")
        
        # Get all tasks initially treats search as case-sensitive
        search_lower = search.lower()
        client.sync()
        if search_lower == "tasks":
            all_items = _get_all_tasks_from_ticktick()
            # Remove unneeded information
            all_items = _filter_unneeded_properties(all_items)
            return format_response(all_items)
        elif search_lower == "projects":
            projects = [ { "id": client.inbox_id, "name": "Inbox" } ] + client.state['projects']
            return format_response(projects)
        elif search_lower == "tags":
            all_items = client.state['tags']
            return format_response(all_items)
        else:
            return format_response({"error": f"Invalid search type: {search}"})
    except Exception as e:
        logging.error(f"Failed to get all items of type {search}: {e}", exc_info=True)
        return format_response({"error": f"Failed to get all items of type {search}: {e}"})
