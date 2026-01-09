from langchain_core.tools import tool
from typing import Literal, Optional, Dict, Any


@tool
def check_config(
    config_data: Optional[Dict[str, Any]],
    user_status: Literal["query", "waiting_confirm", "accept"],
) -> Dict[str, Any]:
    """
    Normalize the current configuration and determine whether the user has confirmed it.

    This tool does NOT perform reasoning or validation.
    It is used to:
    1. Normalize / finalize the revised configuration.
    2. Indicate whether the configuration has been explicitly accepted by the user.

    Parameters
    ----------
    config_data : dict or None
        The current revised configuration proposed during the conversation.
        - If None, it indicates that no valid configuration has been formed yet,
          and the caller may use default values or continue querying missing fields.

    user_status : {"query", "waiting_confirm", "accept"}
        The current dialogue state:
        - "query": The configuration is incomplete or missing required fields.
        - "waiting_confirm": A candidate configuration exists but is awaiting user confirmation.
        - "accept": The user has explicitly confirmed and accepted the configuration.

    Returns
    -------
    dict
        {
            "confirm": bool,
            "revised_config": dict
        }

        - confirm:
            True only if user_status == "accept".
            Indicates that the configuration can be safely applied to the system state.
        - revised_config:
            The normalized configuration dictionary.
            If config_data is None, an empty dict is returned.
    """

    confirm = user_status == "accept"
    revised_config = config_data or {}

    return {
        "confirm": confirm,
        "revised_config": revised_config,
    }