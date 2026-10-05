"""Cliente de la API oficial de Albion Online (gameinfo).

La API de albion-online-data.com solo tiene datos de mercado; los jugadores
y gremios están en la API gameinfo de Sandbox Interactive.
"""

import asyncio
import logging
import os

import aiohttp

log = logging.getLogger("seniorhurtadobot.albion")

SERVERS = {
    "americas": "https://gameinfo.albiononline.com",
    "europe": "https://gameinfo-ams.albiononline.com",
    "asia": "https://gameinfo-sgp.albiononline.com",
}


class AlbionAPIError(Exception):
    """La API no respondió o devolvió algo inesperado."""


def base_url() -> str:
    server = os.getenv("ALBION_SERVER", "americas").strip().lower()
    if server not in SERVERS:
        log.error("ALBION_SERVER=%r no es válido; uso americas", server)
        server = "americas"
    return SERVERS[server]


async def _get_json(path: str, params: dict) -> dict:
    url = base_url() + path
    timeout = aiohttp.ClientTimeout(total=10)
    last_error: Exception | None = None
    # La API de Albion falla a ratos: se reintenta una vez.
    for attempt in range(2):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        raise AlbionAPIError(f"HTTP {resp.status}")
                    if "json" not in resp.content_type:
                        # P. ej. una página de bloqueo de un firewall.
                        raise AlbionAPIError(f"respuesta no JSON ({resp.content_type})")
                    return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, AlbionAPIError) as e:
            last_error = e
            log.warning("[ALBION] Intento %d fallido en %s: %s", attempt + 1, path, e)
            await asyncio.sleep(1)
    raise AlbionAPIError(str(last_error))


async def get_recent_events(pages: int = 3) -> list[dict]:
    """Últimos eventos de todo el servidor (feed general, al día)."""
    events = []
    for page in range(pages):
        data = await _get_json("/api/gameinfo/events", {"limit": 51, "offset": page * 51})
        if not isinstance(data, list):
            raise AlbionAPIError("respuesta inesperada en /events")
        events.extend(data)
    return events


async def get_guild_events(guild_id: str, limit: int = 50) -> list[dict]:
    """Últimas kills y muertes de un gremio (las más recientes primero)."""
    data = await _get_json("/api/gameinfo/events",
                           {"guildId": guild_id, "limit": limit, "offset": 0})
    if not isinstance(data, list):
        raise AlbionAPIError("respuesta inesperada en /events")
    return data


async def get_guild_battles(guild_id: str, limit: int = 20) -> list[dict]:
    """Últimas batallas en las que participó un gremio (más recientes primero)."""
    data = await _get_json("/api/gameinfo/battles", {
        "guildId": guild_id, "limit": limit, "offset": 0, "sort": "recent",
    })
    if not isinstance(data, list):
        raise AlbionAPIError("respuesta inesperada en /battles")
    return data


async def find_player(name: str) -> dict | None:
    """Busca un jugador por nombre exacto (sin distinguir mayúsculas).

    Devuelve el dict de la API (Name, GuildName, AllianceName, ...) o None.
    """
    data = await _get_json("/api/gameinfo/search", {"q": name})
    for player in data.get("players") or []:
        if player.get("Name", "").lower() == name.lower():
            return player
    return None
