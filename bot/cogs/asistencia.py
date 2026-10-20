"""Asistencia semanal en los canales de voz.

Cada minuto se suma un minuto a quien está en un canal de voz con al menos
otra persona (sin contar el canal AFK ni a quien está ensordecido). Cada lunes
se publica el ranking de la semana anterior en el canal de historial de nuevos.
La semana va de lunes a domingo, hora del centro de México.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot import settings
from bot.config import ASISTENCIA_FILE, ASISTENCIA_TOP, ASISTENCIA_UTC_OFFSET

log = logging.getLogger("seniorhurtadobot.asistencia")

LOCAL_TZ = timezone(timedelta(hours=ASISTENCIA_UTC_OFFSET))
MEDALS = ["🥇", "🥈", "🥉"]


def _load() -> dict:
    """{servidor: {"semana": "2026-W43", "segundos": {miembro: segundos}}}"""
    if ASISTENCIA_FILE.exists():
        return json.loads(ASISTENCIA_FILE.read_text(encoding="utf-8"))
    return {}


def _save(data: dict):
    ASISTENCIA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = ASISTENCIA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(ASISTENCIA_FILE)


def week_key(when: datetime) -> str:
    year, week, _ = when.astimezone(LOCAL_TZ).isocalendar()
    return f"{year}-W{week:02d}"


def week_label(key: str) -> str:
    """'2026-W43' -> 'del 19/10 al 25/10/2026'."""
    monday = datetime.strptime(key + "-1", "%G-W%V-%u")
    sunday = monday + timedelta(days=6)
    return f"del {monday:%d/%m} al {sunday:%d/%m/%Y}"


def fmt_time(seconds: int) -> str:
    hours, minutes = divmod(int(seconds) // 60, 60)
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


def ranking_embed(guild: discord.Guild, key: str, seconds: dict, final: bool) -> discord.Embed:
    title = "🎙️ Asistencia de la semana" if final else "🎙️ Asistencia de esta semana (hasta ahora)"
    embed = discord.Embed(title=title, color=discord.Color.purple(),
                          description=f"Tiempo en canales de voz {week_label(key)}.")
    ranked = sorted(seconds.items(), key=lambda item: item[1], reverse=True)
    lines = []
    for n, (member_id, secs) in enumerate(ranked[:ASISTENCIA_TOP]):
        member = guild.get_member(int(member_id))
        name = member.mention if member else f"<@{member_id}>"
        place = MEDALS[n] if n < len(MEDALS) else f"**{n + 1}.**"
        lines.append(f"{place} {name} · {fmt_time(secs)}")
    embed.add_field(name="Ranking", inline=False,
                    value="\n".join(lines) if lines else "Nadie estuvo en voz esta semana.")
    if ranked:
        embed.set_footer(text=f"{len(ranked)} personas en total · "
                              "cuenta solo con 2 o más personas en el canal, sin AFK")
    return embed


def present_members(guild: discord.Guild) -> list[discord.Member]:
    """Quienes cuentan ahora: en voz con al menos otra persona, sin AFK ni ensordecidos."""
    out = []
    for channel in (*guild.voice_channels, *guild.stage_channels):
        if guild.afk_channel and channel.id == guild.afk_channel.id:
            continue
        people = [m for m in channel.members if not m.bot and m.voice
                  and not (m.voice.self_deaf or m.voice.deaf)]
        if len(people) >= 2:
            out.extend(people)
    return out


class Asistencia(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tick.start()

    def cog_unload(self):
        self.tick.cancel()

    @tasks.loop(minutes=1)
    async def tick(self):
        data = _load()
        current = week_key(datetime.now(timezone.utc))
        changed = False
        for guild in self.bot.guilds:
            entry = data.setdefault(str(guild.id), {"semana": current, "segundos": {}})
            if entry["semana"] != current:
                await self._publish(guild, entry)
                entry.clear()
                entry.update({"semana": current, "segundos": {}})
                changed = True
            for member in present_members(guild):
                key = str(member.id)
                entry["segundos"][key] = entry["segundos"].get(key, 0) + 60
                changed = True
        if changed:
            _save(data)

    @tick.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    async def _publish(self, guild: discord.Guild, entry: dict):
        channel = settings.channel(guild, "canal_historial")
        if channel is None or not entry.get("segundos"):
            return
        try:
            await channel.send(embed=ranking_embed(guild, entry["semana"], entry["segundos"], final=True),
                               allowed_mentions=discord.AllowedMentions.none())
            log.info("[ASISTENCIA] %s: ranking de %s enviado a #%s", guild.name, entry["semana"], channel.name)
        except discord.HTTPException as e:
            log.error("[ASISTENCIA] %s: no pude enviar el ranking: %s", guild.name, e)

    @app_commands.command(name="asistenciah", description="Ranking de tiempo en voz de esta semana")
    @app_commands.guild_only()
    async def asistenciah(self, interaction: discord.Interaction):
        entry = _load().get(str(interaction.guild.id)) or {}
        key = entry.get("semana") or week_key(datetime.now(timezone.utc))
        embed = ranking_embed(interaction.guild, key, entry.get("segundos") or {}, final=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Asistencia(bot))
