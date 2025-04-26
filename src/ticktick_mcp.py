#!/usr/bin/env python3

import asyncio
import datetime
import json
import os
import sys
import argparse
from typing import Any, Dict, List, Optional, Union
from pathlib import Path
from dotenv import load_dotenv

# Third-party libraries
import httpx
from mcp.server.fastmcp import FastMCP

# MCP library
from mcp.server.fastmcp import FastMCP

# TickTick library
from ticktick.api import TickTickClient
from ticktick.oauth2 import OAuth2
from ticktick.helpers.time_methods import (
    convert_date_to_tick_tick_format,
    convert_local_time_to_utc,
)
from ticktick.helpers.hex_color import check_hex_color, generate_hex_color

# --- Configuration --- (Argument Parsing and Directory/File Handling)

# Setup argument parser
parser = argparse.ArgumentParser(description="Run the TickTick MCP server, specifying the directory for the .env file.")
parser.add_argument(
    "--dotenv-dir",
    type=str,
    help="Path to the directory containing the .env file. Defaults to '~/.config/ticktick-mcp'.",
    default="~/.config/ticktick-mcp" # Default value set
)

# Parse arguments
args = parser.parse_args()

# Determine the target directory for the .env file
dotenv_dir_path = Path(args.dotenv_dir).expanduser() # Expand ~ to home directory

# Create the directory if it doesn't exist
try:
    dotenv_dir_path.mkdir(parents=True, exist_ok=True)
    print(f"Ensured directory exists: {dotenv_dir_path}", file=sys.stderr)
except OSError as e:
    print(f"Error creating directory {dotenv_dir_path}: {e}", file=sys.stderr)
    sys.exit(1)

# Construct the full path to the .env file
dotenv_path = dotenv_dir_path / ".env"

# Check if the .env file exists in the target directory
if not dotenv_path.is_file():
    print(f"Error: Required .env file not found at {dotenv_path}", file=sys.stderr)
    print("Please create the .env file with your TickTick credentials.", file=sys.stderr)
    print("Expected content:", file=sys.stderr)
    print("  TICKTICK_CLIENT_ID=your_client_id", file=sys.stderr)
    print("  TICKTICK_CLIENT_SECRET=your_client_secret", file=sys.stderr)
    print("  TICKTICK_REDIRECT_URI=your_redirect_uri", file=sys.stderr)
    print("  TICKTICK_USERNAME=your_ticktick_email", file=sys.stderr)
    print("  TICKTICK_PASSWORD=your_ticktick_password", file=sys.stderr)
    sys.exit(1) # Exit if .env file is missing

# Load the required .env file
loaded = load_dotenv(override=True, dotenv_path=dotenv_path)
if loaded:
    print(f"Successfully loaded environment variables from: {dotenv_path}", file=sys.stderr)
else:
    # This case might indicate an issue reading the file even if it exists
    print(f"Error: Failed to load environment variables from {dotenv_path}. Check file permissions and format.", file=sys.stderr)
    sys.exit(1)

# --- Environment Variable Loading (after dotenv is loaded) ---
# These will now reflect the loaded .env file
CLIENT_ID = os.getenv("TICKTICK_CLIENT_ID")
CLIENT_SECRET = os.getenv("TICKTICK_CLIENT_SECRET")
REDIRECT_URI = os.getenv("TICKTICK_REDIRECT_URI")
USERNAME = os.getenv("TICKTICK_USERNAME")
PASSWORD = os.getenv("TICKTICK_PASSWORD")

# --- Argument Type Hinting ---
# Define type hints for clarity, especially for list inputs
TaskId = str
ProjectId = str
TagLabel = str
FolderName = str
FolderId = str
TaskObject = Dict[str, Any]
ProjectObject = Dict[str, Any]
TagObject = Dict[str, Any]
FolderObject = Dict[str, Any]
ListOfTaskIds = List[TaskId]
ListOfProjectIds = List[ProjectId]
ListOfTagLabels = List[TagLabel]
ListOfFolderIds = List[FolderId]
ListOfTaskObjects = List[TaskObject]
ListOfProjectObjects = List[ProjectObject]
ListOfTagObjects = List[TagObject]
ListOfFolderObjects = List[FolderObject]
ListOfStrings = List[str]

# --- TickTick Client Initialization ---

# IMPORTANT OAUTH NOTE:
# The first time you run this server OR if your token expires (approx. 6 months),
# ticktick-py's OAuth2 process requires manual browser authorization.
# It will print a URL to the console, open it in a browser, authorize the app,
# and then you'll be redirected to your REDIRECT_URI with a '?code=...' parameter.
# You MUST copy this full redirected URL and paste it back into the console where
# the server is running when prompted.
#
# For an MCP server running non-interactively (e.g., via Claude Desktop), this is
# problematic. The recommended approach is to run this script *manually* in a
# terminal *once* to perform the initial authorization. This will create a
# '.token-oauth' file caching the token. Subsequent runs (including non-interactive
# ones by an MCP host) will use the cached token until it expires.
# Ensure the .token-oauth file is in the correct working directory when run by the host.

ticktick_client: Optional[TickTickClient] = None

def initialize_ticktick_client():
    """Initializes the global TickTick client."""
    global ticktick_client
    if not all([CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, USERNAME, PASSWORD]):
        print(
            "Error: TickTick credentials not found in environment variables.",
            file=sys.stderr,
        )
        print(
            "Please create a .env file with TICKTICK_CLIENT_ID, TICKTICK_CLIENT_SECRET, "
            "TICKTICK_REDIRECT_URI, TICKTICK_USERNAME, and TICKTICK_PASSWORD.",
            file=sys.stderr,
        )
        # In a real scenario, you might want to exit or handle this differently
        # For now, we'll proceed, but API calls will fail.
        return None

    try:
        auth_client = OAuth2(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            cache_path=dotenv_dir_path / ".token-oauth"
        )
        auth_client.get_access_token()
        # This might trigger the interactive OAuth flow if token is missing/expired
        ticktick_client = TickTickClient(USERNAME, PASSWORD, auth_client)
        print("TickTick client initialized successfully.", file=sys.stderr)
        return ticktick_client
    except Exception as e:
        print(f"Error initializing TickTick client: {e}", file=sys.stderr)
        ticktick_client = None # Ensure client is None if init fails
        return None

