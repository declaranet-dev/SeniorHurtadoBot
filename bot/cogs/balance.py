"""Balance de tortas de jamón del gremio: /balanceh, /rankh, /addh y /removeh.

Cada servidor de Discord lleva sus propios saldos en data/balance.json. Los
nombres son libres (los del juego) y no distinguen mayúsculas. /addh y
/removeh solo los usa el rol que maneja el balance (chespirito), y sus
respuestas solo las ve quien los usa; si hay canal de movimientos
configurado, ahí queda el registro para los demás chespirito.
"""

import json
import logging
import re
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from bot import settings
from bot.config import BALANCE_FILE, BALANCE_ROLE_NAME

log = logging.getLogger("seniorhurtadobot.balance")

UNIT = "tortas de jamón"
RANK_SIZE = 20
MAX_HISTORY = 2000  # movimientos que se guardan por servidor


# --- Datos


def _load() -> dict:
    """{"servidores": {id: {"saldos": {clave: {"nombre", "saldo"}}, "historial": [...]}}}"""
    if BALANCE_FILE.exists():
        return json.loads(BALANCE_FILE.read_text(encoding="utf-8"))
    return {"servidores": {}}


def _save(data: dict):
    BALANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = BALANCE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(BALANCE_FILE)


def _server(data: dict, guild_id: int) -> dict:
    server = data["servidores"].setdefault(str(guild_id), {})
    server.setdefault("saldos", {})
    server.setdefault("historial", [])
    return server


def key_of(name: str) -> str:
    return " ".join(name.split()).casefold()


def players(guild_id: int) -> list[dict]:
    """Jugadores del servidor, del que más tiene al que menos."""
    saldos = _server(_load(), guild_id)["saldos"]
    return sorted(saldos.values(), key=lambda p: (-p["saldo"], p["nombre"].casefold()))


def find(guild_id: int, name: str) -> dict | None:
    return _server(_load(), guild_id)["saldos"].get(key_of(name))


def move(guild_id: int, name: str, amount: int | None, by: discord.Member,
         reason: str | None) -> tuple[dict, bool]:
    """Suma amount al jugador (None lo deja en 0). Devuelve (jugador, si era nuevo)."""
    data = _load()
    server = _server(data, guild_id)
    key = key_of(name)
    new = key not in server["saldos"]
    player = server["saldos"].setdefault(key, {"nombre": " ".join(name.split()), "saldo": 0})
    before = player["saldo"]
    player["saldo"] = 0 if amount is None else before + amount
    server["historial"].append({
        "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "por": by.id, "por_nombre": str(by), "jugador": player["nombre"],
        "accion": "reiniciar" if amount is None else "agregar",
        "cantidad": player["saldo"] - before, "saldo": player["saldo"], "razon": reason,
    })
    server["historial"] = server["historial"][-MAX_HISTORY:]
    _save(data)
    return player, new


# --- Formato


def short(n: int) -> str:
    """3373326 -> 3.4M, 808100 -> 808.1k, 950 -> 950"""
    sign, n = ("-" if n < 0 else ""), abs(n)
    for size, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if n >= size:
            text = f"{n / size:.1f}".rstrip("0").rstrip(".")
            return f"{sign}{text}{suffix}"
    return f"{sign}{n}"


