"""
Steam User Detection Utilities

Provides reliable detection of the currently logged-in Steam user by parsing
Steam's loginusers.vdf file instead of relying on directory modification times.
"""

import os
import logging
from typing import Optional

try:
    import vdf
    VDF_AVAILABLE = True
except ImportError:
    VDF_AVAILABLE = False

logger = logging.getLogger(__name__)


def get_logged_in_steam_user(steam_path: Optional[str] = None) -> Optional[str]:
    """
    Get the currently logged-in Steam user's account ID (userdata folder name).
    
    Uses loginusers.vdf with MostRecent flag as primary source,
    falls back to mtime-based detection (excluding user 0).
    
    Args:
        steam_path: Path to Steam installation (auto-detected if None)
        
    Returns:
        Account ID string (the folder name in userdata/) or None
    """
    if steam_path is None:
        steam_path = _find_steam_path()
    
    if not steam_path:
        logger.warning("[SteamUser] Could not find Steam installation path")
        return None
    
    # Try loginusers.vdf first (most reliable)
    user_id = _get_user_from_loginusers(steam_path)
    if user_id:
        logger.info(f"[SteamUser] Found logged-in user from loginusers.vdf: {user_id}")
        return user_id
    
    # Fallback to mtime-based detection (excluding user 0)
    user_id = _get_user_from_mtime(steam_path)
    if user_id:
        logger.info(f"[SteamUser] Fallback: Using mtime-based user detection: {user_id}")
        return user_id
    
    logger.error("[SteamUser] Could not detect logged-in Steam user")
    return None


def _find_steam_path() -> Optional[str]:
    """Find Steam installation directory"""
    possible_paths = [
        os.path.expanduser("~/.steam/steam"),
        os.path.expanduser("~/.local/share/Steam"),
    ]

    for path in possible_paths:
        if os.path.exists(os.path.join(path, "steamapps")):
            return path

    return None


def _get_user_from_loginusers(steam_path: str) -> Optional[str]:
    """
    Get the logged-in user from loginusers.vdf
    
    The file contains Steam64IDs, which we convert to account IDs (userdata folder names).
    """
    if not VDF_AVAILABLE:
        logger.debug("[SteamUser] vdf module not available, skipping loginusers.vdf")
        return None
    
    loginusers_path = os.path.join(steam_path, "config", "loginusers.vdf")
    
    if not os.path.exists(loginusers_path):
        logger.debug(f"[SteamUser] loginusers.vdf not found at {loginusers_path}")
        return None
    
    try:
        with open(loginusers_path, 'r', encoding='utf-8', errors='ignore') as f:
            data = vdf.load(f)
        
        users = data.get('users', {})
        
        # Find the user with MostRecent = "1"
        for steam64_id_str, user_info in users.items():
            if user_info.get('MostRecent') == '1':
                # Convert Steam64ID to account ID (lower 32 bits)
                try:
                    steam64_id = int(steam64_id_str)
                    account_id = steam64_id & 0xFFFFFFFF
                    
                    # Validate that this userdata folder actually exists
                    userdata_path = os.path.join(steam_path, "userdata", str(account_id))
                    if os.path.exists(userdata_path):
                        return str(account_id)
                    else:
                        logger.warning(f"[SteamUser] MostRecent user {account_id} folder doesn't exist")
                except ValueError:
                    logger.warning(f"[SteamUser] Invalid Steam64ID: {steam64_id_str}")
                    continue
        
        logger.debug("[SteamUser] No MostRecent user found in loginusers.vdf")
        
    except Exception as e:
        logger.warning(f"[SteamUser] Error reading loginusers.vdf: {e}")
    
    return None


def _get_user_from_mtime(steam_path: str) -> Optional[str]:
    """
    Fallback: Get the most recently active user by directory mtime.
    
    EXPLICITLY EXCLUDES user 0 which is a meta-directory.
    """
    userdata_path = os.path.join(steam_path, "userdata")
    
    if not os.path.exists(userdata_path):
        return None
    
    user_dirs = []
    for d in os.listdir(userdata_path):
        # Skip non-numeric directories
        if not d.isdigit():
            continue
        
        # CRITICAL: Skip user 0 - it's a meta-directory, not a real user
        if d == '0':
            logger.debug("[SteamUser] Skipping user 0 (meta-directory)")
            continue
        
        dir_path = os.path.join(userdata_path, d)
        if os.path.isdir(dir_path):
            mtime = os.path.getmtime(dir_path)
            user_dirs.append((d, mtime))
    
    if not user_dirs:
        return None
    
    # Sort by mtime descending, return most recent
    user_dirs.sort(key=lambda x: x[1], reverse=True)
    return user_dirs[0][0]


def validate_user_id(steam_path: str, user_id: str) -> bool:
    """
    Validate that a user ID has a valid userdata directory with shortcuts config.
    
    Args:
        steam_path: Path to Steam installation
        user_id: The account ID to validate
        
    Returns:
        True if the user has a valid config directory
    """
    if user_id == '0':
        return False
    
    config_path = os.path.join(steam_path, "userdata", user_id, "config")
    return os.path.exists(config_path)
