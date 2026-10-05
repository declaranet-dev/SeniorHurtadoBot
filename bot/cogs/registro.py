"""Registro de jugadores: !registro asigna el rol según el gremio de Albion."""

import json
import logging
import re
import unicodedata
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from bot import albion
from bot.config import (
    GUILD_ROLE_NAME,
    OUTSIDER_ROLE_NAME,
    REGISTROS_FILE,
    WELCOME_ROLE_NAME,
    get_registro_channel_id,
)

log = logging.getLogger("seniorhurtadobot.registro")

# Nombres de gremio (ya normalizados) que cuentan como Vecindad del Chavo.
GUILD_ALIASES = {"vecindad del chavo", "vecindad"}


def normalize(text: str) -> str:
    """Minúsculas, sin acentos y con espacios simples."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.lower().split())


def parse_registro(text: str) -> tuple[str, str, str | None] | None:
    """Extrae (jugador, gremio, alianza) de los corchetes, o de 'a | b | c'."""
    parts = re.findall(r"\[([^\]]*)\]", text)
    if not parts:
        parts = text.split("|")
    parts = [p.strip() for p in parts]
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    alliance = parts[2] if len(parts) > 2 and parts[2] else None
    return parts[0], parts[1], alliance


def is_vecindad(guild_name: str) -> bool:
    return normalize(guild_name) in GUILD_ALIASES


def find_role(guild: discord.Guild, name: str) -> discord.Role | None:
    return discord.utils.find(lambda r: r.name.lower() == name.lower(), guild.roles)


def save_registro(member: discord.Member, player: str, guild_name: str,
                  alliance: str | None, role_name: str):
    REGISTROS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if REGISTROS_FILE.exists():
        data = json.loads(REGISTROS_FILE.read_text(encoding="utf-8"))
    data[str(member.id)] = {
        "discord": str(member),
        "jugador": player,
        "gremio": guild_name,
        "alianza": alliance,
        "rol": role_name,
        "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    tmp = REGISTROS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(REGISTROS_FILE)


async def set_roles(member: discord.Member, target_name: str,
                    other_name: str) -> str | None:
    """Pone el rol del gremio y quita KikinJR y el rol contrario.

    Devuelve None si todo fue bien, o un texto con el problema.
    """
    guild = member.guild
    me = guild.me
    target = find_role(guild, target_name)
    if target is None:
        log.error("[REGISTRO] ERROR: El rol %s no existe", target_name)
        return f"el rol {target_name} no existe en el servidor."
    if not me.guild_permissions.manage_roles:
        log.error("[REGISTRO] ERROR: El bot no tiene permisos suficientes")
        return "el bot no tiene permiso para gestionar roles."
    if target >= me.top_role:
        log.error("[REGISTRO] ERROR: El rol %s está por encima del bot", target_name)
        return f"el rol {target_name} está por encima del bot."

    to_remove = [
        r for r in (find_role(guild, WELCOME_ROLE_NAME), find_role(guild, other_name))
        if r is not None and r in member.roles and r < me.top_role
    ]
    try:
        await member.add_roles(target, reason="Registro")
        log.info("[REGISTRO] Rol %s asignado a %s", target.name, member)
        if to_remove:
            await member.remove_roles(*to_remove, reason="Registro")
            log.info("[REGISTRO] Roles quitados a %s: %s",
                     member, ", ".join(r.name for r in to_remove))
    except discord.Forbidden:
        log.error("[REGISTRO] ERROR: El bot no tiene permisos suficientes")
        return "el bot no tiene permiso para gestionar roles."
    except discord.HTTPException as e:
        log.error("[REGISTRO] ERROR: No se pudieron cambiar los roles: %s", e)
        return "Discord dio un error al cambiar los roles."
    return None


def same_guild(entered: str, official: str) -> bool:
    """El gremio escrito coincide con el oficial (o ambos son la Vecindad)."""
    if normalize(entered) == normalize(official):
        return True
    return is_vecindad(entered) and is_vecindad(official)


def check_player(player: str, guild_name: str, alliance: str | None,
                 api_player: dict | None) -> str | None:
    """Compara lo escrito con los datos de Albion. Devuelve el problema o None."""
    if api_player is None:
        return f"No encontré al jugador **{player}** en Albion Online."
    official_guild = api_player.get("GuildName") or ""
    if not official_guild:
        return f"**{api_player['Name']}** no está en ningún gremio según Albion."
    if not same_guild(guild_name, official_guild):
        return (f"**{api_player['Name']}** no está en el gremio **{guild_name}**. "
                f"Según Albion, su gremio es **{official_guild}**.")
    official_alliance = api_player.get("AllianceName") or ""
    if alliance and normalize(alliance) != normalize(official_alliance):
        actual = f"**{official_alliance}**" if official_alliance else "ninguna"
        return (f"El gremio **{official_guild}** no está en la alianza **{alliance}**. "
                f"Según Albion, su alianza es {actual}.")
    return None


async def do_registro(member: discord.Member, player: str, guild_name: str,
                      alliance: str | None) -> str:
    """Registra al jugador, ajusta sus roles y devuelve el mensaje de respuesta."""
    log.info("[REGISTRO] %s: jugador=%r gremio=%r alianza=%r",
             member, player, guild_name, alliance)

    # Validación con la API de Albion: solo se registra si el jugador existe
    # y está en el gremio que escribió. Se guardan los nombres oficiales.
    try:
        api_player = await albion.find_player(player)
    except albion.AlbionAPIError as e:
        log.error("[REGISTRO] ERROR: La API de Albion no responde: %s", e)
        return ("⚠️ No pude verificar tu jugador porque la API de Albion no responde. "
                "Inténtalo de nuevo en unos minutos.")
    problem = check_player(player, guild_name, alliance, api_player)
    if problem:
        log.info("[REGISTRO] Rechazado %s: %s", member, problem)
        return f"❌ {problem}\nRevisa que estén bien escritos e inténtalo de nuevo."
    player = api_player["Name"]
    guild_name = api_player["GuildName"]
    alliance = api_player.get("AllianceName") or None

    chavo = is_vecindad(guild_name)
    target_name = GUILD_ROLE_NAME if chavo else OUTSIDER_ROLE_NAME
    other_name = OUTSIDER_ROLE_NAME if chavo else GUILD_ROLE_NAME

    role_error = await set_roles(member, target_name, other_name)
    nick_error = await set_nickname(member, player)
    save_registro(member, player, guild_name, alliance, target_name)

    lines = [
        f"✅ **Registro completado** — {member.mention}",
        f"Jugador Albion: **{player}**",
        f"Gremio: **{guild_name}**",
        f"Alianza: **{alliance or '—'}**",
    ]
    if role_error:
        lines.append(f"⚠️ No pude darte tu rol: {role_error} Avisa a un admin.")
    elif chavo:
        lines.append(f"🏠 ¡Eres de la vecindad! Ya tienes tu rol **{target_name}**.")
    else:
        lines.append(f"👀 No eres de la vecindad... te tocó el rol **{target_name}**.")
    if nick_error:
        lines.append(f"⚠️ No pude cambiarte el apodo: {nick_error}")
    else:
        lines.append(f"🏷️ Tu apodo en el servidor ahora es **{player}**.")
    return "\n".join(lines)


async def set_nickname(member: discord.Member, player: str) -> str | None:
    """Cambia el apodo del miembro al nombre de su PJ. Devuelve el problema o None."""
    guild = member.guild
    me = guild.me
    if member.nick == player:
        return None
    if member.id == guild.owner_id:
        log.warning("[REGISTRO] No se puede cambiar el apodo del dueño del servidor (%s)", member)
        return "Discord no deja que un bot cambie el apodo del dueño del servidor. Cámbialo tú a mano."
    if not me.guild_permissions.manage_nicknames:
        log.error("[REGISTRO] ERROR: El bot no tiene permiso 'Gestionar apodos'")
        return "el bot no tiene permiso para gestionar apodos."
    if member.top_role >= me.top_role:
        log.error("[REGISTRO] ERROR: %s tiene un rol igual o más alto que el bot", member)
        return "tienes un rol igual o más alto que el bot."
    try:
        await member.edit(nick=player, reason="Registro")
    except discord.Forbidden:
        log.error("[REGISTRO] ERROR: Sin permiso para cambiar el apodo de %s", member)
        return "el bot no tiene permiso para cambiar tu apodo."
    except discord.HTTPException as e:
        log.error("[REGISTRO] ERROR: No se pudo cambiar el apodo de %s: %s", member, e)
        return "Discord dio un error al cambiar el apodo."
    log.info("[REGISTRO] Apodo de %s cambiado a %s", member, player)
    return None


def wrong_channel(channel_id_used: int | None) -> str | None:
    """Mensaje de aviso si el registro se intenta fuera del canal configurado."""
    channel_id = get_registro_channel_id()
    if channel_id and channel_id_used != channel_id:
        return f"El registro se hace en <#{channel_id}>."
    return None


class RegistroModal(discord.ui.Modal, title="Registro en la vecindad"):
    jugador = discord.ui.TextInput(label="Nombre PJ (Albion)", max_length=32)
    gremio = discord.ui.TextInput(label="Gremio", max_length=64)
    alianza = discord.ui.TextInput(
        label="Alianza (opcional)", required=False, max_length=64,
        placeholder="Puedes dejarlo en blanco",
    )

    async def on_submit(self, interaction: discord.Interaction):
        # Consultar la API puede tardar: se avisa a Discord para no pasar de 3 s.
        await interaction.response.defer(thinking=True)
        text = await do_registro(
            interaction.user,
            self.jugador.value.strip(),
            self.gremio.value.strip(),
            self.alianza.value.strip() or None,
        )
        await interaction.followup.send(text)


class RegistroView(discord.ui.View):
    """Botón que abre el formulario. Persistente: sigue funcionando tras reiniciar."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Registrarme", emoji="📋",
                       style=discord.ButtonStyle.primary,
                       custom_id="seniorhurtadobot:registro")
    async def open_form(self, interaction: discord.Interaction, button: discord.ui.Button):
        if (warning := wrong_channel(interaction.channel_id)):
            await interaction.response.send_message(warning, ephemeral=True)
            return
        await interaction.response.send_modal(RegistroModal())


