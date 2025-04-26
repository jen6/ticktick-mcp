import datetime
import logging

# Import the shared MCP instance for the decorator
from ..mcp_instance import mcp
# Import helpers
from ..helpers import format_response, require_client
# Import the specific conversion function from the library
from ticktick.helpers.time_methods import convert_date_to_tick_tick_format

# ================== #
# Conversion Tools   #
# ================== #

@mcp.tool()
# @require_client # Does not strictly need the client, but kept for consistency? Removing it.
async def ticktick_convert_datetime_to_ticktick_format(datetime_iso_string: str, tz: str) -> str:
    """
    [Helper Tool] Converts ISO 8601 date/time string to TickTick API format.
    (Agent Usage Guide in docstring)
    """
    try:
        dt_obj = datetime.datetime.fromisoformat(datetime_iso_string)
        ticktick_format = convert_date_to_tick_tick_format(dt_obj, tz)
        return format_response({"ticktick_format": ticktick_format})
    except ValueError as e:
        # Handle specific parsing/timezone errors
        logging.warning(f"Invalid datetime format or timezone for conversion: {e} (Input: '{datetime_iso_string}', TZ: '{tz}')")
        return format_response({"error": f"Invalid datetime format or timezone: {e}. Use ISO format and valid TZ name.", "status": "error"})
    except Exception as e:
        logging.error(f"Conversion failed for '{datetime_iso_string}' (TZ: '{tz}'): {e}", exc_info=True)
        return format_response({"error": f"Conversion failed: {e}", "status": "error"}) 