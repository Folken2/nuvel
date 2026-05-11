"""
Date utility functions for the Data Analysis Agent.
"""

from datetime import datetime
from typing import Dict


def get_current_date_info() -> Dict[str, int]:
    """
    Get current date information (day, month, year).

    Returns:
        Dictionary with 'day', 'month', and 'year' keys
    """
    now = datetime.now()
    return {
        "day": now.day,
        "month": now.month,
        "year": now.year
    }


def format_current_date() -> str:
    """
    Format current date as a readable string.

    Returns:
        Formatted date string (e.g., "January 29, 2026")
    """
    now = datetime.now()
    return now.strftime("%B %d, %Y")
