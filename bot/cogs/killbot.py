"""Killbot: publica las kills y muertes del gremio en un canal de Discord."""

import json
import logging
from datetime import datetime

import discord
from discord.ext import commands, tasks

from bot import albion
from bot.config import (
    KILLBOT_FILE,
    KILLBOT_GLOBAL_PAGES,
    KILLBOT_GUILD_FEED_EVERY,
    GUCCI_MIN_FAME,
    get_gucci_channel_id,
    KILLBOT_INTERVAL_SECONDS,
    get_killbot_channel_id,
    get_killbot_deaths_channel_id,
    get_killbot_guild_id,
    get_killbot_min_fame,
)

log = logging.getLogger("seniorhurtadobot.killbot")

KILL_URL = "https://albiononline.com/killboard/kill/{}"
ITEM_IMG = "https://render.albiononline.com/v1/item/{}.png"


MAX_SEEN = 3000  # ids de eventos ya revisados que se recuerdan


def load_state() -> dict | None:
    """{"ultimo_evento": id al arrancar por primera vez, "vistos": [ids]}"""
    if KILLBOT_FILE.exists():
        state = json.loads(KILLBOT_FILE.read_text(encoding="utf-8"))
        state.setdefault("vistos", [])
        return state
    return None


def save_state(state: dict):
    state["vistos"] = state["vistos"][-MAX_SEEN:]
    KILLBOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    KILLBOT_FILE.write_text(json.dumps(state), encoding="utf-8")


def fmt(n: float) -> str:
    """12345 -> 12.345"""
    return f"{int(n):,}".replace(",", ".")


def player_line(p: dict) -> str:
    guild = p.get("GuildName") or "Sin gremio"
    alliance = f" [{p['AllianceName']}]" if p.get("AllianceName") else ""
    return f"**{p['Name']}**\n{guild}{alliance}\nIP {round(p.get('AverageItemPower') or 0)}"


def weapon(p: dict) -> str | None:
    main = (p.get("Equipment") or {}).get("MainHand")
    return main.get("Type") if main else None


def build_embed(event: dict, guild_id: str) -> discord.Embed:
    killer, victim = event["Killer"], event["Victim"]
    is_kill = killer.get("GuildId") == guild_id
    title = (f"⚔️ {killer['Name']} mató a {victim['Name']}" if is_kill
             else f"💀 {victim['Name']} murió a manos de {killer['Name']}")
    embed = discord.Embed(
        title=title,
        url=KILL_URL.format(event["EventId"]),
        color=discord.Color.green() if is_kill else discord.Color.red(),
        timestamp=datetime.fromisoformat(event["TimeStamp"][:19] + "+00:00"),
    )
    embed.add_field(name="Asesino", value=player_line(killer))
    embed.add_field(name="Víctima", value=player_line(victim))
    embed.add_field(name="Fama", value=fmt(event.get("TotalVictimKillFame") or 0))

    others = [p["Name"] for p in event.get("Participants") or []
              if p.get("Name") != killer["Name"]]
    if others:
        shown = ", ".join(others[:8]) + (f" y {len(others) - 8} más" if len(others) > 8 else "")
        embed.add_field(name=f"Asistencias ({len(others)})", value=shown, inline=False)

    item = weapon(killer if is_kill else victim)
    if item:
        embed.set_thumbnail(url=ITEM_IMG.format(item))
    embed.set_footer(text="Kill del gremio" if is_kill else "Muerte del gremio")
    return embed


