"""Cliente de la API oficial de Albion Online (gameinfo).

La API de albion-online-data.com solo tiene datos de mercado; los jugadores
y gremios están en la API gameinfo de Sandbox Interactive.
"""

import asyncio
import logging

import aiohttp

log = logging.getLogger("seniorhurtadobot.albion")

SERVERS = {
    "americas": "https://gameinfo.albiononline.com",
    "europe": "https://gameinfo-ams.albiononline.com",
    "asia": "https://gameinfo-sgp.albiononline.com",
}


class AlbionAPIError(Exception):
    """La API no respondió o devolvió algo inesperado."""


def base_url(server: str = "americas") -> str:
    if server not in SERVERS:
        log.error("Servidor de Albion %r no válido; uso americas", server)
        server = "americas"
    return SERVERS[server]


async def _get_json(path: str, params: dict, server: str = "americas") -> dict:
    url = base_url(server) + path
    timeout = aiohttp.ClientTimeout(total=30)  # las consultas por gremio a veces tardan
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


async def get_recent_events(pages: int = 3, server: str = "americas") -> list[dict]:
    """Últimos eventos de todo el servidor de Albion (feed general, al día)."""
    events = []
    for page in range(pages):
        data = await _get_json("/api/gameinfo/events", {"limit": 51, "offset": page * 51}, server)
        if not isinstance(data, list):
            raise AlbionAPIError("respuesta inesperada en /events")
        events.extend(data)
    return events


async def get_guild_events(guild_id: str, limit: int = 50, server: str = "americas") -> list[dict]:
    """Últimas kills y muertes de un gremio (las más recientes primero)."""
    data = await _get_json("/api/gameinfo/events",
                           {"guildId": guild_id, "limit": limit, "offset": 0}, server)
    if not isinstance(data, list):
        raise AlbionAPIError("respuesta inesperada en /events")
    return data


async def get_guild_battles(guild_id: str, limit: int = 20, server: str = "americas") -> list[dict]:
    """Últimas batallas en las que participó un gremio (más recientes primero)."""
    data = await _get_json("/api/gameinfo/battles", {
        "guildId": guild_id, "limit": limit, "offset": 0, "sort": "recent",
    }, server)
    if not isinstance(data, list):
        raise AlbionAPIError("respuesta inesperada en /battles")
    return data


async def get_battle_events(battle_id: int, server: str = "americas", max_pages: int = 10) -> list[dict]:
    """Kills de una batalla (con el daño y la curación de cada participante)."""
    events = []
    for page in range(max_pages):
        data = await _get_json(f"/api/gameinfo/events/battle/{battle_id}",
                               {"offset": page * 51, "limit": 51}, server)
        if not isinstance(data, list):
            raise AlbionAPIError("respuesta inesperada en /events/battle")
        events.extend(data)
        if len(data) < 51:
            break
    return events


async def get_player(player_id: str, server: str = "americas") -> dict:
    """Ficha completa de un jugador (fama PvP, PvE, recolección, crafteo...)."""
    return await _get_json(f"/api/gameinfo/players/{player_id}", {}, server)


async def get_player_events(player_id: str, kind: str, server: str = "americas") -> list[dict]:
    """Últimas kills (kind="kills") o muertes (kind="deaths") de un jugador."""
    data = await _get_json(f"/api/gameinfo/players/{player_id}/{kind}", {}, server)
    if not isinstance(data, list):
        raise AlbionAPIError(f"respuesta inesperada en /players/{kind}")
    return data


async def get_guild(guild_id: str, server: str = "americas") -> dict:
    """Datos de un gremio (nombre, alianza...)."""
    return await _get_json(f"/api/gameinfo/guilds/{guild_id}", {}, server)


async def find_player(name: str, server: str = "americas") -> dict | None:
    """Busca un jugador por nombre exacto (sin distinguir mayúsculas).

    Devuelve el dict de la API (Name, GuildName, AllianceName, ...) o None.
    """
    data = await _get_json("/api/gameinfo/search", {"q": name}, server)
    for player in data.get("players") or []:
        if player.get("Name", "").lower() == name.lower():
            return player
    return None


async def find_guild(name: str, server: str = "americas") -> list[dict]:
    """Gremios cuyo nombre coincide con la búsqueda (exactos primero)."""
    data = await _get_json("/api/gameinfo/search", {"q": name}, server)
    guilds = data.get("guilds") or []
    return sorted(guilds, key=lambda g: g.get("Name", "").lower() != name.lower())
