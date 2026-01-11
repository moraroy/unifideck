#!/usr/bin/env python3
"""
Compatibility Cache Test Script

Standalone script to test Steam Store → ProtonDB → Deck Verified lookups
before integrating into main.py.

Usage:
    python3 compat_cache.py

Output:
    - compat_cache.json in ~/.local/share/unifideck/
    - Logs to ~/.local/share/unifideck/compat_test.log
"""

import asyncio
import aiohttp
import json
import os
import logging
import ssl
import time
from pathlib import Path
from typing import Dict, Optional, Any, List, Tuple

# Setup logging to user data directory
UNIFIDECK_DATA_DIR = Path.home() / ".local" / "share" / "unifideck"
UNIFIDECK_DATA_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = UNIFIDECK_DATA_DIR / "compat_test.log"
CACHE_FILE = UNIFIDECK_DATA_DIR / "compat_cache.json"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ProtonDB tier types
PROTONDB_TIERS = ['platinum', 'gold', 'silver', 'bronze', 'borked', 'pending', 'native']

# User-Agent to avoid being blocked by APIs
USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

# Steam Deck compatibility categories from Steam API
DECK_CATEGORIES = {
    1: 'unknown',
    2: 'unsupported',
    3: 'playable',
    4: 'verified'
}


def load_compat_cache() -> Dict[str, Dict]:
    """Load compatibility cache from JSON file."""
    try:
        if CACHE_FILE.exists():
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Error loading compat cache: {e}")
    return {}


def save_compat_cache(cache: Dict[str, Dict]) -> bool:
    """Save compatibility cache to JSON file."""
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=2)
        logger.info(f"Saved {len(cache)} entries to {CACHE_FILE}")
        return True
    except Exception as e:
        logger.error(f"Error saving compat cache: {e}")
        return False


async def search_steam_store(session: aiohttp.ClientSession, title: str) -> Optional[Dict]:
    """
    Search Steam Store for a game by title.
    
    Returns:
        {"appId": int, "name": str} or None if not found
    """
    try:
        url = f"https://store.steampowered.com/api/storesearch/?term={title}&cc=US"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                items = data.get('items', [])
                if items:
                    # Try exact match first
                    normalized_title = title.lower().strip()
                    for item in items:
                        if item.get('name', '').lower().strip() == normalized_title:
                            return {"appId": item['id'], "name": item['name']}
                    # Fall back to first result
                    return {"appId": items[0]['id'], "name": items[0]['name']}
    except asyncio.TimeoutError:
        logger.warning(f"Steam Store search timeout: {title}")
    except Exception as e:
        logger.error(f"Steam Store search error for '{title}': {e}")
    return None


async def fetch_protondb_rating(session: aiohttp.ClientSession, appid: int) -> Optional[str]:
    """
    Fetch ProtonDB rating for a Steam AppID.
    
    Returns:
        Tier string ('platinum', 'gold', etc.) or None
    """
    try:
        url = f"https://www.protondb.com/api/v1/reports/summaries/{appid}.json"
        headers = {'User-Agent': USER_AGENT}
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 200:
                data = await resp.json()
                tier = data.get('tier')
                if tier in PROTONDB_TIERS:
                    return tier
            elif resp.status == 404:
                # Normal - game not in ProtonDB
                return None
    except asyncio.TimeoutError:
        logger.warning(f"ProtonDB timeout for appid {appid}")
    except Exception as e:
        logger.debug(f"ProtonDB error for appid {appid}: {e}")
    return None


async def fetch_deck_verified(session: aiohttp.ClientSession, appid: int) -> str:
    """
    Fetch Steam Deck compatibility status.
    
    Returns:
        'verified', 'playable', 'unsupported', or 'unknown'
    """
    try:
        url = f"https://store.steampowered.com/saleaction/ajaxgetdeckappcompatibilityreport?nAppID={appid}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                category = data.get('results', {}).get('resolved_category', 1)
                return DECK_CATEGORIES.get(category, 'unknown')
    except asyncio.TimeoutError:
        logger.warning(f"Deck Verified timeout for appid {appid}")
    except Exception as e:
        logger.debug(f"Deck Verified error for appid {appid}: {e}")
    return 'unknown'


async def get_compat_for_title(
    session: aiohttp.ClientSession,
    title: str
) -> Tuple[str, Dict[str, Any]]:
    """
    Get full compatibility info for a game title.
    
    Returns:
        (normalized_title, {tier, deckVerified, steamAppId, timestamp})
    """
    normalized = title.lower().strip()
    
    # Step 1: Search Steam Store for AppID
    search_result = await search_steam_store(session, title)
    if not search_result:
        return (normalized, {
            "tier": None,
            "deckVerified": "unknown",
            "steamAppId": None,
            "timestamp": int(time.time())
        })
    
    appid = search_result["appId"]
    
    # Step 2: Fetch ProtonDB and Deck status in parallel
    tier, deck = await asyncio.gather(
        fetch_protondb_rating(session, appid),
        fetch_deck_verified(session, appid)
    )
    
    result = {
        "tier": tier,
        "deckVerified": deck,
        "steamAppId": appid,
        "timestamp": int(time.time())
    }
    
    logger.info(f"Compat: \"{title}\" -> AppID {appid}, tier={tier}, deck={deck}")
    return (normalized, result)


