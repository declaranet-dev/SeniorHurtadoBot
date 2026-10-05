"""Battle board: resumen de las peleas grandes (ZvZ) en las que participa el gremio."""

import json
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

from bot import albion
from bot.config import (
    BATTLE_INTERVAL_SECONDS,
    BATTLE_SETTLE_MINUTES,
    BATTLES_FILE,
    get_battle_min_players,
    get_battleboard_channel_id,
    get_killbot_guild_id,
)

log = logging.getLogger("seniorhurtadobot.battleboard")

BATTLE_URL = "https://albiononline.com/killboard/battles/{}"
MAX_REMEMBERED = 300  # ids de batallas publicadas que se recuerdan


def load_state() -> dict | None:
    if BATTLES_FILE.exists():
        return json.loads(BATTLES_FILE.read_text(encoding="utf-8"))
    return None


def save_state(posted: list[int]):
    BATTLES_FILE.parent.mkdir(parents=True, exist_ok=True)
    BATTLES_FILE.write_text(json.dumps({"publicadas": posted[-MAX_REMEMBERED:]}), encoding="utf-8")


def short(n: float) -> str:
    """1234567 -> 1.2M, 45200 -> 45k"""
    n = float(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(int(n))


def parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text[:19] + "+00:00")


def is_finished(battle: dict, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return parse_time(battle["endTime"]) + timedelta(minutes=BATTLE_SETTLE_MINUTES) <= now


def build_embed(battle: dict, guild_id: str) -> discord.Embed:
    start, end = parse_time(battle["startTime"]), parse_time(battle["endTime"])
    minutes = max(1, round((end - start).total_seconds() / 60))
    players = battle["players"].values()
    ours = sorted((p for p in players if p.get("guildId") == guild_id),
                  key=lambda p: (-p["kills"], p["deaths"], -p["killFame"]))
    our_guild = battle["guilds"].get(guild_id, {"name": "Gremio", "kills": 0, "deaths": 0, "killFame": 0})

    if our_guild["kills"] > our_guild["deaths"]:
        color = discord.Color.green()
    elif our_guild["kills"] < our_guild["deaths"]:
        color = discord.Color.red()
    else:
        color = discord.Color.light_grey()

    zone = battle.get("clusterName")
    embed = discord.Embed(
        title=f"☠️ Batalla en {zone}" if zone else "☠️ Batalla del gremio",
        url=BATTLE_URL.format(battle["id"]),
        color=color,
        description=(
            f"🕒 <t:{int(start.timestamp())}:f> · {minutes} min\n"
            f"👥 {len(battle['players'])} jugadores · ⚔️ {battle['totalKills']} kills · "
            f"🏆 {short(battle['totalFame'])} fama"
        ),
    )

    guilds = sorted(battle["guilds"].values(), key=lambda g: (-g["killFame"], -g["kills"]))
    lines = []
    for g in guilds[:8]:
        alliance = f" [{g['alliance']}]" if g.get("alliance") else ""
        name = f"🏠 **{g['name']}**" if g.get("id") == guild_id else g["name"]
        lines.append(f"{name}{alliance} — {g['kills']}/{g['deaths']} · {short(g['killFame'])}")
    if len(guilds) > 8:
        lines.append(f"... y {len(guilds) - 8} gremios más")
    embed.add_field(name="Gremios (kills/muertes · fama)", value="\n".join(lines)[:1024], inline=False)

    if ours:
        lines = [f"**{p['name']}** — {p['kills']}/{p['deaths']} · {short(p['killFame'])}" for p in ours[:15]]
        if len(ours) > 15:
            lines.append(f"... y {len(ours) - 15} más")
        embed.add_field(
            name=f"{our_guild['name']}: {len(ours)} jugadores · "
                 f"{our_guild['kills']}/{our_guild['deaths']} · {short(our_guild['killFame'])}",
            value="\n".join(lines)[:1024],
            inline=False,
        )
    embed.set_footer(text=f"Batalla {battle['id']}")
    return embed


class BattleBoard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.poll.change_interval(seconds=BATTLE_INTERVAL_SECONDS)

    async def cog_load(self):
        if not get_killbot_guild_id() or not get_battleboard_channel_id():
            log.warning("[BATTLE] Falta KILLBOT_GUILD_ID o BATTLEBOARD_CHANNEL_ID en .env: battle board apagado")
            return
        self.poll.start()

    async def cog_unload(self):
        self.poll.cancel()

    @tasks.loop(seconds=180)
    async def poll(self):
        guild_id = get_killbot_guild_id()
        channel = self.bot.get_channel(get_battleboard_channel_id())
        if channel is None:
            log.error("[BATTLE] ERROR: No encontré el canal BATTLEBOARD_CHANNEL_ID")
            return
        try:
            battles = await albion.get_guild_battles(guild_id)
        except albion.AlbionAPIError as e:
            log.warning("[BATTLE] La API de Albion no responde: %s", e)
            return

        state = load_state()
        if state is None:
            # Primera vez: no se publica el historial, solo lo nuevo desde ahora.
            save_state([b["id"] for b in battles])
            log.info("[BATTLE] Iniciado; se publicarán las batallas nuevas desde ahora")
            return

        posted = state["publicadas"]
        min_players = get_battle_min_players()
        for battle in sorted(battles, key=lambda b: b["id"]):
            if battle["id"] in posted or not is_finished(battle):
                continue  # las que siguen en curso se revisan en la próxima vuelta
            if len(battle["players"]) >= min_players:
                try:
                    await channel.send(embed=build_embed(battle, guild_id))
                except discord.HTTPException as e:
                    log.error("[BATTLE] ERROR: No se pudo publicar la batalla %s: %s", battle["id"], e)
                    break
                log.info("[BATTLE] Publicada %s (%d jugadores)", battle["id"], len(battle["players"]))
            posted.append(battle["id"])
            save_state(posted)

    @poll.before_loop
    async def before_poll(self):
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(get_battleboard_channel_id())
        log.info("[BATTLE] Siguiendo batallas de %s+ jugadores en #%s cada %ss",
                 get_battle_min_players(), getattr(channel, "name", "?"), BATTLE_INTERVAL_SECONDS)


async def setup(bot: commands.Bot):
    await bot.add_cog(BattleBoard(bot))
