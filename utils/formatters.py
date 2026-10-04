"""
Formatting helper functions.
"""


def format_large_number(number):
    """
    Convert large numbers into readable format.

    Example:
    1200000 -> 1.20M
    """

    if number is None:
        return "N/A"

    if number >= 1_000_000_000_000:
        return f"{number / 1_000_000_000_000:.2f}T"

    if number >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}B"

    if number >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"

    if number >= 1_000:
        return f"{number / 1_000:.2f}K"

    return f"{number:.2f}"


def get_currency_symbol(currency_code):
    """
    Return currency symbol.
    """

    currency_map = {
        "INR": "₹",
        "USD": "$",
        "EUR": "€",
        "GBP": "£",
        "JPY": "¥",
    }

    return currency_map.get(currency_code, currency_code + " ")


def format_price(price, currency="₹"):
    """
    Format price.
    """

    if price is None:
        return "N/A"

    return f"{currency}{price:,.2f}"


def format_percentage(value):
    """
    Format percentage.
    """

    if value is None:
        return "N/A"

    return f"{value:.2f}%"


def format_volume(volume):
    """
    Format trading volume.
    """

    if volume is None:
        return "N/A"

    return f"{int(volume):,}"