class Killbot(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ticks = 0
        self.poll.change_interval(seconds=KILLBOT_INTERVAL_SECONDS)

    async def cog_load(self):
        if not get_killbot_guild_id() or not get_killbot_channel_id():
            log.warning("[KILLBOT] Falta KILLBOT_GUILD_ID o KILLBOT_CHANNEL_ID en .env: killbot apagado")
            return
        self.poll.start()

    async def cog_unload(self):
        self.poll.cancel()

    @tasks.loop(seconds=15)
    async def poll(self):
        guild_id = get_killbot_guild_id()
        kills_channel = self.bot.get_channel(get_killbot_channel_id())
        # Si no hay canal de muertes configurado, las muertes van al de kills.
        deaths_channel = self.bot.get_channel(
            get_killbot_deaths_channel_id() or get_killbot_channel_id())
        if kills_channel is None or deaths_channel is None:
            log.error("[KILLBOT] ERROR: No encontré el canal de kills o de muertes")
            return

        # Fuente principal: el feed general de Albion (al día) filtrado al gremio.
        # Respaldo: la lista del gremio, que la API sirve con horas de retraso,
        # para no perder lo que se escape del feed general.
        events = {}
        gucci = {}  # kills de todo Albion con mucha fama
        try:
            for event in await albion.get_recent_events(KILLBOT_GLOBAL_PAGES):
                if guild_id in (event["Killer"].get("GuildId"), event["Victim"].get("GuildId")):
                    events[event["EventId"]] = event
                if (event.get("TotalVictimKillFame") or 0) >= GUCCI_MIN_FAME:
                    gucci[event["EventId"]] = event
            self._ticks += 1
            if self._ticks % KILLBOT_GUILD_FEED_EVERY == 1:
                for event in await albion.get_guild_events(guild_id):
                    events[event["EventId"]] = event
        except albion.AlbionAPIError as e:
            log.warning("[KILLBOT] La API de Albion no responde: %s", e)
            if not events and not gucci:
                return

        state = load_state()
        if state is None:
            # Primera vez: no se publica el historial, solo lo nuevo desde ahora.
            state = {"ultimo_evento": max([*events, *gucci], default=0), "vistos": sorted(events)}
            save_state(state)
            log.info("[KILLBOT] Iniciado; se publicarán las kills nuevas desde ahora")
            return

        await self._post_gucci_kills(gucci, state)

        seen = set(state["vistos"])
        new = [events[i] for i in sorted(events)
               if i > state["ultimo_evento"] and i not in seen]
        min_fame = get_killbot_min_fame()
        for event in new:
            if (event.get("TotalVictimKillFame") or 0) >= min_fame:
                is_kill = event["Killer"].get("GuildId") == guild_id
                channel = kills_channel if is_kill else deaths_channel
                try:
                    await channel.send(embed=build_embed(event, guild_id))
                except discord.HTTPException as e:
                    log.error("[KILLBOT] ERROR: No se pudo publicar el evento %s: %s", event["EventId"], e)
                    break  # se reintenta en la siguiente vuelta
                log.info("[KILLBOT] Publicado %s: %s -> %s (hora del juego %s)", event["EventId"],
                         event["Killer"]["Name"], event["Victim"]["Name"], event["TimeStamp"][11:19])
            state["vistos"].append(event["EventId"])
            save_state(state)

    async def _post_gucci_kills(self, gucci: dict, state: dict):
        """Kills de todo Albion con mucha fama al canal de gucci kills."""
        gucci_id = get_gucci_channel_id()
        channel = self.bot.get_channel(gucci_id) if gucci_id else None
        if channel is None:
            return
        seen = set(state.setdefault("gucci_vistos", []))
        for event_id in sorted(gucci):
            if event_id <= state["ultimo_evento"] or event_id in seen:
                continue
            event = gucci[event_id]
            # Se muestra desde el lado del asesino (verde, arma del asesino).
            embed = build_embed(event, event["Killer"].get("GuildId"))
            embed.title = f"💵 GUCCI KILL · {embed.title}"
            embed.color = discord.Color.gold()
            embed.set_footer(text="Gucci kill de Albion")
            try:
                await channel.send(embed=embed)
            except discord.HTTPException as e:
                log.error("[KILLBOT] ERROR: No se pudo publicar la gucci kill %s: %s", event_id, e)
                return  # se reintenta en la siguiente vuelta
            log.info("[KILLBOT] Gucci kill %s: %s -> %s (%s de fama)", event_id,
                     event["Killer"]["Name"], event["Victim"]["Name"], event["TotalVictimKillFame"])
            state["gucci_vistos"] = (state["gucci_vistos"] + [event_id])[-MAX_SEEN:]
            save_state(state)

    @poll.before_loop
    async def before_poll(self):
        await self.bot.wait_until_ready()
        kills = self.bot.get_channel(get_killbot_channel_id())
        deaths = self.bot.get_channel(get_killbot_deaths_channel_id() or get_killbot_channel_id())
        log.info("[KILLBOT] Siguiendo al gremio %s: kills en #%s, muertes en #%s, cada %ss",
                 get_killbot_guild_id(), getattr(kills, "name", "?"),
                 getattr(deaths, "name", "?"), KILLBOT_INTERVAL_SECONDS)


async def setup(bot: commands.Bot):
    await bot.add_cog(Killbot(bot))
