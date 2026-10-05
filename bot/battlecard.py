"""Tarjeta del battle board: resumen visual de una batalla sobre el fondo de la vecindad."""

import asyncio
import io
from datetime import datetime

import aiohttp
from PIL import ImageDraw

from bot.killcard import WIDTH, _background, _icon, _panel, _text, font, short

ROW_H = 40
WEAPON = 32  # icono de arma en la tabla de jugadores
CARD_ICON = 72  # icono de arma en los destacados
MAX_PLAYERS = 15
MAX_GUILDS = 8

GREEN, RED, GOLD, GREY = (120, 220, 120), (255, 110, 100), (255, 215, 90), (210, 210, 210)


def parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text[:19] + "+00:00")


def player_stats(battle: dict, events: list[dict], guild_id: str) -> list[dict]:
    """Jugadores del gremio con kills, muertes, fama, daño, curación y arma."""
    extra: dict[str, dict] = {}

    def note(p: dict, damage: float = 0, heal: float = 0):
        info = extra.setdefault(p["Id"], {"dano": 0.0, "cura": 0.0, "arma": None})
        info["dano"] += damage or 0
        info["cura"] += heal or 0
        main = (p.get("Equipment") or {}).get("MainHand")
        if main:
            info["arma"] = main["Type"]

    for event in events:
        note(event["Killer"])
        note(event["Victim"])
        for p in event.get("Participants") or []:
            note(p, p.get("DamageDone"), p.get("SupportHealingDone"))

    out = []
    for pid, p in battle["players"].items():
        if p.get("guildId") != guild_id:
            continue
        info = extra.get(pid, {"dano": 0.0, "cura": 0.0, "arma": None})
        out.append({"nombre": p["name"], "kills": p["kills"], "muertes": p["deaths"],
                    "fama": p["killFame"], **info})
    return sorted(out, key=lambda s: (-s["kills"], -s["dano"], s["muertes"]))


def highlights(stats: list[dict]) -> list[tuple[str, dict, str, tuple]]:
    """(título, jugador, valor, color) de los destacados del gremio."""
    if not stats:
        return []
    out = []
    best = max(stats, key=lambda s: (s["kills"], s["fama"]))
    if best["kills"] > 0:
        out.append(("MÁS KILLS", best, f"{best['kills']} kills · {short(best['fama'])} fama", GOLD))
    dmg = max(stats, key=lambda s: s["dano"])
    if dmg["dano"] > 0:
        out.append(("MAYOR DAÑO", dmg, f"{short(dmg['dano'])} de daño", (255, 160, 80)))
    heal = max(stats, key=lambda s: s["cura"])
    if heal["cura"] > 0:
        out.append(("MÁS CURACIÓN", heal, f"{short(heal['cura'])} curados", GREEN))
    worst = max(stats, key=lambda s: (s["muertes"], -s["kills"], -s["dano"]))
    out.append(("PEOR DESEMPEÑO", worst, f"{worst['muertes']} muertes · {worst['kills']} kills", RED))
    return out


