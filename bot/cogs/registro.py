"""Registro de jugadores: valida con Albion y da el rol según el gremio configurado."""

import json
import logging
import re
import unicodedata
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from bot import albion, settings
from bot.config import REGISTROS_FILE

log = logging.getLogger("seniorhurtadobot.registro")

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


def save_registro(member: discord.Member, player: str, guild_name: str,
                  alliance: str | None, role_name: str | None):
    """Guarda el registro en data/registros.json, separado por servidor de Discord."""
    REGISTROS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if REGISTROS_FILE.exists():
        data = json.loads(REGISTROS_FILE.read_text(encoding="utf-8"))
    data.setdefault(str(member.guild.id), {})[str(member.id)] = {
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


async def set_roles(member: discord.Member, target: discord.Role | None,
                    other: discord.Role | None) -> str | None:
    """Pone el rol que le toca y quita el rol de nuevos y el rol contrario.

    Devuelve None si todo fue bien, o un texto con el problema.
    """
    guild = member.guild
    me = guild.me
    if target is None:
        return "el rol no está configurado (/configuracion)."
    if not me.guild_permissions.manage_roles:
        log.error("[REGISTRO] ERROR: El bot no tiene permisos suficientes")
        return "el bot no tiene permiso para gestionar roles."
    if target >= me.top_role:
        log.error("[REGISTRO] ERROR: El rol %s está por encima del bot", target.name)
        return f"el rol {target.name} está por encima del bot."

    to_remove = [
        r for r in (settings.role(guild, "rol_nuevo"), other)
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


ARTICLES = ("la ", "el ", "los ", "las ", "the ")


def same_guild(entered: str, official: str) -> bool:
    """El gremio escrito coincide con el oficial. También vale:
    - con artículo delante ("La Vecindad del Chavo")
    - una parte clara del nombre ("vecindad")
    Las siglas (p. ej. "VDC") no se aceptan."""
    entered, official = normalize(entered), normalize(official)
    for article in ARTICLES:
        if entered.startswith(article):
            entered = entered[len(article):]
        if official.startswith(article):
            official = official[len(article):]
    if entered == official:
        return True
    return len(entered) >= 4 and entered in official


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
    config = settings.get(member.guild.id)
    try:
        api_player = await albion.find_player(player, config["servidor_albion"])
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

    is_member = bool(config["gremio_id"]) and api_player.get("GuildId") == config["gremio_id"]
    member_role = settings.role(member.guild, "rol_miembro")
    outsider_role = settings.role(member.guild, "rol_externo")
    target, other = (member_role, outsider_role) if is_member else (outsider_role, member_role)

    role_error = await set_roles(member, target, other)
    nick_error = await set_nickname(member, player)
    save_registro(member, player, guild_name, alliance, target.name if target else None)

    lines = [
        f"✅ **Registro completado** — {member.mention}",
        f"Jugador Albion: **{player}**",
        f"Gremio: **{guild_name}**",
        f"Alianza: **{alliance or '—'}**",
    ]
    if role_error:
        lines.append(f"⚠️ No pude darte tu rol: {role_error} Avisa a un admin.")
    elif is_member:
        lines.append(f"🏠 ¡Eres de la vecindad! Ya tienes tu rol **{target.name}**.")
    else:
        lines.append(f"👀 No eres de la vecindad... te tocó el rol **{target.name}**.")
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


def wrong_channel(guild: discord.Guild, channel_id_used: int | None) -> str | None:
    """Mensaje de aviso si el registro se intenta fuera del canal configurado."""
    channel_id = settings.get(guild.id)["canal_registro"]
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
        if (warning := wrong_channel(interaction.guild, interaction.channel_id)):
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
        if (warning := wrong_channel(ctx.guild, ctx.channel.id)):
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
        if (warning := wrong_channel(interaction.guild, interaction.channel_id)):
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
            config = settings.get(guild.id)
            if not config["canal_registro"]:
                continue  # registro sin configurar en este servidor
            if not config["gremio_id"]:
                log.warning("[REGISTRO] %s: no hay gremio de Albion configurado", guild.name)
            for key in ("rol_miembro", "rol_externo"):
                role = settings.role(guild, key)
                if role is None:
                    log.warning("[REGISTRO] %s: falta %s", guild.name, settings.ROLES[key])
                elif role >= guild.me.top_role:
                    log.error("[REGISTRO] %s: el rol %s está por encima del bot", guild.name, role.name)


async def setup(bot: commands.Bot):
    await bot.add_cog(Registro(bot))
