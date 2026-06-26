"""
Shared settings loader.

Reads the GitHub Enterprise name and token from a `settings.ini` file so they
do not need to be passed on the command line every time.

settings.ini format:
    [github]
    enterprise = YOUR_ENTERPRISE
    token = ghp_xxx

Copy `settings.ini.example` to `settings.ini` and fill in your values.
The `settings.ini` file is git-ignored to avoid committing your token.

Resolution order for each value (first non-empty wins):
    1. Command-line argument (--enterprise / --token)
    2. Environment variable (GITHUB_ENTERPRISE / GITHUB_TOKEN)
    3. settings.ini
"""

import configparser
import os
from pathlib import Path

DEFAULT_SETTINGS_FILE = "settings.ini"


def load_settings(settings_path: str = DEFAULT_SETTINGS_FILE) -> dict:
    """Load enterprise/token from a settings.ini file if it exists."""
    result = {"enterprise": None, "token": None}
    path = Path(settings_path)
    if not path.exists():
        return result

    parser = configparser.ConfigParser()
    try:
        parser.read(path)
    except configparser.Error as exc:
        print(f"Warning: Failed to parse '{settings_path}': {exc}")
        return result

    if parser.has_section("github"):
        result["enterprise"] = parser.get("github", "enterprise", fallback=None) or None
        result["token"] = parser.get("github", "token", fallback=None) or None

    return result


def resolve_credentials(
    enterprise_arg: str | None,
    token_arg: str | None,
    settings_path: str = DEFAULT_SETTINGS_FILE,
) -> tuple[str | None, str | None]:
    """Resolve enterprise and token from args, env vars, then settings.ini."""
    settings = load_settings(settings_path)

    enterprise = (
        enterprise_arg
        or os.environ.get("GITHUB_ENTERPRISE")
        or settings.get("enterprise")
    )
    token = (
        token_arg
        or os.environ.get("GITHUB_TOKEN")
        or settings.get("token")
    )

    return enterprise, token