async def render_battle_card(battle: dict, guild_id: str, events: list[dict]) -> io.BytesIO:
    """Dibuja la tarjeta de la batalla y la devuelve como JPEG en memoria."""
    stats = player_stats(battle, events, guild_id)
    cards = highlights(stats)
    guilds = sorted(battle["guilds"].values(), key=lambda g: (-g["killFame"], -g["kills"]))
    players_per_guild: dict[str, int] = {}
    for p in battle["players"].values():
        players_per_guild[p.get("guildId")] = players_per_guild.get(p.get("guildId"), 0) + 1
    ours = battle["guilds"].get(guild_id, {"name": "Gremio", "kills": 0, "deaths": 0, "killFame": 0})

    shown_players = stats[:MAX_PLAYERS]
    shown_guilds = guilds[:MAX_GUILDS]
    if guild_id in battle["guilds"] and battle["guilds"][guild_id] not in shown_guilds:
        shown_guilds[-1] = battle["guilds"][guild_id]  # el gremio propio siempre se ve
    header_h, cards_h = 190, 150
    table_top = 20 + header_h + 20 + (cards_h + 20 if cards else 0)
    table_rows = max(len(shown_players), len(shown_guilds)) + (1 if len(stats) > MAX_PLAYERS else 0)
    height = table_top + 70 + table_rows * ROW_H + 40

    # Iconos de armas en paralelo.
    weapons = {s["arma"] for s in shown_players + [c[1] for c in cards] if s["arma"]}
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        icons = dict(zip(weapons, await asyncio.gather(
            *(_icon(session, {"Type": w}, CARD_ICON) for w in weapons))))

    card = _background(height)
    draw = ImageDraw.Draw(card)

    # --- Encabezado
    start, end = parse_time(battle["startTime"]), parse_time(battle["endTime"])
    minutes = max(1, round((end - start).total_seconds() / 60))
    if ours["kills"] > ours["deaths"]:
        result, color = "VICTORIA", GREEN
    elif ours["kills"] < ours["deaths"]:
        result, color = "DERROTA", RED
    else:
        result, color = "EMPATE", GREY
    _panel(card, (20, 20, WIDTH - 20, 20 + header_h), alpha=170)
    _text(draw, (50, 38), "☠ BATTLE BOARD", 22, fill=GOLD)
    _text(draw, (50, 66), ours["name"], 40)
    zone = battle.get("clusterName")
    sub = f"{start:%d/%m/%Y · %H:%M} UTC · {minutes} min" + (f" · {zone}" if zone else "")
    _text(draw, (50, 116), sub, 20, fill=GREY)
    _text(draw, (WIDTH - 50, 44), result, 52, fill=color, anchor="ra")
    _text(draw, (WIDTH - 50, 110), f"{ours['kills']} kills / {ours['deaths']} muertes", 24,
          fill=GREY, anchor="ra")
    totals = [("JUGADORES", str(len(battle["players"]))), ("KILLS", str(battle["totalKills"])),
              ("FAMA TOTAL", short(battle["totalFame"])), ("DEL GREMIO", str(len(stats))),
              ("GREMIOS", str(len(guilds)))]
    for n, (label, value) in enumerate(totals):
        x = 50 + n * 240
        _text(draw, (x, 150), value, 26, fill=GOLD)
        _text(draw, (x + draw.textlength(value, font=font(26)) + 10, 158), label, 16, fill=GREY)

    # --- Destacados
    if cards:
        top = 20 + header_h + 20
        width = (WIDTH - 40 - 20 * (len(cards) - 1)) // len(cards)
        for n, (title, player, value, accent) in enumerate(cards):
            x0 = 20 + n * (width + 20)
            _panel(card, (x0, top, x0 + width, top + cards_h), alpha=170)
            draw.rectangle((x0 + 14, top + 12, x0 + width - 14, top + 15), fill=accent)
            _text(draw, (x0 + 18, top + 26), title, 18, fill=accent)
            icon = icons.get(player["arma"])
            text_x = x0 + 18
            if icon:
                card.alpha_composite(icon.resize((CARD_ICON, CARD_ICON)), (x0 + 12, top + 58))
                text_x = x0 + 18 + CARD_ICON
            _text(draw, (text_x, top + 66), player["nombre"], 24)
            _text(draw, (text_x, top + 100), value, 17, fill=GREY)

    # --- Tablas: gremios (izquierda) y jugadores del gremio (derecha)
    left_w = 470
    _panel(card, (20, table_top, 20 + left_w, height - 20), alpha=170)
    _panel(card, (40 + left_w, table_top, WIDTH - 20, height - 20), alpha=170)
    y = table_top + 18
    right = 20 + left_w - 20
    guild_cols = [("JUG", right - 175), ("K / M", right - 110), ("FAMA", right)]
    _text(draw, (40, y), "GREMIOS", 20, fill=GOLD)
    for label, cx in guild_cols:
        _text(draw, (cx, y + 2), label, 16, fill=GREY, anchor="ra" if label != "K / M" else "ma")
    name_room = guild_cols[0][1] - 40 - 50  # espacio para el nombre antes de la columna JUG
    for n, g in enumerate(shown_guilds):
        ry = y + 46 + n * ROW_H
        fill = GOLD if g.get("id") == guild_id else (235, 235, 235)
        tag = f" [{g['alliance']}]" if g.get("alliance") else ""
        name = g["name"] + tag
        while draw.textlength(name, font=font(18)) > name_room and len(name) > 4:
            name = name[:-2] + "…"
        _text(draw, (40, ry), name, 18, fill=fill)
        values = [str(players_per_guild.get(g.get("id"), 0)), f"{g['kills']} / {g['deaths']}",
                  short(g["killFame"])]
        for (label, cx), value in zip(guild_cols, values):
            _text(draw, (cx, ry), value, 18, fill=fill, anchor="ra" if label != "K / M" else "ma")

    x0 = 60 + left_w
    _text(draw, (x0, y), "JUGADORES DEL GREMIO", 20, fill=GOLD)
    cols = [("K", 330), ("M", 385), ("FAMA", 470), ("DAÑO", 565), ("CURA", 655)]
    for label, cx in cols:
        _text(draw, (x0 + cx, y + 2), label, 16, fill=GREY, anchor="ra")
    for n, s in enumerate(shown_players):
        ry = y + 46 + n * ROW_H
        icon = icons.get(s["arma"])
        if icon:
            card.alpha_composite(icon.resize((WEAPON, WEAPON)), (x0, ry - 6))
        _text(draw, (x0 + WEAPON + 8, ry), s["nombre"][:18], 18)
        values = [str(s["kills"]), str(s["muertes"]), short(s["fama"]), short(s["dano"]), short(s["cura"])]
        for (label, cx), value in zip(cols, values):
            fill = GREEN if label == "K" and s["kills"] else RED if label == "M" and s["muertes"] else (235, 235, 235)
            _text(draw, (x0 + cx, ry), value, 18, fill=fill, anchor="ra")
    if len(stats) > MAX_PLAYERS:
        _text(draw, (x0, y + 46 + len(shown_players) * ROW_H), f"... y {len(stats) - MAX_PLAYERS} más", 16,
              fill=GREY)

    out = io.BytesIO()
    card.convert("RGB").save(out, "JPEG", quality=88)
    out.seek(0)
    return out
