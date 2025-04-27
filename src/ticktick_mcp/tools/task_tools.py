import datetime
import logging
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field

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
    title: str
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

    class Config:
        # Allow population by field name OR alias
        populate_by_name = True
        # Allow arbitrary types if needed for complex nested structures from API
        # arbitrary_types_allowed = True
        pass

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
        title (str): The title of the task.
        projectId (str, optional): ID of the project to add the task to. Defaults to Inbox.
        content (str, optional): Additional details or notes for the task.
        desc (str, optional): Description for checklist items (if any).
        allDay (bool, optional): Set to True if the task spans the entire day.
        startDate (str, optional): Start date/time in ISO 8601 format (e.g., '2024-07-26T10:00:00+09:00' or '2024-07-26').
        dueDate (str, optional): Due date/time in ISO 8601 format.
        timeZone (str, optional): IANA timezone name (e.g., 'Asia/Seoul'). Defaults to client's timezone.
        reminders (List[str], optional): List of reminder triggers (e.g., ["TRIGGER:PT0S"] for on-time).
        repeat (str, optional): Recurring rule (e.g., "RRULE:FREQ=DAILY;INTERVAL=1").
        priority (int, optional): Task priority (0=None, 1=Low, 3=Medium, 5=High).
        sortOrder (int, optional): Custom sort order value.
        items (List[Dict], optional): List of subtask dictionaries (checklists). Each dict needs at least 'title'.

    Returns:
        A JSON string containing the newly created task object or an error message.

    Example:
        Creating a simple task:
        {
            "title": "Buy Groceries",
            "priority": 3,
            "dueDate": "2024-08-01"
        }

        Creating a task with content and a reminder:
        {
            "title": "Team Meeting Prep",
            "projectId": "project123abc",
            "content": "Review agenda and prepare slides.",
            "startDate": "2024-07-27T09:00:00+09:00",
            "dueDate": "2024-07-27T10:00:00+09:00",
            "timeZone": "Asia/Seoul",
            "reminders": ["TRIGGER:PT0S"]
        }
    """
    logging.info(f"Attempting to create task with title: '{title}'")
    try:
        client = TickTickClientSingleton.get_client()
        if not client:
            raise ToolLogicError("TickTick client is not available.")

        # Convert date strings to datetime objects if provided
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
        task_object: A dictionary representing the task to update. Must include the 'id' field.
                     Other fields to update should also be included.
                     Date fields (startDate, dueDate) should be in ISO 8601 format.

    Returns:
        A JSON string containing the updated task object or an error message.

    Example:
        Updating a task's title and priority:
        {
            "task_object": {
                "id": "task_id_12345",
                "title": "Buy Groceries (Updated)",
                "priority": 5
            }
        }

        Changing the due date and adding content:
        {
            "task_object": {
                "id": "task_id_67890",
                "dueDate": "2024-08-05",
                "content": "Milk, Eggs, Bread, Apples"
            }
        }
    """
    if not isinstance(task_object, dict) or 'id' not in task_object:
         return format_response({"error": "Invalid input: task_object must be a dictionary with an 'id'."})

    task_id = task_object.get('id')
    logging.info(f"Attempting to update task ID: {task_id}")

    try:
        client = TickTickClientSingleton.get_client()
        if not client:
            raise ToolLogicError("TickTick client is not available.")

        # The library expects the full task dictionary for update
        updated_task = client.task.update(task_object)
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
        task_ids: A single task ID string or a list of task ID strings.

    Returns:
        A JSON string indicating success or failure, potentially listing deleted IDs or errors.

    Example:
        Deleting a single task:
        {
            "task_ids": "task_id_to_delete_123"
        }

        Deleting multiple tasks:
        {
            "task_ids": ["task_id_abc", "task_id_def", "task_id_ghi"]
        }
    """
    tasks_to_delete = []
    ids_to_process = task_ids if isinstance(task_ids, list) else [task_ids]

    if not all(isinstance(tid, str) for tid in ids_to_process):
         return format_response({"error": "Invalid input: task_ids must be a string or a list of strings.", "status": "error"})

    # ticktick-py delete expects task *objects*, not just IDs. We need to fetch them first.
    try:
        tasks_to_delete = []
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

@mcp.tool()
@require_ticktick_client
async def ticktick_get_tasks_from_project(project_id: str) -> str:
    """
    Retrieves a list of all *uncompleted* tasks belonging to a specific project ID.

    Args:
        project_id: The ID string of the project.

    Returns:
        A JSON string containing a list of uncompleted task objects in that project, or an error message.

    Example:
        {
            "project_id": "project_work_456"
        }
    """
    if not isinstance(project_id, str):
         return format_response({"error": "Invalid input: project_id must be a string."})

    try:
        client = TickTickClientSingleton.get_client()
        if not client:
            raise ToolLogicError("TickTick client is not available.")
        tasks = client.task.get_from_project(project_id)
        # Ensure result is a list even if API returns None or single dict
        if tasks is None:
             tasks = []
        elif isinstance(tasks, dict):
             tasks = [tasks]
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
        task_id: The ID string of the task to mark as complete.

    Returns:
        A JSON string containing the completed task object or an error message.

    Example:
        {
            "task_id": "task_to_complete_789"
        }
    """
    if not isinstance(task_id, str):
         return format_response({"error": "Invalid input: task_id must be a string."})

    try:
        client = TickTickClientSingleton.get_client()
        if not client:
            raise ToolLogicError("TickTick client is not available.")
        # Need to fetch the task object first
        task_obj = client.get_by_id(task_id)
        if not task_obj or not isinstance(task_obj, dict) or not task_obj.get('projectId'):
            return format_response({"error": f"Task with ID {task_id} not found or invalid.", "status": "not_found"})

        completed_task_result = client.task.complete(task_obj)
        # The method might return the original task or result object.
        # Fetch again to confirm status change and return the updated object.
        updated_task_obj = client.get_by_id(task_id)
        if updated_task_obj and isinstance(updated_task_obj, dict) and updated_task_obj.get('status', 0) != 0:
             return format_response(updated_task_obj)
        else:
             # If refetch fails or status didn't change, return original result/error
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
        task_id: The ID string of the task to move.
        new_project_id: The ID string of the destination project.

    Returns:
        A JSON string indicating success or failure, potentially containing the moved task object.

    Example:
        {
            "task_id": "task_xyz_111",
            "new_project_id": "project_personal_222"
        }
    """
    if not isinstance(task_id, str) or not isinstance(new_project_id, str):
         return format_response({"error": "Invalid input: task_id and new_project_id must be strings."})

    try:
        client = TickTickClientSingleton.get_client()
        if not client:
            raise ToolLogicError("TickTick client is not available.")
        # Need to fetch the task object first
        task_obj = client.get_by_id(task_id)
        if not task_obj or not isinstance(task_obj, dict) or not task_obj.get('projectId'):
            return format_response({"error": f"Task with ID {task_id} not found or invalid.", "status": "not_found"})

        # Check if the target project exists? (Optional, API might handle it)
        target_proj = client.get_by_id(new_project_id)
        if not target_proj or not isinstance(target_proj, dict) or target_proj.get('id') != new_project_id:
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
        parent_task_id: The ID string of the task that will become the parent.
        child_task_id: The ID string of the task that will become the subtask.

    Returns:
        A JSON string indicating success or failure.

    Example:
        {
            "parent_task_id": "parent_task_id_main",
            "child_task_id": "child_task_id_sub1"
        }
    """
    if not isinstance(child_task_id, str) or not isinstance(parent_task_id, str):
         return format_response({"error": "Invalid input: child_task_id and parent_task_id must be strings."})

    if child_task_id == parent_task_id:
         return format_response({"error": "Child and parent task IDs cannot be the same."})

    try:
        client = TickTickClientSingleton.get_client()
        if not client:
            raise ToolLogicError("TickTick client is not available.")
        # Need to fetch both task objects
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