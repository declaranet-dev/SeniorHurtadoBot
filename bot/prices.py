"""Precios aproximados de objetos con la API de albion-online-data.com.

Se usa para estimar cuánto valía el equipo y el inventario de una kill.
Es una estimación: depende de lo que los jugadores reportan al proyecto.
"""

import asyncio
import logging
import statistics
import time
from datetime import datetime, timedelta, timezone

import aiohttp

log = logging.getLogger("seniorhurtadobot.prices")

PRICES_URL = "https://west.albion-online-data.com/api/v2/stats/prices/{}.json"
CITIES = "Caerleon,Bridgewatch,Lymhurst,Martlock,Thetford,FortSterling,Brecilien"
CACHE_SECONDS = 3600
MAX_AGE_DAYS = 30  # precios más viejos no se toman en cuenta
BATCH = 60  # objetos por consulta (límite de largo de la URL)

_cache: dict[tuple[str, int], tuple[float, int]] = {}  # (objeto, calidad) -> (hora, precio)


def _pick_price(rows: list[dict], quality: int) -> int:
    """Mediana de los precios de venta recientes, prefiriendo la misma calidad."""
    limit = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)

    def recent(r):
        if not r.get("sell_price_min"):
            return False
        date = datetime.fromisoformat(r["sell_price_min_date"][:19] + "+00:00")
        return date >= limit

    same = [r["sell_price_min"] for r in rows if r["quality"] == quality and recent(r)]
    any_q = [r["sell_price_min"] for r in rows if recent(r)]
    values = same or any_q
    return int(statistics.median(values)) if values else 0


async def _fetch(items: list[str]) -> list[dict]:
    url = PRICES_URL.format(",".join(items))
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, params={"locations": CITIES}) as resp:
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            return await resp.json()


async def get_prices(items: list[tuple[str, int]]) -> dict[tuple[str, int], int]:
    """Precio aproximado de cada (objeto, calidad). 0 si no hay datos."""
    now = time.time()
    result = {}
    missing = set()
    for key in items:
        cached = _cache.get(key)
        if cached and now - cached[0] < CACHE_SECONDS:
            result[key] = cached[1]
        else:
            missing.add(key)

    ids = sorted({item for item, _ in missing})
    rows_by_item: dict[str, list[dict]] = {}
    for i in range(0, len(ids), BATCH):
        try:
            for row in await _fetch(ids[i:i + BATCH]):
                rows_by_item.setdefault(row["item_id"], []).append(row)
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as e:
            log.warning("[PRECIOS] No se pudieron consultar precios: %s", e)
            return {key: result.get(key, 0) for key in items}

    for key in missing:
        price = _pick_price(rows_by_item.get(key[0], []), key[1])
        _cache[key] = (now, price)
        result[key] = price
    return result


def equipment_items(player: dict) -> list[tuple[str, int, int]]:
    """(objeto, calidad, cantidad) del equipo puesto."""
    out = []
    for item in (player.get("Equipment") or {}).values():
        if item:
            out.append((item["Type"], item.get("Quality") or 1, item.get("Count") or 1))
    return out


def inventory_items(player: dict) -> list[tuple[str, int, int]]:
    out = []
    for item in player.get("Inventory") or []:
        if item:
            out.append((item["Type"], item.get("Quality") or 1, item.get("Count") or 1))
    return out


async def value_of(items: list[tuple[str, int, int]]) -> int:
    prices = await get_prices([(t, q) for t, q, _ in items])
    return sum(prices.get((t, q), 0) * c for t, q, c in items)