# --- MCP Server Setup ---
mcp = FastMCP("ticktick-server")

# --- Helper Function ---
def format_response(result: Any) -> str:
    """Formats the result from ticktick-py into a JSON string for MCP."""
    if isinstance(result, (dict, list)):
        try:
            # Use default=str to handle potential datetime objects if any slip through
            return json.dumps(result, indent=2, default=str)
        except TypeError as e:
            return json.dumps({"error": "Failed to serialize response", "details": str(e)})
    elif result is None:
         return json.dumps(None)
    else:
        # Fallback for unexpected types
        return json.dumps({"result": str(result)})

# --- Internal Helper to Get All Tasks ---
def _get_all_tasks_from_ticktick() -> List[TaskObject]:
    """Internal helper to fetch all tasks from all projects."""
    if not ticktick_client:
        raise ConnectionError("TickTick client not initialized.")

    all_tasks = []
    # Access projects directly from the client's state
    projects_state = ticktick_client.state.get('projects', [])
    if projects_state is None: projects_state = [] # Handle None case

    # Ensure projects_state is a list
    if not isinstance(projects_state, list):
        print(f"Warning: Expected list of projects in state, got {type(projects_state)}. Fetching inbox tasks only.", file=sys.stderr)
        projects_state = [] # Reset to empty list if not a list

    # Add inbox explicitly if not already covered or if projects fetch failed
    project_ids = {p.get('id') for p in projects_state if isinstance(p, dict) and p.get('id')}
    if ticktick_client.inbox_id not in project_ids:
        project_ids.add(ticktick_client.inbox_id)


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
        except Exception as e:
            print(f"Warning: Failed to get tasks for project {project_id}: {e}", file=sys.stderr)
            # Continue to next project even if one fails

    # Note: This currently only gets *uncompleted* tasks because get_from_project does.
    # To get *all* tasks (including completed), the logic would need to be more complex,
    # potentially involving get_completed for each project over a wide date range,
    # which is inefficient and not directly supported by ticktick-py's structure.
    # For now, this function returns all *uncompleted* tasks across all projects.
    return all_tasks


# --- TickTick Tools Implementation ---

# ==================
# Task Tools
# ==================

