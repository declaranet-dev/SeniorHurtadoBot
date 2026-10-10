"""Configuración por servidor de Discord (la que se edita con /configuracion).

Cada servidor tiene sus propios canales, roles y gremio de Albion, guardados
en data/servidores.json. Así el mismo bot sirve para varios servidores.
"""

import json
import logging
import os
import threading

import discord

from bot.config import ROOT_DIR

log = logging.getLogger("seniorhurtadobot.settings")

SETTINGS_FILE = ROOT_DIR / "data" / "servidores.json"

# clave -> (tipo, texto que ve el usuario)
CHANNELS = {
    "canal_bienvenida": "Bienvenida",
    "canal_registro": "Registro",
    "canal_historial": "Historial de nuevos",
    "canal_eventos": "Eventos",
    "canal_kills": "Kills del gremio",
    "canal_muertes": "Muertes del gremio",
    "canal_batallas": "Battle board",
    "canal_gucci": "Gucci kills (todo Albion)",
}
ROLES = {
    "rol_nuevo": "Rol al entrar al servidor",
    "rol_miembro": "Rol para miembros del gremio",
    "rol_aliado": "Rol para la alianza del gremio",
    "rol_externo": "Rol para otros gremios",
    "rol_admin_eventos": "Rol que puede crear eventos",
}
DEFAULTS = {
    **{k: None for k in CHANNELS},
    **{k: None for k in ROLES},
    "gremio_id": None,  # ID del gremio en la API de Albion
    "gremio_nombre": None,
    "servidor_albion": "americas",  # americas, europe o asia
    "batalla_min_jugadores": 10,
    "batalla_min_gremio": 5,  # jugadores del gremio en la batalla para publicarla
    "gucci_min_fama": 5_000_000,
    "tickets_registro": True,  # abrir un canal temporal de registro a cada jugador nuevo
}

_lock = threading.Lock()


def _load_all() -> dict:
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    return {}


def _save_all(data: dict):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SETTINGS_FILE)


def get(guild_id: int) -> dict:
    """Configuración del servidor, con valores por defecto en lo que falte."""
    with _lock:
        stored = _load_all().get(str(guild_id), {})
    return {**DEFAULTS, **stored}


def update(guild_id: int, **values):
    with _lock:
        data = _load_all()
        data.setdefault(str(guild_id), {}).update(values)
        _save_all(data)


def configured_guild_ids() -> list[int]:
    with _lock:
        return [int(g) for g in _load_all()]


def channel(guild: discord.Guild, key: str) -> discord.TextChannel | None:
    channel_id = get(guild.id).get(key)
    found = guild.get_channel(channel_id) if channel_id else None
    return found if isinstance(found, discord.TextChannel) else None


def role(guild: discord.Guild, key: str) -> discord.Role | None:
    role_id = get(guild.id).get(key)
    return guild.get_role(role_id) if role_id else None


def migrate_from_env(guild: discord.Guild):
    """Pasa la configuración vieja del .env (un solo servidor) al nuevo sistema.

    Solo se aplica al servidor donde están los canales del .env, y una sola vez.
    """
    if str(guild.id) in {str(g) for g in configured_guild_ids()}:
        return
    env_channels = {
        "canal_bienvenida": "WELCOME_CHANNEL_ID",
        "canal_registro": "REGISTRO_CHANNEL_ID",
        "canal_eventos": "EVENTOS_CHANNEL_ID",
        "canal_kills": "KILLBOT_CHANNEL_ID",
        "canal_muertes": "KILLBOT_DEATHS_CHANNEL_ID",
        "canal_batallas": "BATTLEBOARD_CHANNEL_ID",
        "canal_gucci": "GUCCI_CHANNEL_ID",
    }
    values = {}
    for key, env in env_channels.items():
        raw = os.getenv(env, "").strip()
        if raw.isdigit() and guild.get_channel(int(raw)):
            values[key] = int(raw)
    if not values:
        return
    # Nombres de rol que el bot usaba antes de /configuracion.
    old_roles = {"rol_nuevo": "KikinJR", "rol_miembro": "chavo",
                 "rol_externo": "Chusma", "rol_admin_eventos": "Admin"}
    for key, name in old_roles.items():
        found = discord.utils.find(lambda r, n=name: r.name.lower() == n.lower(), guild.roles)
        if found:
            values[key] = found.id
    if os.getenv("KILLBOT_GUILD_ID", "").strip():
        values["gremio_id"] = os.getenv("KILLBOT_GUILD_ID").strip()
        values["gremio_nombre"] = "Vecindad Del Chavo"
    values["servidor_albion"] = os.getenv("ALBION_SERVER", "americas").strip().lower() or "americas"
    update(guild.id, **values)
    _migrate_data_files(guild.id)
    log.info("[CONFIG] Configuración del .env migrada a %s", guild.name)


def _migrate_data_files(guild_id: int):
    """Los archivos de datos de antes eran de un solo servidor: se pasan a este."""
    from bot.config import BATTLES_FILE, KILLBOT_FILE, REGISTROS_FILE

    def rewrite(path, convert):
        if path.exists():
            old = json.loads(path.read_text(encoding="utf-8"))
            new = convert(old)
            if new is not None:
                path.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")

    key = str(guild_id)
    # killbot.json: {"ultimo_evento", "vistos", ...} -> {"servidores": {id: {...}}}
    rewrite(KILLBOT_FILE, lambda d: None if "servidores" in d else {"servidores": {key: d}})
    # battles.json: {"publicadas": [...]} -> {"servidores": {id: [...]}}
    rewrite(BATTLES_FILE, lambda d: None if "servidores" in d
            else {"servidores": {key: d.get("publicadas", [])}})
    # registros.json: {usuario: {...}} -> {servidor: {usuario: {...}}}
    rewrite(REGISTROS_FILE, lambda d: {key: d}
            if d and all(isinstance(v, dict) and "jugador" in v for v in d.values()) else None)
