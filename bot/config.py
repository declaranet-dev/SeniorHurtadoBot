"""Carga de configuración desde el archivo .env."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

log = logging.getLogger("seniorhurtadobot.config")

COMMAND_PREFIX = "!"

# Bienvenida
WELCOME_ROLE_NAME = "KikinJR"

# Registro
GUILD_ROLE_NAME = "chavo"  # miembros del gremio Vecindad del Chavo
OUTSIDER_ROLE_NAME = "Chusma"  # jugadores de otros gremios
REGISTROS_FILE = ROOT_DIR / "data" / "registros.json"

# Eventos
EVENT_ADMIN_ROLE_NAME = "Admin"  # quién puede crear eventos
EVENT_DURATION_HOURS = 2  # Discord exige una hora de fin en eventos externos
EVENTOS_FILE = ROOT_DIR / "data" / "eventos.json"

# Killbot
KILLBOT_FILE = ROOT_DIR / "data" / "killbot.json"
KILLBOT_INTERVAL_SECONDS = 15  # cada cuánto se consulta el feed general de Albion
KILLBOT_GLOBAL_PAGES = 4  # páginas de 51 eventos del feed general por consulta
GUCCI_MIN_FAME = 5_000_000  # fama mínima para que una kill de todo Albion salga en gucci kills
KILLBOT_GUILD_FEED_EVERY = 4  # cada cuántas vueltas se revisa la lista del gremio (respaldo)

# Battle board
BATTLES_FILE = ROOT_DIR / "data" / "battles.json"
BATTLE_INTERVAL_SECONDS = 180
BATTLE_SETTLE_MINUTES = 10  # se espera a que la batalla termine del todo
EVENT_ROLES = {  # opciones de "Roles necesarios" y su emoji en los botones
    "Tank": "🛡️",
    "Offtank": "🪓",
    "Stopper": "⛓️",
    "Healer": "💚",
    "Healer de Back": "🌿",
    "Support": "✨",
    "DPS Melee": "⚔️",
    "DPS Rango": "🏹",
    "Scout": "👁️",
}


def get_token() -> str:
    """Devuelve DISCORD_TOKEN desde .env. Nunca se imprime."""
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Falta DISCORD_TOKEN en el archivo .env")
    return token


def _get_channel_id(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    if not value.isdigit():
        log.error("%s no es un número válido: %r", name, value)
        return None
    return int(value)


def get_welcome_channel_id() -> int | None:
    """Devuelve WELCOME_CHANNEL_ID desde .env, o None si no está configurado."""
    return _get_channel_id("WELCOME_CHANNEL_ID")


def get_registro_channel_id() -> int | None:
    """Devuelve REGISTRO_CHANNEL_ID desde .env, o None si no está configurado."""
    return _get_channel_id("REGISTRO_CHANNEL_ID")


def get_killbot_channel_id() -> int | None:
    """Canal de kills (alguien del gremio mata): KILLBOT_CHANNEL_ID en .env."""
    return _get_channel_id("KILLBOT_CHANNEL_ID")


def get_gucci_channel_id() -> int | None:
    """Canal de gucci kills (kills de todo Albion con mucha fama): GUCCI_CHANNEL_ID en .env."""
    return _get_channel_id("GUCCI_CHANNEL_ID")


def get_killbot_deaths_channel_id() -> int | None:
    """Canal de muertes (matan a alguien del gremio): KILLBOT_DEATHS_CHANNEL_ID en .env."""
    return _get_channel_id("KILLBOT_DEATHS_CHANNEL_ID")


def get_killbot_guild_id() -> str | None:
    """ID del gremio de Albion a seguir (KILLBOT_GUILD_ID en .env)."""
    return os.getenv("KILLBOT_GUILD_ID", "").strip() or None


def get_battleboard_channel_id() -> int | None:
    """Canal del resumen de batallas: BATTLEBOARD_CHANNEL_ID en .env."""
    return _get_channel_id("BATTLEBOARD_CHANNEL_ID")


def get_battle_min_players() -> int:
    """Jugadores mínimos para publicar una batalla (BATTLE_MIN_PLAYERS, por defecto 10)."""
    value = os.getenv("BATTLE_MIN_PLAYERS", "10").strip()
    return int(value) if value.isdigit() else 10


def get_killbot_min_fame() -> int:
    """Fama mínima para publicar una kill/muerte (KILLBOT_MIN_FAME, por defecto 0)."""
    value = os.getenv("KILLBOT_MIN_FAME", "0").strip()
    return int(value) if value.isdigit() else 0


def get_eventos_channel_id() -> int | None:
    """Devuelve EVENTOS_CHANNEL_ID desde .env, o None si no está configurado."""
    return _get_channel_id("EVENTOS_CHANNEL_ID")