def parse_amount(text: str) -> int | None:
    """'1M', '1.5m', '500k', '1,500,000', '-250k' -> entero. None si no se entiende."""
    m = re.fullmatch(r"([+-]?)\s*([\d.,]+)\s*([kmb]?)", text.strip().lower())
    if not m:
        return None
    sign, number, suffix = m.groups()
    if suffix:
        number = number.replace(",", ".")
        if number.count(".") > 1:
            return None
        value = float(number) * {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[suffix]
    elif re.fullmatch(r"\d{1,3}([.,]\d{3})+|\d+", number):
        value = int(re.sub(r"[.,]", "", number))
    else:
        return None
    value = round(value)
    return -value if sign == "-" else value


# --- Permisos


def banker_role(guild: discord.Guild) -> discord.Role | None:
    return settings.role(guild, "rol_banquero") or discord.utils.find(
        lambda r: r.name.casefold() == BALANCE_ROLE_NAME, guild.roles)


def is_banker(member: discord.Member) -> bool:
    role = banker_role(member.guild)
    return role is not None and role in member.roles


async def deny(interaction: discord.Interaction) -> bool:
    """Responde y devuelve True si quien usa el comando no maneja el balance."""
    if is_banker(interaction.user):
        return False
    role = banker_role(interaction.guild)
    name = f"**{role.name}**" if role else f"**{BALANCE_ROLE_NAME}** (no existe en este servidor)"
    await interaction.response.send_message(f"⛔ Solo el rol {name} puede usar este comando.",
                                            ephemeral=True)
    return True


async def log_move(guild: discord.Guild, text: str):
    channel = settings.channel(guild, "canal_balance_log")
    if channel is None:
        return
    try:
        await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as e:
        log.error("[BALANCE] %s: no se pudo escribir en #%s: %s", guild.name, channel.name, e)


# --- Comandos


async def player_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    current = key_of(current)
    names = [p["nombre"] for p in players(interaction.guild_id) if current in key_of(p["nombre"])]
    return [app_commands.Choice(name=n[:100], value=n[:100]) for n in names[:25]]


class ConfirmReset(discord.ui.View):
    """Botones para confirmar /removeh (solo los ve quien lo usó)."""

    def __init__(self, name: str, reason: str | None):
        super().__init__(timeout=120)
        self.name, self.reason = name, reason

    @discord.ui.button(label="Sí, dejar en 0", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        before = find(interaction.guild_id, self.name)
        if before is None:
            await interaction.response.edit_message(content="Ese jugador ya no tiene registro.", view=None)
            return
        old = before["saldo"]
        player, _ = move(interaction.guild_id, self.name, None, interaction.user, self.reason)
        log.info("[BALANCE] %s: %s dejó en 0 a %s (tenía %d)", interaction.guild.name,
                 interaction.user, player["nombre"], old)
        await interaction.response.edit_message(
            content=f"🔴 **{player['nombre']}** quedó en **0 {UNIT}** (tenía {short(old)}).", view=None)
        reason = f" · Razón: {self.reason}" if self.reason else ""
        await log_move(interaction.guild, f"🔴 {interaction.user.mention} dejó en 0 a **{player['nombre']}** "
                                          f"(tenía {short(old)}){reason}")
        self.stop()

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Cancelado, no cambié nada.", view=None)
        self.stop()


class Balance(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="balanceh", description="Cuántas tortas de jamón tiene un jugador")
    @app_commands.describe(jugador="Nombre del jugador")
    @app_commands.autocomplete(jugador=player_autocomplete)
    @app_commands.guild_only()
    async def balanceh(self, interaction: discord.Interaction, jugador: app_commands.Range[str, 1, 64]):
        player = find(interaction.guild_id, jugador)
        if player is None:
            await interaction.response.send_message(
                f"**{jugador}** no tiene registro de balance.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"🥪 **{player['nombre']}** tiene **{short(player['saldo'])} {UNIT}**.")

    @app_commands.command(name="rankh", description=f"Top {RANK_SIZE} de tortas de jamón")
    @app_commands.guild_only()
    async def rankh(self, interaction: discord.Interaction):
        top = players(interaction.guild_id)[:RANK_SIZE]
        if not top:
            await interaction.response.send_message("Todavía no hay balances registrados.", ephemeral=True)
            return
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = [f"{medals.get(n, f'`{n:>2}.`')} **{p['nombre']}** — {short(p['saldo'])}"
                 for n, p in enumerate(top, 1)]
        embed = discord.Embed(title=f"🏆 Ranking de {UNIT}", description="\n".join(lines),
                              color=discord.Color.gold())
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="addh", description="Agrega tortas de jamón a un jugador (rol chespirito)")
    @app_commands.describe(jugador="Jugador (si no existe, se crea)",
                           cantidad="Ej.: 1M, 500k, 1500000; negativo para quitar (-250k)",
                           razon="Motivo (opcional)")
    @app_commands.autocomplete(jugador=player_autocomplete)
    @app_commands.guild_only()
    async def addh(self, interaction: discord.Interaction, jugador: app_commands.Range[str, 1, 64],
                   cantidad: app_commands.Range[str, 1, 20], razon: app_commands.Range[str, 1, 200] | None = None):
        if await deny(interaction):
            return
        amount = parse_amount(cantidad)
        if not amount:
            await interaction.response.send_message(
                f"No entendí la cantidad **{cantidad}**. Usa por ejemplo `1M`, `500k` o `1500000`.",
                ephemeral=True)
            return
        current = find(interaction.guild_id, jugador)
        if current is None and amount < 0:
            await interaction.response.send_message(f"**{jugador}** no tiene balance para quitarle.",
                                                    ephemeral=True)
            return
        if current is not None and current["saldo"] + amount < 0:
            await interaction.response.send_message(
                f"**{current['nombre']}** solo tiene {short(current['saldo'])}. "
                f"Para dejarlo en 0 usa `/removeh`.", ephemeral=True)
            return
        player, new = move(interaction.guild_id, jugador, amount, interaction.user, razon)
        verb, icon = ("Se agregaron", "✅") if amount > 0 else ("Se quitaron", "🔴")
        reason = f"\nRazón: {razon}" if razon else ""
        note = " (jugador nuevo)" if new else ""
        log.info("[BALANCE] %s: %s %+d a %s -> %d", interaction.guild.name, interaction.user,
                 amount, player["nombre"], player["saldo"])
        await interaction.response.send_message(
            f"{icon} {verb} **{short(abs(amount))} {UNIT}** a **{player['nombre']}**{note}.\n"
            f"Nuevo saldo: **{short(player['saldo'])}**{reason}", ephemeral=True)
        await log_move(interaction.guild,
                       f"{icon} {interaction.user.mention}: {verb.lower()} {short(abs(amount))} a "
                       f"**{player['nombre']}**{note} · nuevo saldo {short(player['saldo'])}{reason}")

    @app_commands.command(name="removeh", description="Deja en 0 el balance de un jugador (rol chespirito)")
    @app_commands.describe(jugador="Jugador", razon="Motivo (opcional)")
    @app_commands.autocomplete(jugador=player_autocomplete)
    @app_commands.guild_only()
    async def removeh(self, interaction: discord.Interaction, jugador: app_commands.Range[str, 1, 64],
                      razon: app_commands.Range[str, 1, 200] | None = None):
        if await deny(interaction):
            return
        player = find(interaction.guild_id, jugador)
        if player is None:
            await interaction.response.send_message(f"**{jugador}** no tiene registro de balance.",
                                                    ephemeral=True)
            return
        await interaction.response.send_message(
            f"¿Dejar a **{player['nombre']}** en 0? Ahora tiene **{short(player['saldo'])} {UNIT}**.",
            view=ConfirmReset(player["nombre"], razon), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Balance(bot))
