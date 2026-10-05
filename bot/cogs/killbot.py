"""Killbot: publica kills, muertes y gucci kills en cada servidor según su /configuracion."""

import io
import json
import logging
from datetime import datetime

import discord
from discord.ext import commands, tasks

from bot import albion, killcard, settings
from bot.config import (
    KILLBOT_FILE,
    KILLBOT_GLOBAL_PAGES,
    KILLBOT_GUILD_FEED_EVERY,
    KILLBOT_INTERVAL_SECONDS,
)

log = logging.getLogger("seniorhurtadobot.killbot")

KILL_URL = "https://albiononline.com/killboard/kill/{}"
ITEM_IMG = "https://render.albiononline.com/v1/item/{}.png"


MAX_SEEN = 3000  # ids de eventos ya revisados que se recuerdan


def load_states() -> dict:
    """Estado por servidor de Discord:
    {"servidores": {id: {"ultimo_evento", "vistos", "gucci_vistos"}}}"""
    if not KILLBOT_FILE.exists():
        return {"servidores": {}}
    data = json.loads(KILLBOT_FILE.read_text(encoding="utf-8"))
    data.setdefault("servidores", {})  # el formato viejo lo migra settings.migrate_from_env
    return data


def save_states(data: dict):
    for state in data["servidores"].values():
        state["vistos"] = state["vistos"][-MAX_SEEN:]
        state["gucci_vistos"] = state["gucci_vistos"][-MAX_SEEN:]
    KILLBOT_FILE.parent.mkdir(parents=True, exist_ok=True)
    KILLBOT_FILE.write_text(json.dumps(data), encoding="utf-8")


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


_cards: dict[int, bytes] = {}  # tarjetas ya dibujadas (se reusan entre servidores)


async def send_with_card(channel, embed: discord.Embed, event: dict, server: str = "americas"):
    """Envía el mensaje con la tarjeta de la kill; si la imagen falla, sin ella."""
    image = _cards.get(event["EventId"])
    if image is None:
        try:
            image = (await killcard.render_card(event, server)).getvalue()
        except Exception:  # la tarjeta es un extra: nunca debe frenar el killbot
            log.exception("[KILLBOT] No se pudo dibujar la tarjeta de %s", event["EventId"])
            await channel.send(embed=embed)
            return
        if len(_cards) > 50:
            _cards.clear()
        _cards[event["EventId"]] = image
    embed.set_thumbnail(url=None)  # la tarjeta ya muestra el equipo completo
    embed.set_image(url="attachment://kill.jpg")
    await channel.send(embed=embed, file=discord.File(io.BytesIO(image), filename="kill.jpg"))


