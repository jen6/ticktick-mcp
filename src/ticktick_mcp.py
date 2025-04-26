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
    TickTick에 새로운 할 일(task)을 생성합니다.

    에이전트 사용 가이드:
    - 필수 인자: 'title' (할 일 제목).
    - 선택적 인자:
        - 'projectId': 할 일을 추가할 프로젝트 ID. 지정하지 않으면 기본 받은편지함(inbox)에 추가됩니다.
        - 'content'/'desc': 할 일의 내용 또는 설명.
        - 'startDate'/'dueDate': 시작/마감 날짜 및 시간. **ISO 8601 형식 문자열** (예: '2025-04-26T10:00:00')로 제공해야 합니다.
        - 'allDay': True로 설정하면 시간 정보 없이 날짜만 사용됩니다.
        - 'timeZone': 날짜/시간의 기준 시간대 (예: 'Asia/Seoul', 'UTC').
        - 'reminders': 알림 설정 문자열 리스트 (TickTick 형식 따름).
        - 'repeat': 반복 설정 문자열 (TickTick 형식 따름, 예: 'RRULE:FREQ=DAILY;INTERVAL=1').
        - 'priority': 우선순위 (0: 없음, 1: 낮음, 3: 중간, 5: 높음).
        - 'items': 하위 할 일(subtask) 목록. 각 하위 할 일은 {'title': '...', 'status': 0} 형태의 딕셔너리입니다.
    - 반환값: 성공 시 생성된 할 일 객체(TaskObject)의 JSON 문자열. 실패 시 에러 정보가 포함된 JSON 문자열.
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
    기존 할 일(task)의 내용을 업데이트합니다.

    에이전트 사용 가이드:
    - 필수 인자: 'task_object' - 업데이트할 필드가 **포함된 전체 할 일 객체 딕셔너리**. 'id' 필드가 반드시 포함되어야 합니다.
    - 사용 방법:
        1. 먼저 `ticktick_get_by_id` 또는 `ticktick_get_by_fields`를 사용하여 업데이트하려는 할 일의 **전체 객체**를 가져옵니다.
        2. 가져온 객체에서 변경하려는 필드의 값을 수정합니다.
        3. 수정된 **전체 객체**를 이 함수의 'task_object' 인자로 전달합니다.
    - 주의사항:
        - 날짜 필드('startDate', 'dueDate', 'completedTime')는 TickTick API가 요구하는 특정 형식의 문자열이어야 합니다 (예: '2025-04-26T10:00:00.000+0000').
        - 필요하다면 `ticktick_convert_datetime_to_ticktick_format` 헬퍼 도구를 사용하여 ISO 8601 문자열을 TickTick 형식으로 변환할 수 있습니다.
    - 반환값: 성공 시 업데이트된 할 일 객체(TaskObject)의 JSON 문자열. 실패 시 에러 정보가 포함된 JSON 문자열.
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
    ID를 사용하여 하나 또는 여러 개의 할 일(task)을 삭제합니다.

    에이전트 사용 가이드:
    - 필수 인자: 'task_ids' - 삭제할 할 일의 ID (문자열) 또는 ID 리스트 (문자열 리스트).
    - 동작 방식: 내부적으로 ID를 사용하여 실제 할 일 객체를 찾은 후 삭제를 시도합니다.
    - 반환값: 성공 시 삭제 작업 결과(TickTick API 응답 기반)의 JSON 문자열. ID를 찾지 못하거나 실패 시 에러/메시지 정보가 포함된 JSON 문자열.
    - 주의: 삭제된 할 일은 복구하기 어려울 수 있습니다.
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
    지정된 기간 내에 완료된 할 일(task) 목록을 가져옵니다.

    에이전트 사용 가이드:
    - 필수 인자: 'start_date' - 조회 시작 날짜. **ISO 8601 형식 문자열** (예: '2025-04-26' 또는 '2025-04-26T10:00:00').
    - 선택적 인자:
        - 'end_date': 조회 종료 날짜. **ISO 8601 형식 문자열**. 지정하지 않으면 'start_date' 당일만 조회합니다.
        - 'include_time': True로 설정하면 날짜뿐만 아니라 시간까지 고려하여 범위를 지정합니다 (ticktick-py의 'full=False'에 해당). False(기본값)이면 시간은 무시하고 날짜만 비교합니다.
        - 'tz': 날짜 비교에 사용할 시간대 (예: 'Asia/Seoul', 'UTC').
    - 반환값: 성공 시 완료된 할 일 객체(TaskObject) 리스트의 JSON 문자열. 실패 시 에러 정보가 포함된 JSON 문자열. 날짜 형식이 잘못된 경우 에러를 반환합니다.
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
    특정 프로젝트 ID에 속한 모든 *미완료* 할 일(task) 목록을 가져옵니다.

    에이전트 사용 가이드:
    - 필수 인자: 'project_id' - 할 일을 가져올 프로젝트의 ID (문자열).
    - 동작 방식: 해당 프로젝트 내의 완료되지 않은 할 일만 반환합니다. 완료된 할 일은 포함되지 않습니다.
    - 반환값: 성공 시 해당 프로젝트의 미완료 할 일 객체(TaskObject) 리스트의 JSON 문자열. 프로젝트 ID가 유효하지 않거나 실패 시 에러 정보가 포함된 JSON 문자열. 목록이 비어 있을 경우 빈 리스트 '[]'를 반환합니다.
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
    ID를 사용하여 특정 할 일(task) 하나를 완료 상태로 변경합니다.

    에이전트 사용 가이드:
    - 필수 인자: 'task_id' - 완료 처리할 할 일의 ID (문자열).
    - 동작 방식: ID를 사용하여 할 일 객체를 찾은 후 완료 처리합니다.
    - 반환값: 성공 시 상태가 업데이트된 할 일 객체(TaskObject)의 JSON 문자열. 할 일을 찾지 못하거나 실패 시 에러 정보가 포함된 JSON 문자열.
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
    특정 할 일(task) 하나를 다른 프로젝트로 이동시킵니다.

    에이전트 사용 가이드:
    - 필수 인자:
        - 'task_id': 이동시킬 할 일의 ID (문자열).
        - 'new_project_id': 할 일을 이동시킬 대상 프로젝트의 ID (문자열).
    - 동작 방식: 'task_id'로 할 일을 찾아 'new_project_id' 프로젝트로 이동합니다.
    - 반환값: 성공 시 이동된 할 일 객체(TaskObject)의 JSON 문자열. 할 일 또는 프로젝트를 찾지 못하거나 실패 시 에러 정보가 포함된 JSON 문자열.
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
    한 할 일(child)을 다른 할 일(parent)의 하위 할 일(subtask)로 만듭니다. 두 할 일은 반드시 같은 프로젝트에 속해야 합니다.

    에이전트 사용 가이드:
    - 필수 인자:
        - 'child_task_id': 하위 할 일이 될 기존 할 일의 ID (문자열).
        - 'parent_task_id': 상위 할 일이 될 기존 할 일의 ID (문자열).
    - 제약 조건: 두 할 일은 동일한 프로젝트 내에 있어야 합니다.
    - 반환값: 성공 시 하위 할 일이 추가된 *상위* 할 일 객체(TaskObject)와 성공 메시지가 포함된 JSON 문자열. 할 일을 찾지 못하거나 다른 프로젝트에 있거나 실패 시 에러 정보가 포함된 JSON 문자열.
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
    [헬퍼 도구] ISO 8601 형식의 날짜/시간 문자열을 TickTick API 업데이트 시 필요한 특정 형식의 문자열로 변환합니다.

    에이전트 사용 가이드:
    - 목적: `ticktick_update_task`와 같이 TickTick 날짜 형식이 필요한 함수에 전달할 날짜 문자열을 생성할 때 사용합니다.
    - 필수 인자:
        - 'datetime_iso_string': 변환할 날짜/시간. **ISO 8601 형식 문자열** (예: '2025-04-26T10:00:00').
        - 'tz': 해당 날짜/시간의 기준 시간대 (예: 'America/New_York', 'Asia/Seoul', 'UTC'). 유효한 TZ 데이터베이스 이름이어야 합니다.
    - 반환값: 성공 시 TickTick 형식의 날짜 문자열 (예: '2025-04-26T10:00:00.000+0000')을 포함한 JSON 문자열 ({"ticktick_format": "..."}). 실패 시 에러 정보가 포함된 JSON 문자열.
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
    고유 ID를 사용하여 TickTick 객체 (할 일, 프로젝트, 태그 등) 하나를 가져옵니다.

    에이전트 사용 가이드:
    - 필수 인자: 'obj_id' - 찾으려는 객체의 고유 ID (문자열).
    - 사용 목적: 특정 객체의 최신 정보를 조회하거나, 다른 함수(예: `ticktick_update_task`, `ticktick_complete_task`)에 전달할 객체를 얻기 위해 사용합니다.
    - 반환값: 성공 시 찾은 객체 (TaskObject, ProjectObject 등)의 JSON 문자열. ID에 해당하는 객체를 찾지 못하면 'null'을 반환합니다. 실패 시 에러 정보가 포함된 JSON 문자열.
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
    여러 필드 조건을 만족하는 TickTick 객체 **하나**를 검색하여 가져옵니다.

    에이전트 사용 가이드:
    - 필수 인자:
        - 'search': 검색할 객체 유형 (문자열). 'tasks', 'projects', 'tags', 'project_folders' 중 하나여야 합니다.
        - '**fields': 검색 조건으로 사용할 필드와 값의 키워드 인자. **적어도 하나 이상의 필드 조건**을 제공해야 합니다. (예: `title='중요 회의'`, `name='업무 프로젝트'`)
    - 동작 방식: 제공된 필드 조건과 **모두 일치**하는 객체를 검색합니다. 여러 객체가 조건에 맞더라도 **첫 번째** 찾은 객체만 반환합니다.
    - 반환값: 성공 시 조건에 맞는 첫 번째 객체(TaskObject, ProjectObject 등)의 JSON 문자열. 조건에 맞는 객체가 없으면 'null'을 반환합니다. 인자 오류 또는 실패 시 에러 정보가 포함된 JSON 문자열.
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
    지정된 유형의 모든 TickTick 객체 목록을 가져옵니다.

    에이전트 사용 가이드:
    - 필수 인자: 'search' - 가져올 객체 유형 (문자열). 'tasks', 'projects', 'tags', 'project_folders' 중 하나여야 합니다.
    - 동작 방식:
        - 'tasks': 모든 프로젝트(받은편지함 포함)에서 **미완료된** 할 일 목록을 가져옵니다. (내부적으로 `_get_all_tasks_from_ticktick` 헬퍼 사용)
        - 'projects': 모든 프로젝트 목록을 가져옵니다.
        - 'tags': 모든 태그 목록을 가져옵니다.
        - 'project_folders': 모든 프로젝트 폴더 목록을 가져옵니다.
    - 반환값: 성공 시 해당 유형의 객체 리스트(ListOfTaskObjects, ListOfProjectObjects 등)의 JSON 문자열. 목록이 비어있으면 빈 리스트 '[]'를 반환합니다. 실패 시 에러 정보가 포함된 JSON 문자열.
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
    [필터링 도구] 모든 *미완료* 할 일(task) 중에서 주어진 필터 조건과 일치하는 할 일 목록을 반환합니다.

    에이전트 사용 가이드:
    - 필수 인자: 'filters_json' - 필터링 조건을 나타내는 **JSON 형식의 문자열**. JSON 객체(딕셔너리) 형태여야 합니다. (예: `'{\"priority\": 5, \"projectId\": \"project123\"}'`)
    - 지원 필터: 할 일 객체(TaskObject)에 포함된 대부분의 필드를 기준으로 필터링할 수 있습니다 (예: 'status', 'priority', 'projectId', 'tags' 등).
        - 'status': 이 함수는 미완료 할 일만 대상으로 하므로 `status: 0` 조건은 항상 참입니다.
        - 'priority': 0(없음), 1(낮음), 3(중간), 5(높음).
        - 'tags': 태그 이름 리스트와 정확히 일치하는지 확인합니다 (부분 일치 아님).
    - 동작 방식: 모든 미완료 할 일을 가져온 후, 'filters_json'에 명시된 **모든 조건**과 일치하는 할 일만 필터링합니다.
    - 반환값: 성공 시 필터링된 할 일 객체(TaskObject) 리스트의 JSON 문자열. 조건에 맞는 할 일이 없으면 빈 리스트 '[]'를 반환합니다. JSON 형식이 잘못되었거나 실패 시 에러 정보가 포함된 JSON 문자열.
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
    [특수 필터링 도구] 오늘 마감이거나 이미 마감일이 지난 모든 *미완료* 할 일 목록을 가져옵니다. 선택적으로 태그 필터링 및 우선순위 정렬을 적용합니다.

    에이전트 사용 가이드:
    - 목적: 오늘 처리해야 하거나 지연된 할 일을 빠르게 확인하는 데 사용합니다.
    - 선택적 인자:
        - 'tag_label': 특정 태그가 지정된 할 일만 필터링합니다. 태그 이름(문자열)을 제공합니다.
        - 'sort_by_priority': True(기본값)로 설정하면 결과를 우선순위가 높은 순서 (높음 -> 중간 -> 낮음 -> 없음)로 정렬합니다.
    - 동작 방식: 모든 미완료 할 일을 가져온 후, 마감일(dueDate)이 오늘(함수 실행 시점 기준) 또는 그 이전인 할 일만 선택합니다. 추가로 'tag_label'이 제공되면 해당 태그를 가진 할 일만 남깁니다. 마지막으로 'sort_by_priority' 설정에 따라 정렬합니다.
    - 반환값: 성공 시 조건에 맞는 할 일 객체(TaskObject) 리스트의 JSON 문자열. 조건에 맞는 할 일이 없으면 빈 리스트 '[]'를 반환합니다. 실패 시 에러 정보가 포함된 JSON 문자열.
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
    [특수 필터링 도구] 이번 주 금요일까지 마감이거나 이미 마감일이 지난 모든 *미완료* 할 일 목록을 가져옵니다. 선택적으로 태그 필터링 및 우선순위 정렬을 적용합니다.

    에이전트 사용 가이드:
    - 목적: 이번 주 내(금요일까지) 처리해야 하거나 지연된 할 일을 확인하는 데 사용합니다.
    - 선택적 인자:
        - 'tag_label': 특정 태그가 지정된 할 일만 필터링합니다. 태그 이름(문자열)을 제공합니다.
        - 'sort_by_priority': True(기본값)로 설정하면 결과를 우선순위가 높은 순서 (높음 -> 중간 -> 낮음 -> 없음)로 정렬합니다.
    - 동작 방식: 모든 미완료 할 일을 가져온 후, 마감일(dueDate)이 이번 주 금요일(함수 실행 시점 기준) 또는 그 이전인 할 일만 선택합니다. '금요일'은 현재 주의 금요일을 의미합니다. 추가로 'tag_label'이 제공되면 해당 태그를 가진 할 일만 남깁니다. 마지막으로 'sort_by_priority' 설정에 따라 정렬합니다.
    - 반환값: 성공 시 조건에 맞는 할 일 객체(TaskObject) 리스트의 JSON 문자열. 조건에 맞는 할 일이 없으면 빈 리스트 '[]'를 반환합니다. 실패 시 에러 정보가 포함된 JSON 문자열.
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