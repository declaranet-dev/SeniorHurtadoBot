"""Constantes del bot y lectura del token desde .env.

Lo que cambia de un servidor de Discord a otro (canales, roles, gremio)
se configura con /configuracion y se guarda en data/servidores.json
(ver bot/settings.py).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

COMMAND_PREFIX = "!"

# Registro
REGISTROS_FILE = ROOT_DIR / "data" / "registros.json"

# Eventos
EVENT_DURATION_HOURS = 2  # Discord exige una hora de fin en eventos externos
EVENT_DELETE_AFTER_HOURS = 8  # el anuncio se borra estas horas después de la hora del evento
EVENTOS_FILE = ROOT_DIR / "data" / "eventos.json"
# Tipos de contenido: emoji y cantidades de jugadores que puede elegir el creador.
EVENT_CONTENT = {
    "ZvZ": ("⚔️", [10, 20, 40, 80]),
    "Avaloniana": ("🏛️", list(range(10, 21))),
    "Buffo Avalon": ("✨", list(range(5, 21))),
    "Maz Azules": ("🔵", list(range(1, 6))),
    "Cofres Dorados": ("💰", list(range(1, 8))),
    "Gankeo": ("🗡️", [8, 9]),
    "HCE": ("💀", [5]),
    "OpenWorld": ("🌍", list(range(10, 16))),
    "Dragones": ("🐉", list(range(10, 21))),
    "Otro": ("📌", [5, 10, 15, 20, 25, 30, 40, 50, 80]),
}
SLOT_CALLER_ROLE = "Asignado por Caller"  # rol por defecto de cada lugar
EVENT_ROLES = {  # roles que se pueden poner en cada lugar, con su emoji
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

# Killbot
KILLBOT_FILE = ROOT_DIR / "data" / "killbot.json"
KILLBOT_INTERVAL_SECONDS = 15  # cada cuánto se consulta el feed general de Albion
KILLBOT_GLOBAL_PAGES = 4  # páginas de 51 eventos del feed general por consulta
KILLBOT_GUILD_FEED_EVERY = 4  # cada cuántas vueltas se revisa la lista del gremio (respaldo)

# Battle board
BATTLES_FILE = ROOT_DIR / "data" / "battles.json"
BATTLE_INTERVAL_SECONDS = 180
BATTLE_SETTLE_MINUTES = 10  # se espera a que la batalla termine del todo


def get_token() -> str:
    """Devuelve DISCORD_TOKEN desde .env. Nunca se imprime."""
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Falta DISCORD_TOKEN en el archivo .env")
    return token
