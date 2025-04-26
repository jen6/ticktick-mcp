import logging
from typing import Optional

# TickTick library imports
from ticktick.api import TickTickClient
from ticktick.oauth2 import OAuth2

# Import config variables and paths
from .config import CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, USERNAME, PASSWORD, dotenv_dir_path

# Global client variable
ticktick_client: Optional[TickTickClient] = None

def initialize_ticktick_client():
    """Initializes the global TickTick client."""
    global ticktick_client
    if ticktick_client:
        logging.info("TickTick client already initialized.")
        return ticktick_client

    if not all([CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, USERNAME, PASSWORD]):
        logging.error("TickTick credentials not found in environment variables (checked in config.py). Ensure .env file is correct.")
        # Set client to None explicitly
        ticktick_client = None
        return None

    try:
        logging.info(f"Initializing OAuth2 with cache path: {dotenv_dir_path / '.token-oauth'}")
        auth_client = OAuth2(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            cache_path=dotenv_dir_path / ".token-oauth" # Use path from config
        )
        # Note: The following line might print directly to stdout/stderr during interactive auth flow
        # It's part of the underlying library, difficult to redirect cleanly here.
        auth_client.get_access_token() # This might trigger the interactive OAuth flow

        logging.info(f"Initializing TickTickClient with username: {USERNAME}")
        ticktick_client = TickTickClient(USERNAME, PASSWORD, auth_client)
        logging.info("TickTick client initialized successfully.")
        return ticktick_client
    except Exception as e:
        logging.error(f"Error initializing TickTick client: {e}", exc_info=True) # Log traceback
        ticktick_client = None # Ensure client is None if init fails
        return None 