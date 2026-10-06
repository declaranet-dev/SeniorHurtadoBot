"""Bienvenida: asigna el rol de nuevos y saluda al miembro (configurable con /configuracion)."""

import logging
import random

import discord
from discord.ext import commands

from bot import settings

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

ROLE_FOOTER = "\n\nYa tienes tu rol {rol}.\nAhora comienza la aventura. ⚔️🏠"


def pick_welcome_message(mention: str, role_name: str | None) -> str:
    message = random.choice(WELCOME_MESSAGES).format(usuario=mention)
    if role_name:
        message += ROLE_FOOTER.format(rol=role_name)
    return message


def role_problem(guild: discord.Guild, role: discord.Role | None) -> str | None:
    """Motivo por el que el bot no puede asignar el rol, o None."""
    if role is None:
        return "no hay rol de nuevos configurado (/configuracion)"
    me = guild.me
    if not me.guild_permissions.manage_roles:
        return "el bot no tiene el permiso 'Gestionar roles'"
    if role >= me.top_role:
        return f"el rol {role.name} está por encima del rol del bot"
    return None


class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        """Revisa al arrancar que la bienvenida pueda funcionar en cada servidor."""
        for guild in self.bot.guilds:
            if not settings.get(guild.id)["canal_bienvenida"]:
                continue  # bienvenida sin configurar en este servidor
            problem = role_problem(guild, settings.role(guild, "rol_nuevo"))
            if problem:
                log.error("[WELCOME] %s: %s", guild.name, problem)
            channel = settings.channel(guild, "canal_bienvenida")
            log.info("[WELCOME] %s: canal #%s", guild.name, getattr(channel, "name", "no encontrado"))
            # Quien ya tiene rol de miembro u otro gremio no debe seguir con el de nuevos.
            for member in guild.members:
                await self._drop_new_role(member)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Si alguien recibe el rol de miembro o de otro gremio (por /registro o
        a mano), se le quita el rol de nuevos."""
        if before.roles != after.roles:
            await self._drop_new_role(after)

    async def _drop_new_role(self, member: discord.Member):
        guild = member.guild
        new_role = settings.role(guild, "rol_nuevo")
        if new_role is None or new_role not in member.roles:
            return
        final_roles = [settings.role(guild, k) for k in ("rol_miembro", "rol_externo")]
        if not any(r is not None and r in member.roles for r in final_roles):
            return
        if role_problem(guild, new_role):
            return  # el bot no puede quitarlo (permisos o jerarquía); ya se avisa al arrancar
        try:
            await member.remove_roles(new_role, reason="Ya tiene rol de miembro u otro gremio")
            log.info("[WELCOME] %s: quitado %s a %s", guild.name, new_role.name, member)
        except discord.HTTPException as e:
            log.error("[WELCOME] No se pudo quitar %s a %s: %s", new_role.name, member, e)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        log.info("[WELCOME] %s: nuevo miembro %s (id %s)", guild.name, member, member.id)
        # Cada paso maneja sus propios errores: si falla el rol, igual se
        # da la bienvenida; si falla la bienvenida, el rol se queda puesto.
        role = settings.role(guild, "rol_nuevo")
        assigned = await self._assign_role(member, role)
        await self._send_welcome(member, role.name if assigned else None)

    async def _assign_role(self, member: discord.Member, role: discord.Role | None) -> bool:
        if role is None:
            return False  # sin rol configurado: solo se da la bienvenida
        problem = role_problem(member.guild, role)
        if problem:
            log.error("[WELCOME] ERROR: %s", problem)
            return False
        try:
            await member.add_roles(role, reason="Bienvenida automática")
        except discord.HTTPException as e:
            log.error("[WELCOME] ERROR: No se pudo asignar %s: %s", role.name, e)
            return False
        log.info("[WELCOME] Rol %s asignado correctamente", role.name)
        return True

    async def _send_welcome(self, member: discord.Member, role_name: str | None):
        channel = settings.channel(member.guild, "canal_bienvenida")
        if channel is None:
            return  # bienvenida sin configurar en este servidor
        try:
            await channel.send(pick_welcome_message(member.mention, role_name))
        except discord.HTTPException as e:
            log.error("[WELCOME] ERROR: No se pudo enviar la bienvenida en #%s: %s", channel.name, e)
            return
        log.info("[WELCOME] Mensaje enviado correctamente en #%s", channel.name)


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