class Killbot(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._ticks = 0
        self.poll.change_interval(seconds=KILLBOT_INTERVAL_SECONDS)

    async def cog_load(self):
        self.poll.start()

    async def cog_unload(self):
        self.poll.cancel()

    def _active_servers(self) -> list[tuple[discord.Guild, dict]]:
        """Servidores de Discord con killbot o gucci kills configurados."""
        out = []
        for guild in self.bot.guilds:
            config = settings.get(guild.id)
            tracks_guild = config["gremio_id"] and (config["canal_kills"] or config["canal_muertes"])
            if tracks_guild or config["canal_gucci"]:
                out.append((guild, config))
        return out

    @tasks.loop(seconds=15)
    async def poll(self):
        servers = self._active_servers()
        if not servers:
            return

        # Fuente principal: el feed general de cada servidor de Albion (al día).
        # Respaldo: la lista de cada gremio, que la API sirve con horas de
        # retraso, para no perder lo que se escape del feed general.
        self._ticks += 1
        feeds: dict[str, list[dict]] = {}
        guild_feeds: dict[str, list[dict]] = {}
        for region in {config["servidor_albion"] for _, config in servers}:
            try:
                feeds[region] = await albion.get_recent_events(KILLBOT_GLOBAL_PAGES, region)
            except albion.AlbionAPIError as e:
                log.warning("[KILLBOT] La API de Albion (%s) no responde: %s", region, e)
        if self._ticks % KILLBOT_GUILD_FEED_EVERY == 1:
            for albion_guild, region in {(c["gremio_id"], c["servidor_albion"]) for _, c in servers if c["gremio_id"]}:
                try:
                    guild_feeds[albion_guild] = await albion.get_guild_events(albion_guild, server=region)
                except albion.AlbionAPIError as e:
                    log.warning("[KILLBOT] Lista del gremio %s no disponible: %s", albion_guild, e)

        data = load_states()
        for guild, config in servers:
            feed = feeds.get(config["servidor_albion"])
            if feed is None:
                continue
            state = data["servidores"].get(str(guild.id))
            if state is None:
                # Servidor nuevo: no se publica el historial, solo lo nuevo.
                data["servidores"][str(guild.id)] = {
                    "ultimo_evento": max((e["EventId"] for e in feed), default=0),
                    "vistos": [], "gucci_vistos": []}
                save_states(data)
                log.info("[KILLBOT] %s: iniciado; se publicarán las kills nuevas desde ahora", guild.name)
                continue
            state.setdefault("vistos", [])
            state.setdefault("gucci_vistos", [])
            await self._post_gucci_kills(guild, config, feed, state, data)
            await self._post_guild_kills(guild, config, feed, guild_feeds, state, data)

    async def _post_guild_kills(self, guild, config, feed, guild_feeds, state, data):
        albion_guild = config["gremio_id"]
        kills_channel = settings.channel(guild, "canal_kills")
        # Si no hay canal de muertes configurado, las muertes van al de kills.
        deaths_channel = settings.channel(guild, "canal_muertes") or kills_channel
        if not albion_guild or (kills_channel is None and deaths_channel is None):
            return
        events = {e["EventId"]: e for e in feed
                  if albion_guild in (e["Killer"].get("GuildId"), e["Victim"].get("GuildId"))}
        for e in guild_feeds.get(albion_guild, []):
            events[e["EventId"]] = e
        seen = set(state["vistos"])
        for event_id in sorted(events):
            if event_id <= state["ultimo_evento"] or event_id in seen:
                continue
            event = events[event_id]
            is_kill = event["Killer"].get("GuildId") == albion_guild
            channel = kills_channel if is_kill else deaths_channel
            if channel is not None:
                try:
                    await send_with_card(channel, build_embed(event, albion_guild), event,
                                         config["servidor_albion"])
                except discord.HTTPException as e:
                    log.error("[KILLBOT] %s: no se pudo publicar %s: %s", guild.name, event_id, e)
                    return  # se reintenta en la siguiente vuelta
                log.info("[KILLBOT] %s: publicado %s: %s -> %s (hora del juego %s)", guild.name, event_id,
                         event["Killer"]["Name"], event["Victim"]["Name"], event["TimeStamp"][11:19])
            state["vistos"].append(event_id)
            save_states(data)

    async def _post_gucci_kills(self, guild, config, feed, state, data):
        """Kills de todo Albion con mucha fama al canal de gucci kills."""
        channel = settings.channel(guild, "canal_gucci")
        if channel is None:
            return
        seen = set(state["gucci_vistos"])
        for event in sorted(feed, key=lambda e: e["EventId"]):
            event_id = event["EventId"]
            if (event.get("TotalVictimKillFame") or 0) < config["gucci_min_fama"]:
                continue
            if event_id <= state["ultimo_evento"] or event_id in seen:
                continue
            # Se muestra desde el lado del asesino (verde, arma del asesino).
            embed = build_embed(event, event["Killer"].get("GuildId"))
            embed.title = f"💵 GUCCI KILL · {embed.title}"
            embed.color = discord.Color.gold()
            embed.set_footer(text="Gucci kill de Albion")
            try:
                await send_with_card(channel, embed, event, config["servidor_albion"])
            except discord.HTTPException as e:
                log.error("[KILLBOT] %s: no se pudo publicar la gucci kill %s: %s", guild.name, event_id, e)
                return  # se reintenta en la siguiente vuelta
            log.info("[KILLBOT] %s: gucci kill %s: %s -> %s (%s de fama)", guild.name, event_id,
                     event["Killer"]["Name"], event["Victim"]["Name"], event["TotalVictimKillFame"])
            state["gucci_vistos"].append(event_id)
            save_states(data)

    @poll.before_loop
    async def before_poll(self):
        await self.bot.wait_until_ready()
        for guild, config in self._active_servers():
            log.info("[KILLBOT] %s: gremio %s, kills #%s, muertes #%s, gucci #%s", guild.name,
                     config["gremio_nombre"] or "-",
                     getattr(settings.channel(guild, "canal_kills"), "name", "-"),
                     getattr(settings.channel(guild, "canal_muertes"), "name", "-"),
                     getattr(settings.channel(guild, "canal_gucci"), "name", "-"))


async def setup(bot: commands.Bot):
    await bot.add_cog(Killbot(bot))
