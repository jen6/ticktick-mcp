import datetime
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Import the shared MCP instance for the decorator
from ..mcp_instance import mcp
# Import helpers
from ..helpers import format_response, require_ticktick_client
# Import the specific conversion function from the library
from ticktick.helpers.time_methods import convert_date_to_tick_tick_format

# ================== #
# Conversion Tools   #
# ================== #

@mcp.tool()
@require_ticktick_client
async def ticktick_convert_datetime_to_ticktick_format(datetime_iso_string: str, tz: str) -> str:
    """
    [Helper Tool] Converts ISO 8601 date/time string to TickTick API format.
    TickTick expects dates in the format 'YYYY-MM-DDTHH:mm:ss.fff+ZZZZ'.

    Args:
        datetime_iso_string: The date/time string in ISO 8601 format (e.g., '2024-07-26T10:00:00', '2024-07-26', '2024-07-26T10:00:00+09:00').
        tz: IANA timezone name (e.g., 'Asia/Seoul', 'America/New_York') to interpret the date/time if it's naive (lacks timezone info).

    Returns:
        A JSON string containing the formatted datetime string or an error message.

    Example:
        Converting a date:
        {
            "datetime_iso_string": "2024-08-15",
            "tz": "Asia/Seoul"
        }
        (Result might be: {"formatted_datetime": "2024-08-15T00:00:00.000+0900"})

        Converting a specific time:
        {
            "datetime_iso_string": "2024-09-01T14:30:00",
            "tz": "America/Los_Angeles"
        }
        (Result might be: {"formatted_datetime": "2024-09-01T14:30:00.000-0700"})

        Converting a time with existing offset:
        {
            "datetime_iso_string": "2024-07-26T18:00:00+02:00",
            "tz": "Europe/Paris" # tz is used if input is naive, otherwise original offset is kept/converted
        }
        (Result might be: {"formatted_datetime": "2024-07-26T18:00:00.000+0200"})
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