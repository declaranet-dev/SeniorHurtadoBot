"""Definición del bot y carga de extensiones (cogs)."""

import logging

import discord
from discord.ext import commands

from bot.config import COMMAND_PREFIX

log = logging.getLogger("seniorhurtadobot")

# Para agregar comandos nuevos (p. ej. Albion Online), crea un archivo en
# bot/cogs/ y añádelo a esta lista.
EXTENSIONS = [
    "bot.cogs.general",
    "bot.cogs.configuracion",
    "bot.cogs.welcome",
    "bot.cogs.registro",
    "bot.cogs.tickets",
    "bot.cogs.eventos",
    "bot.cogs.killbot",
    "bot.cogs.battleboard",
]


class SeniorHurtadoBot(commands.Bot):
    def __init__(self, message_content: bool = True, members: bool = True):
        intents = discord.Intents.default()
        # Necesario para leer comandos con prefijo en canales de servidor.
        # Sin él, los comandos solo funcionan por mensaje directo (DM).
        intents.message_content = message_content
        # Necesario para detectar nuevos miembros (on_member_join).
        intents.members = members
        super().__init__(
            command_prefix=COMMAND_PREFIX,
            intents=intents,
            help_command=None,
        )

    async def setup_hook(self):
        for ext in EXTENSIONS:
            await self.load_extension(ext)
            log.info("Extensión cargada: %s", ext)

    async def on_ready(self):
        log.info("Conectado como %s (id %s)", self.user, self.user.id)
        log.info("Servidores: %d", len(self.guilds))
        if not self.intents.message_content:
            log.warning(
                "Sin Message Content Intent: los comandos NO funcionan en "
                "canales del servidor, solo por mensaje directo (DM)."
            )
        if not self.intents.members:
            log.warning(
                "Sin Server Members Intent: la bienvenida automática NO funciona."
            )
        await self._sync_slash_commands()

    async def _sync_slash_commands(self):
        """Registra los comandos de barra (/) en cada servidor; así aparecen al instante."""
        if getattr(self, "_slash_synced", False):
            return
        self._slash_synced = True
        for guild in self.guilds:
            await self._sync_guild(guild)

    async def _sync_guild(self, guild: discord.Guild):
        self.tree.copy_global_to(guild=guild)
        try:
            synced = await self.tree.sync(guild=guild)
            log.info("Comandos / registrados en %s: %s",
                     guild.name, ", ".join(c.name for c in synced))
        except discord.Forbidden:
            log.error(
                "No pude registrar comandos / en %s: vuelve a invitar al bot "
                "con el permiso 'applications.commands'.", guild.name,
            )
        except discord.HTTPException as e:
            log.error("No pude registrar comandos / en %s: %s", guild.name, e)

    async def on_guild_join(self, guild: discord.Guild):
        log.info("Entré a un servidor nuevo: %s (id %s). Configúralo con /configuracion.",
                 guild.name, guild.id)
        await self._sync_guild(guild)

    async def on_command_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.CommandNotFound):
            return
        log.exception("Error en el comando %s", ctx.command, exc_info=error)
