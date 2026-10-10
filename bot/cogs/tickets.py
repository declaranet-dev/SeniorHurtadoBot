"""Tickets de registro: un canal temporal y privado para cada jugador nuevo.

Al entrar al servidor se le abre un canal que solo ven él, el bot y el staff,
con las instrucciones y el botón de registro. Se borra solo poco después de
registrarse, si se va del servidor o si pasan demasiadas horas sin registrarse.
"""

import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

from bot import settings
from bot.config import TICKET_CLOSE_SECONDS, TICKET_MAX_HOURS, TICKETS_FILE

log = logging.getLogger("seniorhurtadobot.tickets")


def _load() -> dict:
    """{id del canal: {"servidor", "miembro", "creado", "cerrar"}}"""
    if TICKETS_FILE.exists():
        return json.loads(TICKETS_FILE.read_text(encoding="utf-8"))
    return {}


def _save(data: dict):
    TICKETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TICKETS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(TICKETS_FILE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_ticket(channel_id: int | None) -> bool:
    return str(channel_id) in _load()


def ticket_of(member: discord.Member) -> discord.TextChannel | None:
    """Ticket abierto de ese miembro en ese servidor, si existe."""
    for channel_id, ticket in _load().items():
        if ticket["miembro"] == member.id and ticket["servidor"] == member.guild.id:
            channel = member.guild.get_channel(int(channel_id))
            if channel is not None:
                return channel
    return None


def channel_name(member: discord.Member) -> str:
    """registro-<nombre> solo con letras, números y guiones."""
    text = unicodedata.normalize("NFKD", member.display_name)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return f"registro-{text or member.id}"[:90]


def instructions(member: discord.Member) -> str:
    return (
        f"👋 {member.mention}, este canal es **solo para ti**: aquí haces tu registro.\n\n"
        "**Cómo registrarte**\n"
        "1. Pulsa el botón **📋 Registrarme** de abajo (o escribe `/registro`).\n"
        "2. Pon tu **Nombre** tal como sale en Albion, tu **Gremio** y tu **Alianza** (opcional).\n"
        "3. El bot revisa tu personaje en Albion y te da tu rol.\n\n"
        "⚠️ El bot cambia tu apodo a tu nombre de personaje: escríbelo bien.\n"
        "Si vienes de parte de alguien o tienes dudas, escríbelo aquí después de registrarte.\n"
        "Este canal se borra solo cuando termines."
    )


async def open_ticket(member: discord.Member) -> discord.TextChannel | None:
    """Abre (o devuelve) el canal de registro del miembro. None si no se pudo."""
    from bot.cogs.registro import RegistroView  # aquí para evitar un import circular

    guild = member.guild
    config = settings.get(guild.id)
    registro = settings.channel(guild, "canal_registro")
    if registro is None or not config["tickets_registro"]:
        return None
    existing = ticket_of(member)
    if existing is not None:
        return existing
    if not guild.me.guild_permissions.manage_channels:
        log.error("[TICKETS] %s: el bot no tiene 'Gestionar canales'; no se abre ticket", guild.name)
        return None

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                            read_message_history=True, use_application_commands=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True,
                                              attach_files=True, manage_channels=True,
                                              read_message_history=True),
    }
    staff = settings.role(guild, "rol_admin_eventos")
    if staff is not None:
        overwrites[staff] = discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                        read_message_history=True)
    try:
        channel = await guild.create_text_channel(
            channel_name(member), category=registro.category, overwrites=overwrites,
            topic=f"Registro de {member} (se borra solo al terminar)",
            reason=f"Ticket de registro de {member}")
        await channel.send(instructions(member), view=RegistroView())
    except discord.HTTPException as e:
        log.error("[TICKETS] %s: no se pudo abrir el ticket de %s: %s", guild.name, member, e)
        return None

    data = _load()
    data[str(channel.id)] = {"servidor": guild.id, "miembro": member.id,
                             "creado": _now().isoformat(), "cerrar": None}
    _save(data)
    log.info("[TICKETS] %s: ticket #%s abierto para %s", guild.name, channel.name, member)
    return channel


async def registered(member: discord.Member, channel: discord.abc.Messageable | None):
    """El miembro terminó su registro: se programa el cierre de su ticket
    (también si se registró en el canal de registro normal)."""
    data = _load()
    ticket = data.get(str(getattr(channel, "id", None)))
    if ticket is None or ticket["miembro"] != member.id:
        channel = ticket_of(member)
        ticket = data.get(str(channel.id)) if channel is not None else None
    if ticket is None or ticket["cerrar"]:
        return
    ticket["cerrar"] = (_now() + timedelta(seconds=TICKET_CLOSE_SECONDS)).isoformat()
    _save(data)
    try:
        await channel.send(f"✅ Listo. Este canal se cerrará en {TICKET_CLOSE_SECONDS // 60} minutos.")
    except discord.HTTPException:
        pass


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.cleanup.start()

    async def cog_unload(self):
        self.cleanup.cancel()

    async def _delete(self, channel_id: str, why: str):
        channel = self.bot.get_channel(int(channel_id))
        if channel is not None:
            try:
                await channel.delete(reason=why)
                log.info("[TICKETS] #%s borrado: %s", channel.name, why)
            except discord.NotFound:
                pass
            except discord.HTTPException as e:
                log.error("[TICKETS] No se pudo borrar #%s: %s", channel.name, e)
                return  # se reintenta en la próxima vuelta
        data = _load()
        if data.pop(channel_id, None) is not None:
            _save(data)

    @tasks.loop(seconds=30)
    async def cleanup(self):
        now = _now()
        for channel_id, ticket in list(_load().items()):
            guild = self.bot.get_guild(ticket["servidor"])
            if guild is None or guild.unavailable:
                continue  # Discord aún no entrega ese servidor: no se toca nada
            if guild.get_channel(int(channel_id)) is None:
                await self._delete(channel_id, "el canal ya no existe")
            elif ticket["cerrar"] and datetime.fromisoformat(ticket["cerrar"]) <= now:
                await self._delete(channel_id, "registro completado")
            elif datetime.fromisoformat(ticket["creado"]) + timedelta(hours=TICKET_MAX_HOURS) <= now:
                await self._delete(channel_id, f"sin registrarse en {TICKET_MAX_HOURS} h")

    @cleanup.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Si se va del servidor sin registrarse, su ticket ya no hace falta."""
        for channel_id, ticket in list(_load().items()):
            if ticket["miembro"] == member.id and ticket["servidor"] == member.guild.id:
                await self._delete(channel_id, f"{member} salió del servidor")


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