async def prefetch_compat(titles: List[str], batch_size: int = 10, delay_ms: int = 50) -> Dict[str, Dict]:
    """
    Prefetch compatibility info for a list of game titles.
    
    Args:
        titles: List of game title strings
        batch_size: Number of concurrent requests (default 10)
        delay_ms: Delay between batches in milliseconds (default 50)
    
    Returns:
        Dict mapping normalized title -> compat info
    """
    logger.info(f"Prefetching compatibility for {len(titles)} games...")
    
    # Load existing cache
    cache = load_compat_cache()
    
    # Filter out already cached titles (check by normalized key)
    titles_to_fetch = []
    for title in titles:
        normalized = title.lower().strip()
        if normalized not in cache:
            titles_to_fetch.append(title)
    
    logger.info(f"  {len(cache)} already cached, {len(titles_to_fetch)} to fetch")
    
    if not titles_to_fetch:
        return cache
    
    # Create SSL context that doesn't verify (same as main.py pattern)
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    
    connector = aiohttp.TCPConnector(ssl=ssl_context, limit=batch_size * 2)
    
    async with aiohttp.ClientSession(connector=connector) as session:
        processed = 0
        successful = 0
        
        for i in range(0, len(titles_to_fetch), batch_size):
            batch = titles_to_fetch[i:i + batch_size]
            
            # Fetch batch in parallel
            results = await asyncio.gather(
                *[get_compat_for_title(session, title) for title in batch],
                return_exceptions=True
            )
            
            # Process results
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Batch error: {result}")
                    continue
                
                normalized, compat = result
                cache[normalized] = compat
                processed += 1
                
                if compat.get("tier") or compat.get("deckVerified") != "unknown":
                    successful += 1
            
            # Save progress after each batch
            save_compat_cache(cache)
            
            # Log progress every 50 games or at end
            if processed % 50 == 0 or i + batch_size >= len(titles_to_fetch):
                logger.info(f"  Progress: {processed}/{len(titles_to_fetch)} ({successful} with ratings)")
            
            # Delay between batches
            if i + batch_size < len(titles_to_fetch):
                await asyncio.sleep(delay_ms / 1000)
    
    logger.info(f"Prefetch complete: {len(titles_to_fetch)} games, {successful} with ratings")
    return cache


# ============== TEST CODE ==============

async def main():
    """Test the compatibility lookup with sample games."""
    
    # Sample game titles (mix of well-known games for testing)
    test_titles = [
        "Marvel's Midnight Suns",
        "Trek to Yomi",
        "Tomb Raider I-III Remastered Starring Lara Croft",
        "Splinter Cell Chaos Theory",
        "DREDGE",
        "Baldur's Gate II: Enhanced Edition",
        "Surf World Series",
        "Sine Mora EX",
        "Amnesia: The Dark Descent",
        "The Academy: The First Riddle",
        "Alex Kidd in Miracle World DX",
        "Amnesia: Rebirth",
        "20 Minutes Till Dawn",
        "Aerial_Knight's Never Yield",
        "Arcade Paradise",
        "A Plague Tale: Innocence",
        "AK-xolotl: Together",
        "Among Us",
        "Astrea Six Sided Oracles",
        "Behind the Frame: The Finest Scenery",
    ]
    
    logger.info("=" * 60)
    logger.info("COMPATIBILITY CACHE TEST")
    logger.info("=" * 60)
    logger.info(f"Cache file: {CACHE_FILE}")
    logger.info(f"Log file: {LOG_FILE}")
    logger.info("")
    
    start_time = time.time()
    cache = await prefetch_compat(test_titles)
    elapsed = time.time() - start_time
    
    logger.info("")
    logger.info("=" * 60)
    logger.info("RESULTS SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Total time: {elapsed:.1f}s")
    logger.info(f"Total cached: {len(cache)} games")
    
    # Count by tier
    tier_counts = {}
    deck_counts = {}
    for entry in cache.values():
        tier = entry.get("tier") or "none"
        deck = entry.get("deckVerified") or "unknown"
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        deck_counts[deck] = deck_counts.get(deck, 0) + 1
    
    logger.info("")
    logger.info("ProtonDB Tiers:")
    for tier, count in sorted(tier_counts.items()):
        logger.info(f"  {tier}: {count}")
    
    logger.info("")
    logger.info("Steam Deck Status:")
    for status, count in sorted(deck_counts.items()):
        logger.info(f"  {status}: {count}")
    
    logger.info("")
    logger.info("Test complete!")


if __name__ == "__main__":
    asyncio.run(main())
