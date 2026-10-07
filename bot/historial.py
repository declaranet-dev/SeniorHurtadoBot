"""Historial de jugadores nuevos: ficha de Albion que se envía al registrarse.

Usa la API oficial de Albion (gameinfo). albiondb.net no se puede consultar
desde el bot porque está detrás de una verificación anti-bots de Cloudflare.

La API no tiene el historial de gremios, así que se arma con las últimas
kills y muertes del jugador: cada una guarda el gremio que tenía ese día.
"""

import logging
from datetime import datetime

import discord

from bot import albion, settings

log = logging.getLogger("seniorhurtadobot.historial")

KILLBOARD_URL = "https://albiononline.com/killboard/player/{}"
MURDER_LEDGER_URL = "https://murderledger.com/players/{}"
MAX_GUILDS = 10  # gremios que se muestran en el historial


def fmt(n: float) -> str:
    return f"{int(n or 0):,}"


def short(n: float) -> str:
    n = float(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(int(n))


def guild_history(kills: list[dict], deaths: list[dict]) -> list[dict]:
    """Gremios por los que pasó el jugador, del más reciente al más antiguo.

    Cada kill guarda el gremio del asesino y cada muerte el de la víctima en
    ese momento; los días seguidos en el mismo gremio se juntan en un periodo.
    """
    seen = []
    for events, side in ((kills, "Killer"), (deaths, "Victim")):
        for event in events:
            who = event[side]
            seen.append((event["TimeStamp"][:19], who.get("GuildName") or "Sin gremio",
                         who.get("AllianceName") or ""))
    periods = []
    for when, guild, alliance in sorted(seen):
        day = datetime.fromisoformat(when)
        if periods and periods[-1]["gremio"] == guild:
            periods[-1]["hasta"] = day
        else:
            periods.append({"gremio": guild, "alianza": alliance, "desde": day, "hasta": day})
    return list(reversed(periods))


def _history_text(periods: list[dict], current_guild: str) -> str:
    if not periods:
        return f"🟢 **{current_guild}** (actual)\nSin kills ni muertes recientes para armar el historial."
    lines = []
    for n, p in enumerate(periods[:MAX_GUILDS]):
        alliance = f" [{p['alianza']}]" if p["alianza"] else ""
        if p["desde"].date() == p["hasta"].date():
            dates = f"visto el {p['desde']:%d/%m/%Y}"
        else:
            dates = f"del {p['desde']:%d/%m/%Y} al {p['hasta']:%d/%m/%Y}"
        mark = "🟢" if n == 0 and p["gremio"] == current_guild else "▫️"
        lines.append(f"{mark} **{p['gremio']}**{alliance} — {dates}")
    if periods[0]["gremio"] != current_guild:
        lines.insert(0, f"🟢 **{current_guild}** (actual, todavía sin kills ni muertes ahí)")
    oldest = periods[-1]["desde"]
    lines.append(f"\n*Según sus últimas kills y muertes (desde el {oldest:%d/%m/%Y}).*")
    return "\n".join(lines)[:1024]


def build_embed(member: discord.Member, typed_name: str, typed_guild: str, role_name: str | None,
                player: dict | None, kills: list[dict], deaths: list[dict], api_down: bool) -> discord.Embed:
    if player is None:
        embed = discord.Embed(
            title=f"🔎 {typed_name}",
            color=discord.Color.orange(),
            description=("⚠️ La API de Albion no respondió: no se pudo revisar el personaje."
                         if api_down else "❌ Este personaje **no existe** en Albion (servidor de América)."),
        )
    else:
        embed = discord.Embed(title=f"🔎 {player['Name']}", url=KILLBOARD_URL.format(player["Id"]),
                              color=discord.Color.blue())
        guild = player.get("GuildName") or "Sin gremio"
        alliance = f" [{player['AllianceName']}]" if player.get("AllianceName") else ""
        embed.description = f"**Gremio:** {guild}{alliance}"
        embed.add_field(name="⚔️ PvP", inline=True, value=(
            f"Kill fame: **{short(player.get('KillFame'))}**\n"
            f"Death fame: **{short(player.get('DeathFame'))}**\n"
            f"Ratio: **{player.get('FameRatio') or 0}**"))
        stats = player.get("LifetimeStatistics") or {}
        pve = stats.get("PvE") or {}
        embed.add_field(name="🐉 PvE", inline=True, value=(
            f"Total: **{short(pve.get('Total'))}**\n"
            f"Outlands: {short(pve.get('Outlands'))} · Royal: {short(pve.get('Royal'))}\n"
            f"Avalon: {short(pve.get('Avalon'))} · Mists: {short(pve.get('Mists'))}\n"
            f"Hellgate: {short(pve.get('Hellgate'))} · Corrupta: {short(pve.get('CorruptedDungeon'))}"))
        gathering = (stats.get("Gathering") or {}).get("All") or {}
        embed.add_field(name="⛏️ Otros", inline=True, value=(
            f"Recolección: **{short(gathering.get('Total'))}**\n"
            f"Crafteo: **{short((stats.get('Crafting') or {}).get('Total'))}**\n"
            f"Pesca: **{short(stats.get('FishingFame'))}**\n"
            f"Granja: **{short(stats.get('FarmingFame'))}**"))
        embed.add_field(name="🏰 Historial de gremios", inline=False,
                        value=_history_text(guild_history(kills, deaths), guild))
        embed.add_field(name="🔗 Enlaces", inline=False, value=(
            f"[Killboard oficial]({KILLBOARD_URL.format(player['Id'])}) · "
            f"[Murder Ledger]({MURDER_LEDGER_URL.format(player['Name'])})"))
    embed.add_field(name="📋 Registro", inline=False, value=(
        f"Discord: {member.mention}\n"
        f"Escribió: **{typed_name}** / gremio **{typed_guild}**\n"
        f"Rol asignado: **{role_name or '—'}**"))
    embed.set_footer(text="Datos de la API oficial de Albion")
    return embed


async def post_history(member: discord.Member, typed_name: str, typed_guild: str, role_name: str | None,
                       api_player: dict | None, api_down: bool):
    """Envía la ficha al canal de historial del servidor (si está configurado)."""
    channel = settings.channel(member.guild, "canal_historial")
    if channel is None:
        return
    region = settings.get(member.guild.id)["servidor_albion"]
    player, kills, deaths = None, [], []
    if api_player is not None:
        try:
            player = await albion.get_player(api_player["Id"], region)
            kills = await albion.get_player_events(api_player["Id"], "kills", region)
            deaths = await albion.get_player_events(api_player["Id"], "deaths", region)
        except albion.AlbionAPIError as e:
            log.warning("[HISTORIAL] Datos incompletos de %s: %s", api_player["Name"], e)
            player = player or api_player
    embed = build_embed(member, typed_name, typed_guild, role_name, player, kills, deaths, api_down)
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        log.info("[HISTORIAL] %s: ficha de %s enviada a #%s", member.guild.name, typed_name, channel.name)
    except discord.HTTPException as e:
        log.error("[HISTORIAL] No se pudo enviar la ficha de %s: %s", typed_name, e)
