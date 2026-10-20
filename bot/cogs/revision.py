"""Revisión de miembros y aliados contra Albion, cada 2 días.

Compara la lista de miembros del gremio en Albion con la de la revisión
anterior (quién salió y quién entró), revisa a quien tiene el rol de
miembros, de alianza o de otros gremios y
publica un informe en #asistencias-a-cta (o en el historial de nuevos). No quita ni da roles:
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
from bot.cogs.balance import deny, is_banker
from bot.cogs.registro import guild_alliance_id
from bot.config import REGISTROS_FILE, REPORTS_CHANNEL_NAME, REVISION_DAYS, REVISION_FILE

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


def report_channel(guild: discord.Guild) -> discord.TextChannel | None:
    """#asistencias-a-cta; si no existe, el canal de historial de nuevos."""
    # Por si el nombre lleva emojis alrededor (p. ej. "📊asistencias-a-cta").
    found = discord.utils.find(lambda c: REPORTS_CHANNEL_NAME in c.name, guild.text_channels)
    return found or settings.channel(guild, "canal_historial")


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


def roster_text(guild_name: str, previous: list[str] | None, current: list[str],
                registros: dict, guild: discord.Guild) -> str:
    """Quién salió y quién entró al gremio desde la revisión anterior."""
    if previous is None:
        return (f"📝 Guardé la lista de **{len(current)}** miembros de {guild_name} en Albion. "
                "En el próximo informe te digo quién salió o entró.")
    before, now = {n.lower(): n for n in previous}, {n.lower(): n for n in current}
    left = sorted((before[k] for k in before.keys() - now.keys()), key=str.lower)
    joined = sorted((now[k] for k in now.keys() - before.keys()), key=str.lower)
    nick = {e["jugador"].lower(): int(uid) for uid, e in registros.items() if e.get("jugador")}

    def with_nick(name: str) -> str:
        member = guild.get_member(nick.get(name.lower(), 0))
        return f"**{name}** ({member.mention})" if member else f"**{name}**"

    lines = []
    if left:
        lines.append(f"🚪 Los jugadores que dejaron {guild_name} estos últimos días son: "
                     + ", ".join(with_nick(n) for n in left) + ".")
    else:
        lines.append(f"✅ De {guild_name} no se ha ido nadie.")
    if joined:
        lines.append(("Es más, entraron" if not left else "🆕 Entraron") + " estos jugadores nuevos: "
                     + ", ".join(with_nick(n) for n in joined) + ".")
    else:
        lines.append("No entró nadie nuevo.")
    lines.append(f"Ahora son **{len(current)}** en Albion.")
    text = "\n".join(lines)
    return text if len(text) <= 3000 else text[:2990] + "…"


async def review_guild(guild: discord.Guild, previous: list[str] | None = None
                       ) -> tuple[discord.Embed, list[str] | None] | None:
    """Arma el informe de un servidor y devuelve también la lista actual del gremio en Albion.

    None si falta configurar el gremio.
    """
    config = settings.get(guild.id)
    if not config["gremio_id"]:
        return None
    region = config["servidor_albion"]
    guild_name = config["gremio_nombre"] or "el gremio"
    try:
        roster = sorted(p["Name"] for p in await albion.get_guild_members(config["gremio_id"], region))
    except albion.AlbionAPIError as e:
        log.warning("[REVISION] %s: sin la lista del gremio: %s", guild.name, e)
        roster = None
    rol_miembro = settings.role(guild, "rol_miembro")
    rol_aliado = settings.role(guild, "rol_aliado")
    rol_externo = settings.role(guild, "rol_externo")
    alliance_id = await guild_alliance_id(config)
    registros = _load(REGISTROS_FILE).get(str(guild.id), {})

    fuera_gremio, fuera_alianza, falta_rol, no_existe, sin_revisar = [], [], [], [], []
    revisados = 0
    in_roster = {n.lower() for n in roster or []}
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
        if is_member and name.lower() in in_roster:
            revisados += 1  # está en la lista del gremio: no hace falta buscarlo
            continue
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
        description=(
            (roster_text(guild_name, previous, roster, registros, guild) if roster is not None
             else "⚠️ La API de Albion no me dio la lista del gremio: quién salió o entró va en el próximo informe.")
            + f"\n\nRevisé los roles de **{revisados}** jugadores. "
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
    return embed, roster


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
            state = _load(REVISION_FILE)
            previous = state.get(str(guild.id))
            previous = previous if isinstance(previous, dict) else {}
            result = await review_guild(guild, previous.get("miembros"))
            if result is None:
                return False
            embed, roster = result
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            log.info("[REVISION] %s: informe enviado a #%s", guild.name, channel.name)
            state = _load(REVISION_FILE)
            state[str(guild.id)] = {
                "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "miembros": roster if roster is not None else previous.get("miembros"),
            }
            _save_state(state)
            return True
        finally:
            self._running.discard(guild.id)

    @tasks.loop(hours=1)
    async def scheduler(self):
        state = _load(REVISION_FILE)
        now = datetime.now(timezone.utc)
        for guild in self.bot.guilds:
            channel = report_channel(guild)
            if channel is None or not settings.get(guild.id)["gremio_id"]:
                continue
            last = state.get(str(guild.id))
            last = last.get("fecha") if isinstance(last, dict) else last
            if last and now - datetime.fromisoformat(last) < timedelta(days=REVISION_DAYS):
                continue
            try:
                await self._run(guild, channel)
            except (albion.AlbionAPIError, discord.HTTPException) as e:
                log.warning("[REVISION] %s: no pude hacer la revisión: %s", guild.name, e)

    @scheduler.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="revisionh",
                          description="Revisa ahora con Albion a los miembros y aliados (Chespirito)")
    @app_commands.guild_only()
    async def revisionh(self, interaction: discord.Interaction):
        guild = interaction.guild
        # La usan el rol Chespirito (el del balance) y quien administra el servidor.
        if not (interaction.user.guild_permissions.manage_guild or is_banker(interaction.user)):
            await deny(interaction)
            return
        channel = report_channel(guild)
        if channel is None or not settings.get(guild.id)["gremio_id"]:
            await interaction.response.send_message(
                f"Falta el canal #{REPORTS_CHANNEL_NAME} (o el de historial de nuevos) o el gremio (/configuracion).",
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
