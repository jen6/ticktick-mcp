import argparse
import logging
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# --- Configuration --- (Argument Parsing and Directory/File Handling)

# Setup logging
# Note: Basic config should ideally be called only once. This might be called
# again if other modules also import logging and call basicConfig. Consider
# a more robust logging setup if this becomes an issue.
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stderr)

# Setup argument parser
parser = argparse.ArgumentParser(description="Run the TickTick MCP server, specifying the directory for the .env file.")
parser.add_argument(
    "--dotenv-dir",
    type=str,
    help="Path to the directory containing the .env file. Defaults to '~/.config/ticktick-mcp'.",
    default="~/.config/ticktick-mcp" # Default value set
)

# Parse arguments
# Note: Parsing args here means it happens on import. This is usually fine for
# standalone scripts, but be aware if this module were imported elsewhere without
# intending to parse args immediately.
args = parser.parse_args()

# Determine the target directory for the .env file
dotenv_dir_path = Path(args.dotenv_dir).expanduser() # Expand ~ to home directory

# Create the directory if it doesn't exist
try:
    dotenv_dir_path.mkdir(parents=True, exist_ok=True)
    logging.info(f"Ensured directory exists: {dotenv_dir_path}")
except OSError as e:
    logging.error(f"Error creating directory {dotenv_dir_path}: {e}")
    sys.exit(1)

# Construct the full path to the .env file
dotenv_path = dotenv_dir_path / ".env"

# Check if the .env file exists in the target directory
if not dotenv_path.is_file():
    logging.error(f"Required .env file not found at {dotenv_path}")
    logging.error("Please create the .env file with your TickTick credentials.")
    logging.error("Expected content:")
    logging.error("  TICKTICK_CLIENT_ID=your_client_id")
    logging.error("  TICKTICK_CLIENT_SECRET=your_client_secret")
    logging.error("  TICKTICK_REDIRECT_URI=your_redirect_uri")
    logging.error("  TICKTICK_USERNAME=your_ticktick_email")
    logging.error("  TICKTICK_PASSWORD=your_ticktick_password")
    sys.exit(1) # Exit if .env file is missing

# Load the required .env file
loaded = load_dotenv(override=True, dotenv_path=dotenv_path)
if loaded:
    logging.info(f"Successfully loaded environment variables from: {dotenv_path}")
else:
    # This case might indicate an issue reading the file even if it exists
    logging.error(f"Failed to load environment variables from {dotenv_path}. Check file permissions and format.")
    sys.exit(1)

# --- Environment Variable Loading --- #
# Load variables after dotenv has potentially populated them
CLIENT_ID = os.getenv("TICKTICK_CLIENT_ID")
CLIENT_SECRET = os.getenv("TICKTICK_CLIENT_SECRET")
REDIRECT_URI = os.getenv("TICKTICK_REDIRECT_URI")
USERNAME = os.getenv("TICKTICK_USERNAME")
PASSWORD = os.getenv("TICKTICK_PASSWORD") 