"""Bienvenida del gremio: asigna el rol KikinJR y saluda al nuevo miembro."""

import logging
import random

import discord
from discord.ext import commands

from bot.config import WELCOME_ROLE_NAME, get_welcome_channel_id

log = logging.getLogger("seniorhurtadobot.welcome")

# {usuario} se reemplaza por la mención del nuevo miembro.
WELCOME_MESSAGES = [
    "🎉 ¡Tenia que se el chavo del 8! 🏠😂\n\nBienvenido, {usuario}. "
    "es que no te tienen paciencia.",
    "👀 ¡Se nos llenó la vecindad!\n\nBienvenido, {usuario}. Por ahora eres "
    "KikinJR... pero podrias a llegar a ser el chavo.",
    "🏠 ¡Otro inquilino para la vecindad!\n\nBienvenido, {usuario}. No te "
    "preocupes por la renta... don ramon no paga, pero hace buenas kills. ⚔️",
    "😂 ¡Andale el hijo de don barriga!\n\nBienvenido, {usuario}. SeniorHurtadoBot "
    "ya te vio entrar y te puso tu rol de KikinJR.",
    "🛢️ ¡Alguien se asomó por el barril!\n\nBienvenido, {usuario}. Aquí nadie "
    "se esconde cuando suena el llamado a la vecindad.",
    "🥪 ¡Pásale, pásale, que todavía quedan tortas de jamon!\n\nBienvenido, {usuario}. "
    "Eso sí, el loot se reparte parejo....",
    "📢 ¡Atención, vecinos! Llegó gente nueva al patio.\n\nBienvenido, "
    "{usuario}. y parece que trae la chafaldrana de la espiroqueta...",
    "🧹 ¡Barran el patio que hay visita!\n\nBienvenido, {usuario}. Ponte cómodo, "
    "pero no tanto: la próxima ZvZ no se juega sola. ⚔️",
    "🍹 ¡Llegaste justo a tiempo para el agua de jamaica que sabe a limon pero parece de tamarindo!\n\nBienvenido, "
    "{usuario}. Si alguien te pregunta, di que vienes recomendado por el bot.",
    "🎒 ¡Uy, uy, uy, un nuevo aventurero en la vecindad!\n\nBienvenido, "
    "{usuario}. Por ahora te toca KikinJR... si te quieres ganar un pesito, solo tienes que moverme la cola. 😎",
]

ROLE_FOOTER = "\n\nYa tienes tu rol KikinJR.\nAhora comienza la aventura. ⚔️🏠"


def pick_welcome_message(mention: str, role_assigned: bool) -> str:
    message = random.choice(WELCOME_MESSAGES).format(usuario=mention)
    if role_assigned:
        message += ROLE_FOOTER
    return message


class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        """Revisa al arrancar que la bienvenida pueda funcionar en cada servidor."""
        for guild in self.bot.guilds:
            role = discord.utils.get(guild.roles, name=WELCOME_ROLE_NAME)
            me = guild.me
            if role is None:
                log.error("[WELCOME] ERROR: El rol %s no existe en %s", WELCOME_ROLE_NAME, guild.name)
            elif not me.guild_permissions.manage_roles:
                log.error(
                    "[WELCOME] ERROR: El bot no tiene permisos suficientes en %s "
                    "(falta 'Gestionar roles')", guild.name,
                )
            elif role >= me.top_role:
                log.error(
                    "[WELCOME] ERROR: El rol %s está por encima del bot en %s",
                    WELCOME_ROLE_NAME, guild.name,
                )
            else:
                log.info("[WELCOME] Listo para asignar %s en %s", WELCOME_ROLE_NAME, guild.name)

            channel_id = get_welcome_channel_id()
            channel = guild.get_channel(channel_id) if channel_id else None
            if channel is None:
                log.error("[WELCOME] ERROR: Canal de bienvenida no encontrado (WELCOME_CHANNEL_ID)")
            else:
                log.info("[WELCOME] Canal de bienvenida: #%s", channel.name)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        log.info("[WELCOME] Nuevo miembro: %s (id %s)", member, member.id)
        # Cada paso maneja sus propios errores: si falla el rol, igual se
        # da la bienvenida; si falla la bienvenida, el rol se queda puesto.
        role_assigned = await self._assign_role(member)
        await self._send_welcome(member, role_assigned)

    async def _assign_role(self, member: discord.Member) -> bool:
        guild = member.guild
        role = discord.utils.get(guild.roles, name=WELCOME_ROLE_NAME)
        if role is None:
            log.error("[WELCOME] ERROR: El rol %s no existe", WELCOME_ROLE_NAME)
            return False

        me = guild.me
        if not me.guild_permissions.manage_roles:
            log.error(
                "[WELCOME] ERROR: El bot no tiene permisos suficientes "
                "(falta 'Gestionar roles')"
            )
            return False
        if role >= me.top_role:
            log.error(
                "[WELCOME] ERROR: El rol %s está por encima del bot "
                "(rol más alto del bot: %s). Sube el rol del bot por encima "
                "de %s en Ajustes del servidor > Roles.",
                WELCOME_ROLE_NAME, me.top_role.name, WELCOME_ROLE_NAME,
            )
            return False

        try:
            await member.add_roles(role, reason="Bienvenida automática")
        except discord.Forbidden:
            log.error("[WELCOME] ERROR: El bot no tiene permisos suficientes")
            return False
        except discord.HTTPException as e:
            log.error("[WELCOME] ERROR: No se pudo asignar %s: %s", WELCOME_ROLE_NAME, e)
            return False

        log.info("[WELCOME] Rol %s asignado correctamente", WELCOME_ROLE_NAME)
        return True

    async def _send_welcome(self, member: discord.Member, role_assigned: bool):
        channel_id = get_welcome_channel_id()
        if channel_id is None:
            log.error("[WELCOME] ERROR: WELCOME_CHANNEL_ID no está configurado en .env")
            return
        channel = member.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            log.error(
                "[WELCOME] ERROR: No encontré el canal de texto con id %s en este servidor",
                channel_id,
            )
            return

        try:
            await channel.send(pick_welcome_message(member.mention, role_assigned))
        except discord.Forbidden:
            log.error("[WELCOME] ERROR: El bot no puede escribir en #%s", channel.name)
            return
        except discord.HTTPException as e:
            log.error("[WELCOME] ERROR: No se pudo enviar la bienvenida: %s", e)
            return

        log.info("[WELCOME] Mensaje enviado correctamente en #%s", channel.name)


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