class Registro(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(RegistroView())

    @commands.command(name="registro")
    @commands.guild_only()
    async def registro(self, ctx: commands.Context, *, texto: str = ""):
        if (warning := wrong_channel(ctx.channel.id)):
            await ctx.send(warning)
            return

        parsed = parse_registro(texto)
        if parsed is None:
            # Sin datos: botón que abre el formulario con los campos.
            await ctx.send(
                "📋 **Registro en la vecindad**\n"
                "Pulsa el botón y llena tus datos: Nombre PJ, Gremio y Alianza (opcional).",
                view=RegistroView(),
            )
            return
        async with ctx.typing():
            text = await do_registro(ctx.author, *parsed)
        await ctx.send(text)

    @app_commands.command(name="registro", description="Regístrate con tu jugador de Albion y tu gremio")
    @app_commands.describe(
        name="Nombre de tu personaje en Albion",
        guild="Tu gremio en Albion",
        alianza="Tu alianza (opcional)",
    )
    @app_commands.guild_only()
    async def registro_slash(self, interaction: discord.Interaction,
                             name: app_commands.Range[str, 1, 32],
                             guild: app_commands.Range[str, 1, 64],
                             alianza: app_commands.Range[str, 0, 64] | None = None):
        if (warning := wrong_channel(interaction.channel_id)):
            await interaction.response.send_message(warning, ephemeral=True)
            return
        # Cambiar roles puede tardar: se avisa a Discord para no pasar de 3 s.
        await interaction.response.defer(thinking=True)
        text = await do_registro(
            interaction.user, name.strip(), guild.strip(),
            (alianza or "").strip() or None,
        )
        await interaction.followup.send(text)

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            for name in (GUILD_ROLE_NAME, OUTSIDER_ROLE_NAME):
                role = find_role(guild, name)
                if role is None:
                    log.error("[REGISTRO] ERROR: El rol %s no existe en %s", name, guild.name)
                elif role >= guild.me.top_role:
                    log.error("[REGISTRO] ERROR: El rol %s está por encima del bot", name)
            channel_id = get_registro_channel_id()
            channel = guild.get_channel(channel_id) if channel_id else None
            if channel is None:
                log.warning("[REGISTRO] REGISTRO_CHANNEL_ID no configurado: !registro funciona en cualquier canal")
            else:
                log.info("[REGISTRO] Canal de registro: #%s", channel.name)


async def setup(bot: commands.Bot):
    await bot.add_cog(Registro(bot))
