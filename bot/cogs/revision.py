"""Revisión de miembros y aliados contra Albion, cada 3 días.

Revisa a quien tiene el rol de miembros, de alianza o de otros gremios y
publica un informe en el canal de historial de nuevos. No quita ni da roles:
solo avisa al staff.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot import albion, settings
from bot.cogs.registro import guild_alliance_id
from bot.config import REGISTROS_FILE, REVISION_DAYS, REVISION_FILE

log = logging.getLogger("seniorhurtadobot.revision")


def _load(path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_state(data: dict):
    REVISION_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = REVISION_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(REVISION_FILE)


def _player_name(member: discord.Member, registros: dict) -> str:
    """Nombre en Albion: el del registro, o el apodo si no hay registro."""
    entry = registros.get(str(member.id)) or {}
    return entry.get("jugador") or member.display_name


def _where(player: dict) -> str:
    guild = player.get("GuildName") or "sin gremio"
    alliance = f" [{player['AllianceName']}]" if player.get("AllianceName") else ""
    return f"{guild}{alliance}"


def _field_text(lines: list[str]) -> str:
    """Junta las líneas sin pasar del límite de Discord (1024)."""
    out, used = [], 0
    for n, line in enumerate(lines):
        rest = len(lines) - n
        if used + len(line) + 1 > 1000:
            out.append(f"… y {rest} más")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


async def review_guild(guild: discord.Guild) -> discord.Embed | None:
    """Arma el informe de un servidor. None si falta configurar el gremio."""
    config = settings.get(guild.id)
    if not config["gremio_id"]:
        return None
    region = config["servidor_albion"]
    rol_miembro = settings.role(guild, "rol_miembro")
    rol_aliado = settings.role(guild, "rol_aliado")
    rol_externo = settings.role(guild, "rol_externo")
    alliance_id = await guild_alliance_id(config)
    registros = _load(REGISTROS_FILE).get(str(guild.id), {})

    fuera_gremio, fuera_alianza, falta_rol, no_existe, sin_revisar = [], [], [], [], []
    revisados = 0
    for member in guild.members:
        if member.bot:
            continue
        is_member = rol_miembro is not None and rol_miembro in member.roles
        is_ally = not is_member and rol_aliado is not None and rol_aliado in member.roles
        # De los demás gremios solo se revisa a los registrados, por si ya se unieron.
        is_other = (not is_member and not is_ally and rol_externo is not None
                    and rol_externo in member.roles and str(member.id) in registros)
        if not (is_member or is_ally or is_other):
            continue
        name = _player_name(member, registros)
        try:
            player = await albion.find_player(name, region)
        except albion.AlbionAPIError:
            sin_revisar.append(f"{member.mention} ({name})")
            continue
        finally:
            await asyncio.sleep(0.5)  # para no saturar la API de Albion
        revisados += 1
        line = f"{member.mention} · **{name}**"
        if player is None:
            if is_member or is_ally:
                no_existe.append(line)
            continue
        in_guild = player.get("GuildId") == config["gremio_id"]
        in_alliance = bool(alliance_id) and player.get("AllianceId") == alliance_id
        if is_member and not in_guild:
            fuera_gremio.append(f"{line} → {_where(player)}")
        elif is_ally and not in_alliance and not in_guild:
            fuera_alianza.append(f"{line} → {_where(player)}")
        elif is_other and in_guild:
            falta_rol.append(f"{line} → ya es del gremio")
        elif is_other and in_alliance and rol_aliado is not None:
            falta_rol.append(f"{line} → ya es de la alianza ({_where(player)})")

    embed = discord.Embed(
        title="📋 Revisión de miembros y aliados",
        color=discord.Color.teal(),
        description=(f"Revisé **{revisados}** jugadores con Albion. "
                     "**No se quitó ni se dio ningún rol**: esto es solo un aviso para el staff."),
    )
    sections = [
        (f"🚪 Con rol de miembro pero ya no están en {config['gremio_nombre'] or 'el gremio'}", fuera_gremio),
        ("🤝 Con rol de alianza pero su gremio no es de la alianza", fuera_alianza),
        ("⬆️ Registrados como otro gremio que ya entraron", falta_rol),
        ("❓ Su nombre no existe en Albion (revisa el apodo)", no_existe),
        ("⚠️ La API de Albion no respondió", sin_revisar),
    ]
    for title, lines in sections:
        if lines:
            embed.add_field(name=f"{title} ({len(lines)})", value=_field_text(lines), inline=False)
    if not any(lines for _, lines in sections):
        embed.add_field(name="✅ Todo en orden", value="Todos tienen el rol que les toca.", inline=False)
    embed.set_footer(text=f"Se revisa cada {REVISION_DAYS} días · /revisionh para revisar ahora")
    embed.timestamp = datetime.now(timezone.utc)
    return embed


class Revision(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._running: set[int] = set()
        self.scheduler.start()

    def cog_unload(self):
        self.scheduler.cancel()

    async def _run(self, guild: discord.Guild, channel: discord.TextChannel) -> bool:
        if guild.id in self._running:
            return False
        self._running.add(guild.id)
        try:
            embed = await review_guild(guild)
            if embed is None:
                return False
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            log.info("[REVISION] %s: informe enviado a #%s", guild.name, channel.name)
            return True
        finally:
            self._running.discard(guild.id)

    @tasks.loop(hours=1)
    async def scheduler(self):
        state = _load(REVISION_FILE)
        now = datetime.now(timezone.utc)
        for guild in self.bot.guilds:
            channel = settings.channel(guild, "canal_historial")
            if channel is None or not settings.get(guild.id)["gremio_id"]:
                continue
            last = state.get(str(guild.id))
            if last and now - datetime.fromisoformat(last) < timedelta(days=REVISION_DAYS):
                continue
            try:
                sent = await self._run(guild, channel)
            except (albion.AlbionAPIError, discord.HTTPException) as e:
                log.warning("[REVISION] %s: no pude hacer la revisión: %s", guild.name, e)
                continue
            if sent:
                state = _load(REVISION_FILE)
                state[str(guild.id)] = now.isoformat(timespec="seconds")
                _save_state(state)

    @scheduler.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="revisionh",
                          description="Revisa ahora con Albion a los miembros y aliados (admins)")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def revisionh(self, interaction: discord.Interaction):
        guild = interaction.guild
        channel = settings.channel(guild, "canal_historial")
        if channel is None or not settings.get(guild.id)["gremio_id"]:
            await interaction.response.send_message(
                "Falta configurar el canal de historial de nuevos o el gremio (/configuracion).",
                ephemeral=True)
            return
        if guild.id in self._running:
            await interaction.response.send_message("Ya estoy haciendo una revisión, espera un poco.",
                                                    ephemeral=True)
            return
        await interaction.response.send_message(
            f"Revisando con Albion… El informe sale en {channel.mention} en unos minutos.", ephemeral=True)
        try:
            await self._run(guild, channel)
        except (albion.AlbionAPIError, discord.HTTPException) as e:
            log.warning("[REVISION] %s: /revisionh falló: %s", guild.name, e)
            await interaction.followup.send("❌ No pude hacer la revisión: la API de Albion no respondió.",
                                            ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Revision(bot))