@mcp.tool()
async def ticktick_create_task(
    title: str,
    projectId: Optional[ProjectId] = None,
    content: Optional[str] = None,
    desc: Optional[str] = None,
    allDay: Optional[bool] = None,
    startDate: Optional[str] = None, # Expect ISO format string e.g., "2025-04-26T10:00:00"
    dueDate: Optional[str] = None,   # Expect ISO format string e.g., "2025-04-27T18:30:00"
    timeZone: Optional[str] = None,
    reminders: Optional[List[str]] = None, # e.g., ["TRIGGER:PT0S"]
    repeat: Optional[str] = None, # e.g., "RRULE:FREQ=DAILY;INTERVAL=1"
    priority: Optional[int] = None, # 0, 1, 3, 5
    sortOrder: Optional[int] = None,
    items: Optional[List[Dict[str, Any]]] = None # Subtasks [{ "title": "subtask1", "status": 0}]
) -> str:
    """
    Creates a new task in TickTick.

    Agent Usage Guide:
    - Required argument: 'title' (Task title).
    - Optional arguments:
        - 'projectId': ID of the project to add the task to. If not specified, adds to the default inbox.
        - 'content'/'desc': Content or description of the task.
        - 'startDate'/'dueDate': Start/due date and time. Must be provided as **ISO 8601 format strings** (e.g., '2025-04-26T10:00:00').
        - 'allDay': If set to True, uses only the date without time information.
        - 'timeZone': Timezone for the date/time (e.g., 'Asia/Seoul', 'UTC').
        - 'reminders': List of reminder setting strings (follows TickTick format).
        - 'repeat': Repetition setting string (follows TickTick format, e.g., 'RRULE:FREQ=DAILY;INTERVAL=1').
        - 'priority': Priority level (0: None, 1: Low, 3: Medium, 5: High).
        - 'items': List of subtasks. Each subtask is a dictionary like {'title': '...', 'status': 0}.
    - Returns: On success, a JSON string of the created task object (TaskObject). On failure, a JSON string containing error information.
    """
    if not ticktick_client:
        return format_response({"error": "TickTick client not initialized."})

    # Convert date strings to datetime objects if provided
    start_dt = datetime.datetime.fromisoformat(startDate) if startDate else None
    due_dt = datetime.datetime.fromisoformat(dueDate) if dueDate else None

    try:
        # Use the builder internally to construct the task dictionary
        task_dict = ticktick_client.task.builder(
            title=title,
            projectId=projectId,
            content=content,
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
        created_task = ticktick_client.task.create(task_dict)
        return format_response(created_task)
    except Exception as e:
        return format_response({"error": f"Failed to create task: {e}"})

@mcp.tool()
async def ticktick_update_task(task_object: TaskObject) -> str:
    """
    Updates the content of an existing task.

    Agent Usage Guide:
    - Required argument: 'task_object' - The **entire task object dictionary containing the fields to update**. Must include the 'id' field.
    - How to use:
        1. First, retrieve the **entire object** of the task you want to update using `ticktick_get_by_id` or `ticktick_get_by_fields`.
        2. Modify the values of the fields you want to change in the retrieved object.
        3. Pass the modified **entire object** as the 'task_object' argument to this function.
    - Important notes:
        - Date fields ('startDate', 'dueDate', 'completedTime') must be strings in the specific format required by the TickTick API (e.g., '2025-04-26T10:00:00.000+0000').
        - If necessary, you can use the `ticktick_convert_datetime_to_ticktick_format` helper tool to convert ISO 8601 strings to the TickTick format.
    - Returns: On success, a JSON string of the updated task object (TaskObject). On failure, a JSON string containing error information.
    """
    if not ticktick_client:
        return format_response({"error": "TickTick client not initialized."})
    if not isinstance(task_object, dict) or 'id' not in task_object:
         return format_response({"error": "Invalid input: task_object must be a dictionary with an 'id'."})

    try:
        updated_task = ticktick_client.task.update(task_object)
        return format_response(updated_task)
    except Exception as e:
        return format_response({"error": f"Failed to update task {task_object.get('id')}: {e}"})

@mcp.tool()
async def ticktick_delete_tasks(task_ids: Union[TaskId, ListOfTaskIds]) -> str:
    """
    Deletes one or more tasks using their IDs.

    Agent Usage Guide:
    - Required argument: 'task_ids' - The ID (string) or list of IDs (list of strings) of the task(s) to delete.
    - How it works: Internally finds the actual task objects using the IDs and then attempts deletion.
    - Returns: On success, a JSON string of the deletion result (based on TickTick API response). If IDs are not found or deletion fails, returns a JSON string containing error/message information.
    - Caution: Deleted tasks might be difficult to recover.
    """
    if not ticktick_client:
        return format_response({"error": "TickTick client not initialized."})

    tasks_to_delete = []
    ids_to_process = task_ids if isinstance(task_ids, list) else [task_ids]

    if not all(isinstance(tid, str) for tid in ids_to_process):
         return format_response({"error": "Invalid input: task_ids must be a string or a list of strings."})

    # ticktick-py delete expects task *objects*, not just IDs. We need to fetch them first.
    try:
        # Use the internal helper to get all tasks efficiently
        all_tasks_data = _get_all_tasks_from_ticktick() # This gets uncompleted tasks

        # Additionally fetch completed tasks if needed? No, delete usually targets known IDs.
        # If an ID is for a completed task, get_by_id should find it.
        # Let's fetch by ID directly instead of relying on _get_all_tasks_from_ticktick

        tasks_to_delete = []
        missing_ids = []
        for tid in ids_to_process:
            task_obj = ticktick_client.get_by_id(tid)
            if task_obj and isinstance(task_obj, dict):
                tasks_to_delete.append(task_obj)
            else:
                missing_ids.append(tid)

        if missing_ids:
            print(f"Warning: Could not find tasks with IDs: {missing_ids}", file=sys.stderr)

        if not tasks_to_delete:
            return format_response({"message": "No matching tasks found to delete."})

        # Determine if single or multiple deletion based on original input
        input_is_single = isinstance(task_ids, str)

        # Adjust delete_input logic
        if input_is_single:
             delete_input = tasks_to_delete[0] if len(tasks_to_delete) == 1 else None
        else:
             delete_input = tasks_to_delete

        if not delete_input:
             return format_response({"message": "No tasks to delete after filtering/lookup."})

        deleted_result = ticktick_client.task.delete(delete_input)
        return format_response(deleted_result)
    except ConnectionError as ce:
        return format_response({"error": str(ce)})
    except Exception as e:
        return format_response({"error": f"Failed to delete tasks {task_ids}: {e}"})


@mcp.tool()
async def ticktick_get_completed_tasks(
    start_date: str, # ISO format string
    end_date: Optional[str] = None, # ISO format string
    include_time: bool = False, # Corresponds to 'full=False' in ticktick-py
    tz: Optional[str] = None
) -> str:
    """
    Retrieves a list of tasks completed within a specified date range.

    Agent Usage Guide:
    - Required argument: 'start_date' - The start date for the query. Must be an **ISO 8601 format string** (e.g., '2025-04-26' or '2025-04-26T10:00:00').
    - Optional arguments:
        - 'end_date': The end date for the query. **ISO 8601 format string**. If not specified, queries only for the 'start_date'.
        - 'include_time': If True, considers time as well as date for the range (corresponds to 'full=False' in ticktick-py). If False (default), ignores time and compares dates only.
        - 'tz': Timezone to use for date comparison (e.g., 'Asia/Seoul', 'UTC').
    - Returns: On success, a JSON string of the list of completed task objects (TaskObject). On failure, a JSON string containing error information. Returns an error if the date format is invalid.
    """
    if not ticktick_client:
        return format_response({"error": "TickTick client not initialized."})

    try:
        start_dt = datetime.datetime.fromisoformat(start_date)
        end_dt = datetime.datetime.fromisoformat(end_date) if end_date else None

        # ticktick-py uses 'full=True' to ignore time, 'full=False' to include time.
        # So we invert the boolean logic here.
        full_param = not include_time

        completed_tasks = ticktick_client.task.get_completed(start=start_dt, end=end_dt, full=full_param, tz=tz)
        return format_response(completed_tasks)
    except ValueError as e:
         return format_response({"error": f"Invalid date format: {e}. Use ISO format (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)." })
    except Exception as e:
        return format_response({"error": f"Failed to get completed tasks: {e}"})

@mcp.tool()
async def ticktick_get_tasks_from_project(project_id: ProjectId) -> str:
    """
    Retrieves a list of all *uncompleted* tasks belonging to a specific project ID.

    Agent Usage Guide:
    - Required argument: 'project_id' - The ID (string) of the project from which to fetch tasks.
    - How it works: Returns only the uncompleted tasks within the specified project. Completed tasks are not included.
    - Returns: On success, a JSON string of the list of uncompleted task objects (TaskObject) for the project. If the project ID is invalid or fetching fails, returns a JSON string with error information. Returns an empty list '[]' if the list is empty.
    """
    if not ticktick_client:
        return format_response({"error": "TickTick client not initialized."})
    if not isinstance(project_id, str):
         return format_response({"error": "Invalid input: project_id must be a string."})

    try:
        tasks = ticktick_client.task.get_from_project(project_id)
        return format_response(tasks)
    except Exception as e:
        return format_response({"error": f"Failed to get tasks from project {project_id}: {e}"})

@mcp.tool()
async def ticktick_complete_task(task_id: TaskId) -> str:
    """
    Marks a specific task as complete using its ID.

    Agent Usage Guide:
    - Required argument: 'task_id' - The ID (string) of the task to mark as complete.
    - How it works: Finds the task object using the ID and then marks it as complete.
    - Returns: On success, a JSON string of the updated task object (TaskObject). If the task is not found or completion fails, returns a JSON string with error information.
    """
    if not ticktick_client:
        return format_response({"error": "TickTick client not initialized."})
    if not isinstance(task_id, str):
         return format_response({"error": "Invalid input: task_id must be a string."})

    try:
        # Need to fetch the task object first
        task_obj = ticktick_client.get_by_id(task_id)
        if not task_obj:
            return format_response({"error": f"Task with ID {task_id} not found."})
        if not isinstance(task_obj, dict): # Add type check after fetch
             return format_response({"error": f"Fetched object for ID {task_id} is not a valid task dictionary."})


        completed_task = ticktick_client.task.complete(task_obj)
        # The method returns the original task, let's fetch again to confirm status change
        updated_task_obj = ticktick_client.get_by_id(task_id)
        return format_response(updated_task_obj or completed_task) # Return updated if possible
    except Exception as e:
        return format_response({"error": f"Failed to complete task {task_id}: {e}"})

@mcp.tool()
async def ticktick_move_task(task_id: TaskId, new_project_id: ProjectId) -> str:
    """
    Moves a specific task to a different project.

    Agent Usage Guide:
    - Required arguments:
        - 'task_id': The ID (string) of the task to move.
        - 'new_project_id': The ID (string) of the target project to move the task to.
    - How it works: Finds the task by 'task_id' and moves it to the 'new_project_id' project.
    - Returns: On success, a JSON string of the moved task object (TaskObject). If the task or project is not found or moving fails, returns a JSON string with error information.
    """
    if not ticktick_client:
        return format_response({"error": "TickTick client not initialized."})
    if not isinstance(task_id, str) or not isinstance(new_project_id, str):
         return format_response({"error": "Invalid input: task_id and new_project_id must be strings."})

    try:
        # Need to fetch the task object first
        task_obj = ticktick_client.get_by_id(task_id)
        if not task_obj:
            return format_response({"error": f"Task with ID {task_id} not found."})
        if not isinstance(task_obj, dict): # Add type check after fetch
             return format_response({"error": f"Fetched object for ID {task_id} is not a valid task dictionary."})

        moved_task = ticktick_client.task.move(task_obj, new_project_id)
        return format_response(moved_task)
    except Exception as e:
        return format_response({"error": f"Failed to move task {task_id} to project {new_project_id}: {e}"})

@mcp.tool()
async def ticktick_make_subtask(child_task_id: TaskId, parent_task_id: TaskId) -> str:
    """
    Makes one task (child) a subtask of another task (parent). Both tasks must belong to the same project.

    Agent Usage Guide:
    - Required arguments:
        - 'child_task_id': The ID (string) of the existing task that will become the subtask.
        - 'parent_task_id': The ID (string) of the existing task that will become the parent task.
    - Constraint: Both tasks must be in the same project.
    - Returns: On success, a JSON string containing the *parent* task object (TaskObject) with the added subtask and a success message. If tasks are not found, are in different projects, or the operation fails, returns a JSON string with error information.
    """
    if not ticktick_client:
        return format_response({"error": "TickTick client not initialized."})
    if not isinstance(child_task_id, str) or not isinstance(parent_task_id, str):
         return format_response({"error": "Invalid input: child_task_id and parent_task_id must be strings."})

    try:
        # Need to fetch the child task object
        child_task_obj = ticktick_client.get_by_id(child_task_id)
        if not child_task_obj:
            return format_response({"error": f"Child task with ID {child_task_id} not found."})
        if not isinstance(child_task_obj, dict): # Add type check after fetch
             return format_response({"error": f"Fetched object for ID {child_task_id} is not a valid task dictionary."})


        # Parent ID is passed directly
        result_subtask = ticktick_client.task.make_subtask(child_task_obj, parent_task_id)
        # Fetch parent task to show updated subtasks
        parent_task_obj = ticktick_client.get_by_id(parent_task_id)
        return format_response({
             "message": f"Task {child_task_id} successfully made a subtask of {parent_task_id}.",
             "updated_parent_task": parent_task_obj
        })
    except Exception as e:
        return format_response({"error": f"Failed to make task {child_task_id} a subtask of {parent_task_id}: {e}"})

# ==================
# Project Tools
# ==================

# @mcp.tool()
# async def ticktick_create_project(
#     name: str,
#     color: Optional[str] = 'random', # Hex string or 'random'
#     project_type: Optional[str] = 'TASK', # 'TASK' or 'NOTE'
#     folder_id: Optional[FolderId] = None
# ) -> str:
#     """Creates a new project (list) in TickTick."""
#     if not ticktick_client:
#         return format_response({"error": "TickTick client not initialized."})
#     if not isinstance(name, str):
#          return format_response({"error": "Invalid input: name must be a string."})
# 
#     try:
#         # Use builder internally
#         project_obj = ticktick_client.project.builder(
#             name=name,
#             color=color,
#             project_type=project_type,
#             folder_id=folder_id
#         )
#         # Pass the *list* containing the single built object to create
#         created_project = ticktick_client.project.create([project_obj])
#         # Create returns a list, extract the single element
#         return format_response(created_project[0] if created_project else None)
#     except Exception as e:
#         return format_response({"error": f"Failed to create project '{name}': {e}"})
# 
# @mcp.tool()
# async def ticktick_update_project(project_object: ProjectObject) -> str:
#     """
#     Updates an existing project. Provide the full project dictionary object with modified fields.
#     Retrieve the project object first using get_by_fields(search='projects').
#     """
#     if not ticktick_client:
#         return format_response({"error": "TickTick client not initialized."})
#     if not isinstance(project_object, dict) or 'id' not in project_object:
#          return format_response({"error": "Invalid input: project_object must be a dictionary with an 'id'."})
# 
#     try:
#         updated_project = ticktick_client.project.update(project_object)
#         return format_response(updated_project)
#     except Exception as e:
#         return format_response({"error": f"Failed to update project {project_object.get('id')}: {e}"})
# 
# @mcp.tool()
# async def ticktick_delete_projects(project_ids: Union[ProjectId, ListOfProjectIds]) -> str:
#     """
#     Deletes one or more projects by their IDs. WARNING: Deletes tasks within the project.
#     Provide a single ID string or a list of ID strings.
#     """
#     if not ticktick_client:
#         return format_response({"error": "TickTick client not initialized."})
#     if not isinstance(project_ids, (str, list)):
#          return format_response({"error": "Invalid input: project_ids must be a string or a list of strings."})
#     ids_to_process = project_ids if isinstance(project_ids, list) else [project_ids]
#     if not all(isinstance(pid, str) for pid in ids_to_process):
#          return format_response({"error": "Invalid input: project_ids must contain only strings."})
# 
#     try:
#         # ticktick-py delete takes IDs directly
#         deleted_projects = ticktick_client.project.delete(project_ids)
#         return format_response(deleted_projects)
#     except Exception as e:
#         return format_response({"error": f"Failed to delete projects {project_ids}: {e}"})
# 
# @mcp.tool()
# async def ticktick_archive_projects(project_ids: Union[ProjectId, ListOfProjectIds]) -> str:
#     """Archives one or more projects by their IDs."""
#     if not ticktick_client:
#         return format_response({"error": "TickTick client not initialized."})
#     if not isinstance(project_ids, (str, list)):
#          return format_response({"error": "Invalid input: project_ids must be a string or a list of strings."})
#     ids_to_process = project_ids if isinstance(project_ids, list) else [project_ids]
#     if not all(isinstance(pid, str) for pid in ids_to_process):
#          return format_response({"error": "Invalid input: project_ids must contain only strings."})
# 
#     try:
#         archived_projects = ticktick_client.project.archive(project_ids)
#         return format_response(archived_projects)
#     except Exception as e:
#         return format_response({"error": f"Failed to archive projects {project_ids}: {e}"})
# 
# @mcp.tool()
# async def ticktick_create_project_folder(name: Union[FolderName, ListOfStrings]) -> str:
#     """Creates one or more project folders."""
#     if not ticktick_client:
#         return format_response({"error": "TickTick client not initialized."})
#     if not isinstance(name, (str, list)):
#          return format_response({"error": "Invalid input: name must be a string or a list of strings."})
#     if isinstance(name, list) and not all(isinstance(n, str) for n in name):
#          return format_response({"error": "Invalid input: list must contain only strings."})
# 
#     try:
#         created_folder = ticktick_client.project.create_folder(name)
#         return format_response(created_folder)
#     except Exception as e:
#         return format_response({"error": f"Failed to create project folder(s) '{name}': {e}"})
# 
# @mcp.tool()
# async def ticktick_update_project_folder(folder_object: FolderObject) -> str:
#     """
#     Updates an existing project folder. Provide the full folder dictionary object with modified fields.
#     Retrieve the folder object first using get_by_fields(search='project_folders').
#     """
#     if not ticktick_client:
#         return format_response({"error": "TickTick client not initialized."})
#     if not isinstance(folder_object, dict) or 'id' not in folder_object:
#          return format_response({"error": "Invalid input: folder_object must be a dictionary with an 'id'."})
# 
#     try:
#         updated_folder = ticktick_client.project.update_folder(folder_object)
#         return format_response(updated_folder)
#     except Exception as e:
#         return format_response({"error": f"Failed to update project folder {folder_object.get('id')}: {e}"})
# 
# @mcp.tool()
# async def ticktick_delete_project_folders(folder_ids: Union[FolderId, ListOfFolderIds]) -> str:
#     """
#     Deletes one or more project folders by their IDs. Projects inside are preserved but ungrouped.
#     Provide a single ID string or a list of ID strings.
#     """
#     if not ticktick_client:
#         return format_response({"error": "TickTick client not initialized."})
#     if not isinstance(folder_ids, (str, list)):
#          return format_response({"error": "Invalid input: folder_ids must be a string or a list of strings."})
#     ids_to_process = folder_ids if isinstance(folder_ids, list) else [folder_ids]
#     if not all(isinstance(fid, str) for fid in ids_to_process):
#          return format_response({"error": "Invalid input: folder_ids must contain only strings."})
# 
#     try:
#         deleted_folders = ticktick_client.project.delete_folder(folder_ids)
#         return format_response(deleted_folders)
#     except Exception as e:
#         return format_response({"error": f"Failed to delete project folders {folder_ids}: {e}"})

# ==================
# Tag Tools
# ==================
# @mcp.tool()
# async def ticktick_create_tag(
#     label: str,
#     color: Optional[str] = 'random', # Hex string or 'random'
#     parent_label: Optional[TagLabel] = None,
#     sort_order: Optional[int] = None # See ticktick_client.tag.SORT_DICTIONARY
# ) -> str:
#     """
#     Creates a new tag. Allows labels with special characters normally disallowed.
#     Sort order integers correspond to: 0:'project', 1:'dueDate', 2:'title', 3:'priority'.
#     """
#     if not ticktick_client:
#         return format_response({"error": "TickTick client not initialized."})
#     if not isinstance(label, str):
#          return format_response({"error": "Invalid input: label must be a string."})
# 
#     try:
#         # Use builder internally
#         tag_obj = ticktick_client.tag.builder(
#             label=label,
#             color=color,
#             parent=parent_label,
#             sort=sort_order
#         )
#          # Pass the *list* containing the single built object to create
#         created_tag = ticktick_client.tag.create([tag_obj])
#          # Create returns a list, extract the single element
#         return format_response(created_tag[0] if created_tag else None)
#     except Exception as e:
#         return format_response({"error": f"Failed to create tag '{label}': {e}"})
# 
# @mcp.tool()
# async def ticktick_update_tag(tag_object: TagObject) -> str:
#     """
#     Updates an existing tag's color or sort order. Provide the full tag dictionary object with modified fields.
#     Retrieve the tag object first using get_by_fields(search='tags').
#     Use dedicated tools for renaming or nesting.
#     """
#     if not ticktick_client:
#         return format_response({"error": "TickTick client not initialized."})
#     if not isinstance(tag_object, dict) or 'name' not in tag_object: # Tags use 'name' as ID key
#          return format_response({"error": "Invalid input: tag_object must be a dictionary with a 'name'."})
# 
#     try:
#         # Note: ticktick-py update for tags mainly supports color/sort. Renaming/Nesting have own methods.
#         updated_tag = ticktick_client.tag.update(tag_object)
#         return format_response(updated_tag)
#     except Exception as e:
#         return format_response({"error": f"Failed to update tag {tag_object.get('name')}: {e}"})
# 
# @mcp.tool()
# async def ticktick_delete_tags(labels: Union[TagLabel, ListOfTagLabels]) -> str:
#     """Deletes one or more tags by their labels. Provide a single label string or a list of label strings."""
#     if not ticktick_client:
#         return format_response({"error": "TickTick client not initialized."})
#     if not isinstance(labels, (str, list)):
#          return format_response({"error": "Invalid input: labels must be a string or a list of strings."})
#     labels_to_process = labels if isinstance(labels, list) else [labels]
#     if not all(isinstance(lbl, str) for lbl in labels_to_process):
#          return format_response({"error": "Invalid input: labels list must contain only strings."})
# 
#     try:
#         # ticktick-py delete takes labels directly
#         deleted_tags = ticktick_client.tag.delete(labels)
#         return format_response(deleted_tags)
#     except Exception as e:
#         return format_response({"error": f"Failed to delete tags {labels}: {e}"})
# 
# @mcp.tool()
# async def ticktick_rename_tag(old_label: TagLabel, new_label: TagLabel) -> str:
#     """Renames an existing tag."""
#     if not ticktick_client:
#         return format_response({"error": "TickTick client not initialized."})
#     if not isinstance(old_label, str) or not isinstance(new_label, str):
#          return format_response({"error": "Invalid input: old_label and new_label must be strings."})
# 
#     try:
#         renamed_tag = ticktick_client.tag.rename(old_label, new_label)
#         return format_response(renamed_tag)
#     except Exception as e:
#         return format_response({"error": f"Failed to rename tag '{old_label}' to '{new_label}': {e}"})
# 
# @mcp.tool()
# async def ticktick_merge_tags(labels_to_merge: Union[TagLabel, ListOfTagLabels], target_label: TagLabel) -> str:
#     """Merges tasks from one or more tags into a target tag, then deletes the merged tags."""
#     if not ticktick_client:
#         return format_response({"error": "TickTick client not initialized."})
#     if not isinstance(labels_to_merge, (str, list)) or not isinstance(target_label, str):
#          return format_response({"error": "Invalid input: labels_to_merge must be str/list, target_label must be str."})
#     if isinstance(labels_to_merge, list) and not all(isinstance(lbl, str) for lbl in labels_to_merge):
#          return format_response({"error": "Invalid input: labels_to_merge list must contain only strings."})
# 
#     try:
#         merged_tag_result = ticktick_client.tag.merge(labels_to_merge, target_label)
#         return format_response(merged_tag_result)
#     except Exception as e:
#         return format_response({"error": f"Failed to merge tags {labels_to_merge} into {target_label}: {e}"})
# 
# @mcp.tool()
# async def ticktick_nest_tag(child_label: TagLabel, parent_label: Optional[TagLabel]) -> str:
#     """
#     Nests a child tag under a parent tag.
#     To un-nest a tag, provide the child_label and set parent_label to null or an empty string.
#     """
#     if not ticktick_client:
#         return format_response({"error": "TickTick client not initialized."})
#     if not isinstance(child_label, str):
#          return format_response({"error": "Invalid input: child_label must be a string."})
#     if parent_label is not None and not isinstance(parent_label, str):
#          return format_response({"error": "Invalid input: parent_label must be a string or null."})
# 
#     # ticktick-py uses None for un-nesting
#     parent_input = parent_label if parent_label else None
# 
#     try:
#         nested_tag_result = ticktick_client.tag.nesting(child_label, parent_input)
#         return format_response(nested_tag_result)
#     except Exception as e:
#         parent_desc = f"under '{parent_label}'" if parent_input else " (un-nesting)"
#         return format_response({"error": f"Failed to nest tag '{child_label}' {parent_desc}: {e}"})
# 
# @mcp.tool()
# async def ticktick_change_tag_color(label: TagLabel, color: str) -> str:
#     """Changes the color of a specific tag."""
#     if not ticktick_client:
#         return format_response({"error": "TickTick client not initialized."})
#     if not isinstance(label, str) or not isinstance(color, str):
#          return format_response({"error": "Invalid input: label and color must be strings."})
# 
#     try:
#         colored_tag = ticktick_client.tag.color(label, color)
#         return format_response(colored_tag)
#     except Exception as e:
#         return format_response({"error": f"Failed to change color for tag '{label}': {e}"})
# 
# @mcp.tool()
# async def ticktick_change_tag_sort(label: TagLabel, sort_order: int) -> str:
#     """
#     Changes the sort order of a specific tag.
#     Sort order integers correspond to: 0:'project', 1:'dueDate', 2:'title', 3:'priority'.
#     """
#     if not ticktick_client:
#         return format_response({"error": "TickTick client not initialized."})
#     if not isinstance(label, str) or not isinstance(sort_order, int):
#          return format_response({"error": "Invalid input: label must be a string, sort_order must be an integer."})
# 
#     try:
#         sorted_tag = ticktick_client.tag.sorting(label, sort_order)
#         return format_response(sorted_tag)
#     except Exception as e:
#         return format_response({"error": f"Failed to change sort order for tag '{label}': {e}"})
# 
# ==================
# Helper Tools (from ticktick-py helpers)
# ==================

@mcp.tool()
async def ticktick_convert_datetime_to_ticktick_format(datetime_iso_string: str, tz: str) -> str:
    """
    [Helper Tool] Converts an ISO 8601 format date/time string to the specific string format required for TickTick API updates.

    Agent Usage Guide:
    - Purpose: Use this to generate date strings for functions that require the TickTick date format, such as `ticktick_update_task`.
    - Required arguments:
        - 'datetime_iso_string': The date/time to convert. Must be an **ISO 8601 format string** (e.g., '2025-04-26T10:00:00').
        - 'tz': The timezone of the given date/time (e.g., 'America/New_York', 'Asia/Seoul', 'UTC'). Must be a valid TZ database name.
    - Returns: On success, a JSON string containing the TickTick formatted date string (e.g., '{"ticktick_format": "2025-04-26T10:00:00.000+0000"}'). On failure, a JSON string containing error information.
    """
    try:
        dt_obj = datetime.datetime.fromisoformat(datetime_iso_string)
        ticktick_format = convert_date_to_tick_tick_format(dt_obj, tz)
        return format_response({"ticktick_format": ticktick_format})
    except ValueError as e:
        return format_response({"error": f"Invalid datetime format or timezone: {e}. Use ISO format and valid TZ name."})
    except Exception as e:
        return format_response({"error": f"Conversion failed: {e}"})


# ==================
# Generic Getters (from TickTickClient base)
# ==================
@mcp.tool()
async def ticktick_get_by_id(obj_id: str) -> str:
    """
    Retrieves a single TickTick object (task, project, tag, etc.) using its unique ID.

    Agent Usage Guide:
    - Required argument: 'obj_id' - The unique ID (string) of the object to find.
    - Purpose: Use this to query the latest information for a specific object or to obtain an object to pass to other functions (e.g., `ticktick_update_task`, `ticktick_complete_task`).
    - Returns: On success, a JSON string of the found object (TaskObject, ProjectObject, etc.). Returns 'null' if no object is found for the ID. On failure, returns a JSON string with error information.
    """
    if not ticktick_client:
        return format_response({"error": "TickTick client not initialized."})
    if not isinstance(obj_id, str):
         return format_response({"error": "Invalid input: obj_id must be a string."})
    try:
        obj = ticktick_client.get_by_id(obj_id)
        return format_response(obj)
    except Exception as e:
        # get_by_id might return None if not found, handle this gracefully
        return format_response({"error": f"Failed to get object by ID {obj_id}: {e}", "result": None})

@mcp.tool()
async def ticktick_get_by_fields(search: str, **fields: Any) -> str:
    """
    Searches for and retrieves **one** TickTick object that matches multiple field conditions.

    Agent Usage Guide:
    - Required arguments:
        - 'search': The type of object to search for (string). Must be one of 'tasks', 'projects', 'tags', 'project_folders'.
        - '**fields': Keyword arguments for the field conditions to use for searching. **At least one field condition** must be provided (e.g., `title='Important Meeting'`, `name='Work Project'`).
    - How it works: Searches for objects where **all** provided field conditions match. Even if multiple objects match, only the **first** one found is returned.
    - Returns: On success, a JSON string of the first matching object (TaskObject, ProjectObject, etc.). Returns 'null' if no matching object is found. On argument error or failure, returns a JSON string with error information.
    """
    if not ticktick_client:
        return format_response({"error": "TickTick client not initialized."})
    if not isinstance(search, str) or search not in ['tasks', 'projects', 'tags', 'project_folders']:
         return format_response({"error": "Invalid input: 'search' must be 'tasks', 'projects', 'tags', or 'project_folders'."})
    if not fields:
         # FIX: Return error as get_by_fields requires fields according to the error message
         return format_response({"error": "Invalid input: At least one field=value pair is required for get_by_fields."})

    try:
        # The method might return None if no match
        # IMPORTANT CHANGE: ticktick-py get_by_fields returns a list, even if only one match
        obj_list = ticktick_client.get_by_fields(search=search, **fields)
        # Return the first item if found, otherwise None (or empty list as per original format_response)
        result = obj_list[0] if obj_list else None
        return format_response(result)
    except Exception as e:
        return format_response({"error": f"Failed to get object by fields ({search}, {fields}): {e}", "result": None})

@mcp.tool()
async def ticktick_get_all(search: str) -> str:
    """
    Retrieves a list of all TickTick objects of a specified type.

    Agent Usage Guide:
    - Required argument: 'search' - The type of objects to retrieve (string). Must be one of 'tasks', 'projects', 'tags', 'project_folders'.
    - How it works:
        - 'tasks': Retrieves a list of **uncompleted** tasks from all projects (including inbox). (Uses the internal `_get_all_tasks_from_ticktick` helper)
        - 'projects': Retrieves a list of all projects.
        - 'tags': Retrieves a list of all tags.
        - 'project_folders': Retrieves a list of all project folders.
    - Returns: On success, a JSON string of the list of objects of the specified type (ListOfTaskObjects, ListOfProjectObjects, etc.). Returns an empty list '[]' if the list is empty. On failure, returns a JSON string with error information.
    """
    if not ticktick_client:
        return format_response({"error": "TickTick client not initialized."})
    if not isinstance(search, str) or search not in ['tasks', 'projects', 'tags', 'project_folders']:
         return format_response({"error": "Invalid input: 'search' must be 'tasks', 'projects', 'tags', or 'project_folders'."})

    try:
        if search == 'tasks':
            # Use the internal helper to get tasks from all projects
            all_objs = _get_all_tasks_from_ticktick()
        # FIX: Use client.state for projects, tags, and folders instead of get_by_fields
        elif search == 'projects':
             all_objs = ticktick_client.state.get('projects', [])
        elif search == 'tags':
             all_objs = ticktick_client.state.get('tags', [])
        elif search == 'project_folders':
             all_objs = ticktick_client.state.get('project_folders', [])
        else:
             # Should not happen due to initial check, but good practice
             return format_response({"error": f"Unknown search type: {search}"})

        # Ensure the result is always a list, even if state returns None
        if all_objs is None: all_objs = []

        return format_response(all_objs)
    except ConnectionError as ce:
         return format_response({"error": str(ce)})
    except Exception as e:
        return format_response({"error": f"Failed to get all {search}: {e}"})

# ==================
# New Filtering Tool (새 필터링 도구)
# ==================
@mcp.tool()
async def ticktick_filter_tasks(filters_json: str) -> str:
    """
    [Filtering Tool] Returns a list of *uncompleted* tasks that match the given filter conditions.

    Agent Usage Guide:
    - Required argument: 'filters_json' - A **JSON formatted string** representing the filter conditions. Must be a JSON object (dictionary) (e.g., `'{\"priority\": 5, \"projectId\": \"project123\"}'`).
    - Supported Filters: Can filter based on most fields present in a TaskObject (e.g., 'status', 'priority', 'projectId', 'tags', etc.).
        - 'status': Since this function targets uncompleted tasks, a `status: 0` condition is implicitly true.
        - 'priority': 0 (None), 1 (Low), 3 (Medium), 5 (High).
        - 'tags': Checks for an exact match with the list of tag names (not partial match).
    - How it works: Fetches all uncompleted tasks, then filters them, keeping only those that match **all** conditions specified in 'filters_json'.
    - Returns: On success, a JSON string of the list of filtered task objects (TaskObject). Returns an empty list '[]' if no tasks match the conditions. Returns a JSON string with error information if the JSON format is invalid or if the operation fails.
    """
    if not ticktick_client:
        return format_response({"error": "TickTick client not initialized."})

    try:
        # 필터 JSON 파싱
        filters = json.loads(filters_json)
        if not isinstance(filters, dict):
            raise ValueError("Filters must be a JSON object (dictionary).")
    except json.JSONDecodeError:
        return format_response({"error": "Invalid JSON format for filters."})
    except ValueError as e:
        return format_response({"error": str(e)})
    except Exception as e:
        return format_response({"error": f"Error processing filters: {e}"})

    try:
        # 모든 *미완료* task 가져오기 (수정된 로직 사용)
        all_uncompleted_tasks = _get_all_tasks_from_ticktick()

        filtered_tasks = []
        for task in all_uncompleted_tasks:
             # task가 유효한 사전인지 확인
            if not isinstance(task, dict):
                print(f"Skipping invalid task data: {task}", file=sys.stderr)
                continue

            match = True
            for key, value in filters.items():
                # task에 키가 없거나 값이 일치하지 않으면 필터링 제외
                if key not in task or task[key] != value:
                    match = False
                    break
            if match:
                filtered_tasks.append(task)

        return format_response(filtered_tasks)
    except ConnectionError as ce:
         return format_response({"error": str(ce)})
    except Exception as e:
        return format_response({"error": f"Failed to get or filter tasks: {e}"})


# --- Helper for Due Date Parsing ---
def _parse_due_date(due_date_str: Optional[str]) -> Optional[datetime.date]:
    """Parses TickTick's dueDate string into a date object."""
    if not due_date_str:
        return None
    try:
        # Extract YYYY-MM-DD part. Assumes format like '2024-07-27T...'
        date_part = due_date_str[:10]
        return datetime.datetime.strptime(date_part, "%Y-%m-%d").date()
    except (ValueError, TypeError, IndexError):
        # Log error or handle appropriately if format is unexpected
        print(f"Warning: Could not parse dueDate string: {due_date_str}", file=sys.stderr)
        return None

# ==================
# New Due Date Filtering Tools
# ==================

@mcp.tool()
async def ticktick_get_due_today_or_overdue_tasks(
    tag_label: Optional[TagLabel] = None,
    sort_by_priority: bool = True
) -> str:
    """
    [Special Filtering Tool] Retrieves all *uncompleted* tasks that are due today or already overdue. Optionally filters by tag and sorts by priority.

    Agent Usage Guide:
    - Purpose: Useful for quickly finding tasks that need attention today or are delayed.
    - Optional arguments:
        - 'tag_label': Filters for tasks that have the specified tag. Provide the tag name (string).
        - 'sort_by_priority': If True (default), sorts the results by priority in descending order (High -> Medium -> Low -> None).
    - How it works: Fetches all uncompleted tasks, selects those whose 'dueDate' is today (based on execution time) or earlier. If 'tag_label' is provided, further filters by that tag. Finally, sorts according to 'sort_by_priority'.
    - Returns: On success, a JSON string of the list of matching task objects (TaskObject). Returns an empty list '[]' if no tasks match. On failure, returns a JSON string with error information.
    """
    if not ticktick_client:
        return format_response({"error": "TickTick client not initialized."})

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

        return format_response(filtered_tasks)
    except ConnectionError as ce:
         return format_response({"error": str(ce)})
    except Exception as e:
        # Added traceback printing for better debugging during development
        import traceback
        print(f"Error in ticktick_get_due_today_or_overdue_tasks: {e}\n{traceback.format_exc()}", file=sys.stderr)
        return format_response({"error": f"Failed to get/filter due today/overdue tasks: {e}"})


@mcp.tool()
async def ticktick_get_due_this_friday_or_overdue_tasks(
    tag_label: Optional[TagLabel] = None,
    sort_by_priority: bool = True
) -> str:
    """
    [Special Filtering Tool] Retrieves all *uncompleted* tasks that are due by this Friday or are already overdue. Optionally filters by tag and sorts by priority.

    Agent Usage Guide:
    - Purpose: Useful for finding tasks that need to be handled within the current week (by Friday) or are delayed.
    - Optional arguments:
        - 'tag_label': Filters for tasks that have the specified tag. Provide the tag name (string).
        - 'sort_by_priority': If True (default), sorts the results by priority in descending order (High -> Medium -> Low -> None).
    - How it works: Fetches all uncompleted tasks, selects those whose 'dueDate' is this Friday (based on execution time) or earlier. 'Friday' refers to the upcoming Friday of the current week. If 'tag_label' is provided, further filters by that tag. Finally, sorts according to 'sort_by_priority'.
    - Returns: On success, a JSON string of the list of matching task objects (TaskObject). Returns an empty list '[]' if no tasks match. On failure, returns a JSON string with error information.
    """
    if not ticktick_client:
        return format_response({"error": "TickTick client not initialized."})

    try:
        all_uncompleted_tasks = _get_all_tasks_from_ticktick()
        today = datetime.date.today()
        # Calculate days until upcoming Friday (Friday is 4)
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

        return format_response(filtered_tasks)
    except ConnectionError as ce:
         return format_response({"error": str(ce)})
    except Exception as e:
        # Added traceback printing for better debugging during development
        import traceback
        print(f"Error in ticktick_get_due_this_friday_or_overdue_tasks: {e}\n{traceback.format_exc()}", file=sys.stderr)
        return format_response({"error": f"Failed to get/filter due by Friday/overdue tasks: {e}"})


def main():
    print("Initializing TickTick MCP Server...", file=sys.stderr)
    client_instance = initialize_ticktick_client()

    if client_instance:
        print("TickTick client ready. Starting MCP server on stdio...", file=sys.stderr)
        # Run the MCP server using stdio transport
        mcp.run(transport="stdio")
    else:
        print("MCP Server cannot start due to TickTick client initialization failure.", file=sys.stderr)
        sys.exit(1)

# --- Main Execution ---
if __name__ == "__main__":
    main()