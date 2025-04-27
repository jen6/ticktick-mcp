# TickTick MCP Server

<!-- Add relevant badges here -->
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
<!-- [![PyPI version](https://badge.fury.io/py/your-package-name.svg)](https://badge.fury.io/py/your-package-name) -->

This project provides an MCP (Model Context Protocol) server that exposes tools for interacting with the [TickTick](https://ticktick.com/) task management service. It allows AI assistants and other applications compatible with MCP (like [Claude Desktop](https://www.anthropic.com/claude-desktop), VS Code Agent Mode, or libraries like [mcp-use](https://github.com/pietrozullo/mcp-use)) to leverage TickTick functionalities programmatically.

Learn more about the Model Context Protocol at [modelcontextprotocol.io](https://modelcontextprotocol.io/).

## ✨ Features

This server provides comprehensive access to TickTick functionalities via MCP tools, categorized as follows:

* **Task Management:** Create, update (including conversion to TickTick's date format), delete, complete, and move tasks.
* **Subtask Management:** Create subtasks by linking existing tasks.
* **Task Retrieval:**
  *  Get all uncompleted tasks.
  *  Get tasks by ID or specific fields.
  *  Get completed tasks within a date range.
  *  Get tasks from a specific project.
  *  Filter tasks based on various critggeria (priority, project, tags, etc.).
*  **Project/Tag Management:** Retrieve all projects, tags, and project folders.
*  **Helper Tools:** Convert datetime strings to the required TickTick format.

Refer to the tool definitions within the `src/ticktick_mcp/tools/` directory for detailed specifications.

## 🚀 Getting Started

This server utilizes the unofficial [`ticktick-py` library](https://lazeroffmichael.github.io/ticktick-py/) to interact with the TickTick API.

### Prerequisites

*  Python >= 3.10
*  Access to TickTick and API credentials (see below).

### Setup

1. **Register a TickTick Application:** Before using the server, you need to register an application with TickTick to obtain API credentials. Follow these steps based on the `ticktick-py` documentation:
    * Go to the [TickTick OpenAPI Documentation](https://developer.ticktick.com/docs#/openapi) and log in with your TickTick account.
    *  Click on `Manage Apps` in the top right corner.
    *  Register a new app by clicking the `+App Name` button. Provide a name for your application (e.g., "MCP Server").
    *  Once created, edit the app details. Note down the generated `Client ID` and `Client Secret`.
    *  For the `OAuth Redirect URL`, enter a URL where you want to be redirected after authorizing the application. It doesn't need to be a live URL
       *  `http://localhost:8080/redirect` or `http://127.0.0.1:8080/` are common choices for local development. 
        * **Ensure this exact URL is saved in your environment variables.**

2. **Environment Variables:** The server requires the TickTick API credentials you just obtained, plus your TickTick login details. By default, it looks for a `.env` file located at `~/.config/ticktick-mcp/.env`.
    * The server *might* create the `~/.config/ticktick-mcp/` directory if it doesn't exist, but it's safer to create it manually.
    * You **must create the `.env` file manually** within that directory.
    * Alternatively, you can specify a different directory using the `--dotenv-dir` command-line argument *only* when running the server directly via Python (see "Running the Server" below).
 
    The `.env` file should contain:
 ```dotenv
 TICKTICK_CLIENT_ID=your_client_id   # Obtained in Step 1
 TICKTICK_CLIENT_SECRET=your_client_secret # Obtained in Step 1
 TICKTICK_REDIRECT_URI=your_redirect_uri # Entered in Step 1 (must match exactly)
 TICKTICK_USERNAME=your_ticktick_email # Your TickTick login email
 TICKTICK_PASSWORD=your_ticktick_password # Your TickTick login password (or app password if enabled)
 ```

3. **Authentication (First Run):** On the first run (either directly or via an MCP client), the underlying `ticktick-py` library will initiate an OAuth2 authentication flow.
    * A web browser window might open automatically, or a URL will be printed in the console/log output.
    * You need to visit this URL, log in to TickTick if necessary, and authorize the application (granting Read and Write permissions).
    * After authorization, you will be redirected to the `TICKTICK_REDIRECT_URI` you specified. 
      * The console will prompt you to **paste this full redirected URL** (which includes a `code=` parameter) back into the terminal.
    * Upon successful verification, a `.token-oauth` file will be created in the same directory as your `.env` file. 
    * This file caches the authorization token, so you typically only need to perform this manual authorization step once every ~6 months or if the token becomes invalid.

### Running the Server

You can run the server in two main ways:

**1. Via an MCP Client (Recommended for AI Assistant Integration):**

Configure your MCP client (like Claude Desktop, VS Code Agent Mode, etc.) to use the server. Example configuration:

```json
{
 "mcpServers": {
 "ticktick": {
  "command": "uvx",
  "args": [
  "--from",
  "git+https://github.com/jen6/ticktick-mcp.git",
  "ticktick-mcp"
  // Optional: Add "--dotenv-dir", "/path/to/your/config" if needed,
  // but standard clients might not support passing extra args easily.
  ]
 }
 }
}
```

## 🔧 Tools

This server provides the following tools for interacting with the TickTick task management service:

### Task Management

1. `ticktick_create_task`
   * Creates a new task in TickTick
   * Inputs:
     * `title` (string): The title of the task. Required.
     * `projectId` (string, optional): ID of the project to add the task to.
     * `content` (string, optional): Additional details or notes for the task.
     * `desc` (string, optional): Description for the task.
     * `allDay` (boolean, optional): Set to True if the task spans the entire day.
     * `startDate` (string, optional): Start date/time in ISO 8601 format.
     * `dueDate` (string, optional): Due date/time in ISO 8601 format.
     * `timeZone` (string, optional): IANA timezone name (e.g., 'Asia/Seoul').
     * `reminders` (array of strings, optional): List of reminder triggers in RFC 5545 format.
     * `repeat` (string, optional): Recurring rule in RFC 5545 format.
     * `priority` (integer, optional): Task priority (0=None, 1=Low, 3=Medium, 5=High).
     * `sortOrder` (integer, optional): Custom sort order value.
     * `items` (array of objects, optional): List of subtask dictionaries.

2. `ticktick_update_task`
   * Updates an existing task
   * Inputs:
     * `task_object` (object): A dictionary with task properties to update including the task `id`.

3. `ticktick_delete_tasks`
   * Deletes one or more tasks
   * Inputs:
     * `task_ids` (string or array of strings): A single task ID or list of task IDs to delete.

4. `ticktick_complete_task`
   * Marks a task as complete
   * Inputs:
     * `task_id` (string): The ID of the task to mark as complete.

5. `ticktick_move_task`
   * Moves a task to a different project
   * Inputs:
     * `task_id` (string): The ID of the task to move.
     * `new_project_id` (string): The ID of the destination project.

6. `ticktick_make_subtask`
   * Makes one task a subtask of another
   * Inputs:
     * `parent_task_id` (string): The ID of the task that will become the parent.
     * `child_task_id` (string): The ID of the task that will become the subtask.

### Task Retrieval

7. `ticktick_get_by_id`
   * Retrieves a specific object (task, project, etc.) by ID
   * Inputs:
     * `obj_id` (string): The unique ID of the object to retrieve.

8. `ticktick_get_all`
   * Retrieves all objects of a specified type
   * Inputs:
     * `search` (string): The type of objects to retrieve (e.g., 'tasks', 'projects', 'tags').

9. `ticktick_get_tasks_from_project`
   * Retrieves all uncompleted tasks from a specific project
   * Inputs:
     * `project_id` (string): The ID of the project.

10. `ticktick_filter_tasks`
    * Filters tasks based on various criteria
    * Inputs:
      * `filter_criteria` (object): Dictionary with filtering parameters such as:
        * `status` (string): Task status ('uncompleted' or 'completed').
        * `project_id` (string, optional): Project ID to filter tasks by.
        * `tag_label` (string, optional): Tag name to filter tasks by.
        * `priority` (integer, optional): Priority level.
        * `due_start_date` (string, optional): ISO format start date for due date filter.
        * `due_end_date` (string, optional): ISO format end date for due date filter.
        * `completion_start_date` (string, optional): Start date for completion date filter.
        * `completion_end_date` (string, optional): End date for completion date filter.
        * `sort_by_priority` (boolean, optional): Sort results by priority.
        * `tz` (string, optional): Timezone for date interpretation.

### Helper Tools

11. `ticktick_convert_datetime_to_ticktick_format`
    * Converts ISO 8601 date/time string to TickTick API format
    * Inputs:
      * `datetime_iso_string` (string): The date/time string in ISO 8601 format.
      * `tz` (string): IANA timezone name to interpret the date/time.

## 🤖 Sample agent prompt

```
## Persona: Daily Stand-up Agent

- **Role**: AI agent integrated with the user's TickTick account to assist in daily work planning
- **Goal**: Help the user start their day efficiently, focus on key tasks, and break large tasks into manageable subtasks

---

## Core Features & Workflow

1. **Fetch Current Time** 
 - Retrieve current time using `time mcp`.

2. **Session Start & Data Loading** 
 - The user initiates the session with a command like "Start daily stand-up" or "Hello." 
 - Call TickTick MCP API to fetch all tasks due **today**. 
 - Optionally notify the user that data is loading (e.g., "Fetching today's and overdue tasks from TickTick…").

3. **Daily Briefing**
 Good morning! Today's date is {YYYY-MM-DD}. Here's your daily stand-up from TickTick:

 **Tasks Due Today:**
 - Task Name 1
 - Task Name 2
 …

 **Overdue Tasks:**
 - Task Name 3
 - Task Name 4
 …

4. **Select Key Task** 
 > "Which of these tasks would you like to focus on first or must complete today? 
 > Or is there another important task you'd like to add?"

5. **Task Breakdown (Subtask Creation)** 
 - After the user selects a main task, suggest 2–5 specific subtasks needed to complete it. 
 - Example (if "Write project report" is selected):
  1. Draft outline & table of contents (10 min) 
  2. Gather & analyze data (30 min) 
  3. Write section drafts (1 h) 
  4. Review & revise draft (30 min) 
  5. Final submission (10 min)

6. **Confirm & Add Subtasks** 
 - Ask the user to confirm or adjust the suggested subtasks: 
  > "Does this breakdown look good? Any changes?" 
 - Once approved, call MCP to add each subtask to TickTick, setting them as children of the main task if supported, naming them "[Main Task] – [Subtask]". 
 mcp.ticktick.addTask({
  name: "[Main Task] – [Subtask]",
  parentId: "..."
 });

7. **Session Close** 
 > "All subtasks have been added to TickTick. Have a productive day! Anything else I can help with?"

---

## Additional Guidelines

- **Tone & Manner**: Friendly, proactive, and organized. 
- **MCP Interface Examples**: 
 // Fetch today's due tasks
 mcp.ticktick.getTasks({
 filter_criteria: {
  status: "uncompleted",
  tz: "Asia/Seoul",
  due_end_date: "2025-04-29"
 }
 });

 // Add a subtask
 mcp.ticktick.addTask({
 name: "Project Report – Write Draft",
 parentId: "task123"
 });
- **Error Handling**: Inform the user and suggest retrying on MCP call failures. 
- **Clarity**: Present task lists and subtask suggestions clearly. 
- **Plan First**: Use `sequential thinking mcp` to plan steps before adding or modifying tasks.

```

## 🤝 Contributing

Contributions are welcome! Please feel free to open an issue or submit a pull request.


## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🔗 See Also

* [Model Context Protocol Servers](https://github.com/modelcontextprotocol/servers): The central repository for reference implementations of MCP servers for various tools and platforms.
* [modelcontextprotocol.io](https://modelcontextprotocol.io/): Official documentation for the Model Context Protocol.
* [pietrozullo/mcp-use](https://github.com/pietrozullo/mcp-use): A popular Python library for building clients/agents that interact with MCP servers.
* [lazeroffmichael/ticktick-py](https://lazeroffmichael.github.io/ticktick-py/): The unofficial TickTick API library used by this project to handle authentication and API interactions.
