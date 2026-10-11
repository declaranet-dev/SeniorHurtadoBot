"""Balance de tortas de jamón del gremio: /balanceh, /rankh, /addh, /removeh y /prestamoh.

Cada servidor de Discord lleva sus propios saldos en data/balance.json. Los
nombres son libres (los del juego) y no distinguen mayúsculas. /addh,
/removeh y /prestamoh solo los usa el rol que maneja el balance (chespirito).

Préstamos: /prestamoh suma al saldo y deja la misma cantidad como deuda. Lo
que después se agregue con /addh paga primero la deuda y solo el resto llega
al saldo (deuda de 1M y se agregan 1.2M: se suman 200k).
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
    """{"servidores": {id: {"saldos": {clave: {"nombre", "saldo", "deuda"}}, "historial": [...]}}}"""
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


def move(guild_id: int, name: str, action: str, amount: int, by: discord.Member,
         reason: str | None) -> tuple[dict, bool, int]:
    """Aplica un movimiento al jugador y lo anota en el historial.

    action: "agregar" (amount puede ser negativo), "reiniciar" (saldo a 0) o
    "prestamo". Devuelve (jugador, si era nuevo, lo que se usó para pagar deuda).
    """
    data = _load()
    server = _server(data, guild_id)
    key = key_of(name)
    new = key not in server["saldos"]
    player = server["saldos"].setdefault(key, {"nombre": " ".join(name.split()), "saldo": 0})
    player.setdefault("deuda", 0)
    before, paid = player["saldo"], 0
    if action == "reiniciar":
        player["saldo"] = 0
    elif action == "prestamo":
        player["saldo"] += amount
        player["deuda"] += amount
    else:
        if amount > 0:
            paid = min(player["deuda"], amount)
            player["deuda"] -= paid
        player["saldo"] += amount - paid
    server["historial"].append({
        "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "por": by.id, "por_nombre": str(by), "jugador": player["nombre"], "accion": action,
        "cantidad": player["saldo"] - before, "pago_deuda": paid, "saldo": player["saldo"],
        "deuda": player["deuda"], "razon": reason,
    })
    server["historial"] = server["historial"][-MAX_HISTORY:]
    _save(data)
    return player, new, paid


def debt_note(player: dict) -> str:
    return f" · 📉 debe {money(player['deuda'])}" if player.get("deuda") else ""


# --- Formato


def money(n: int) -> str:
    """Cantidad exacta con separador de miles: 1520412 -> 1,520,412"""
    return f"{n:,}"


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
        player, _, _ = move(interaction.guild_id, self.name, "reiniciar", 0, interaction.user, self.reason)
        log.info("[BALANCE] %s: %s dejó en 0 a %s (tenía %d)", interaction.guild.name,
                 interaction.user, player["nombre"], old)
        await interaction.response.edit_message(content="Hecho.", view=None)
        reason = f"\nRazón: {self.reason}" if self.reason else ""
        await interaction.followup.send(
            f"💸 **{player['nombre']}** quedó en **🪙 0 {UNIT}** (tenía {money(old)}){debt_note(player)}.{reason}")
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
            f"💰 **{player['nombre']}** tiene **🪙 {money(player['saldo'])} {UNIT}** 🥪{debt_note(player)}")

    @app_commands.command(name="rankh", description=f"Top {RANK_SIZE} de tortas de jamón")
    @app_commands.guild_only()
    async def rankh(self, interaction: discord.Interaction):
        top = players(interaction.guild_id)[:RANK_SIZE]
        if not top:
            await interaction.response.send_message("Todavía no hay balances registrados.", ephemeral=True)
            return
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = [f"{medals.get(n, f'`{n:>2}.`')} **{p['nombre']}** — 🪙 {money(p['saldo'])}{debt_note(p)}"
                 for n, p in enumerate(top, 1)]
        embed = discord.Embed(title=f"🏆💰 Ranking de {UNIT}", description="\n".join(lines),
                              color=discord.Color.gold())
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="addh", description="Agrega tortas de jamón a un jugador (rol chespirito)")
    @app_commands.describe(jugador="Jugador (si no existe, se crea)",
                           cantidad="Ej.: 1,520,412 o 1520412 (también 1M o 500k); negativo para quitar",
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
                f"No entendí la cantidad **{cantidad}**. Usa por ejemplo `1,520,412`, `1520412` o `1M`.",
                ephemeral=True)
            return
        current = find(interaction.guild_id, jugador)
        if current is None and amount < 0:
            await interaction.response.send_message(f"**{jugador}** no tiene balance para quitarle.",
                                                    ephemeral=True)
            return
        if current is not None and current["saldo"] + amount < 0:
            await interaction.response.send_message(
                f"**{current['nombre']}** solo tiene {money(current['saldo'])}. "
                f"Para dejarlo en 0 usa `/removeh`.", ephemeral=True)
            return
        player, new, paid = move(interaction.guild_id, jugador, "agregar", amount, interaction.user, razon)
        note = " (jugador nuevo)" if new else ""
        reason = f"\nRazón: {razon}" if razon else ""
        if amount > 0:
            text = f"💵 Se agregaron **🪙 {money(amount - paid)} {UNIT}** a **{player['nombre']}**{note}."
            if paid:
                text += (f"\nDe los {money(amount)}, {money(paid)} pagaron su préstamo"
                         + (f"; aún debe {money(player['deuda'])}." if player["deuda"] else "; ya no debe nada."))
        else:
            text = f"💸 Se quitaron **🪙 {money(-amount)} {UNIT}** a **{player['nombre']}**."
        log.info("[BALANCE] %s: %s %+d a %s (deuda pagada %d) -> %d", interaction.guild.name,
                 interaction.user, amount, player["nombre"], paid, player["saldo"])
        await interaction.response.send_message(
            f"{text}\n🏦 Nuevo saldo: **🪙 {money(player['saldo'])}**{debt_note(player)}{reason}")

    @app_commands.command(name="prestamoh", description="Presta tortas de jamón a un jugador (rol chespirito)")
    @app_commands.describe(jugador="Jugador (si no existe, se crea)", cantidad="Ej.: 1,000,000 o 1M",
                           razon="Motivo (opcional)")
    @app_commands.autocomplete(jugador=player_autocomplete)
    @app_commands.guild_only()
    async def prestamoh(self, interaction: discord.Interaction, jugador: app_commands.Range[str, 1, 64],
                        cantidad: app_commands.Range[str, 1, 20],
                        razon: app_commands.Range[str, 1, 200] | None = None):
        if await deny(interaction):
            return
        amount = parse_amount(cantidad)
        if not amount or amount < 0:
            await interaction.response.send_message(
                f"No entendí la cantidad **{cantidad}**. Usa por ejemplo `1,000,000` o `1M`.", ephemeral=True)
            return
        player, new, _ = move(interaction.guild_id, jugador, "prestamo", amount, interaction.user, razon)
        note = " (jugador nuevo)" if new else ""
        reason = f"\nRazón: {razon}" if razon else ""
        log.info("[BALANCE] %s: %s prestó %d a %s -> saldo %d, deuda %d", interaction.guild.name,
                 interaction.user, amount, player["nombre"], player["saldo"], player["deuda"])
        await interaction.response.send_message(
            f"🤝💵 Se prestaron **🪙 {money(amount)} {UNIT}** a **{player['nombre']}**{note}.\n"
            f"🏦 Nuevo saldo: **🪙 {money(player['saldo'])}** · 📉 debe **{money(player['deuda'])}**, que se "
            f"descontará de lo próximo que se le agregue.{reason}")

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
            f"¿Dejar a **{player['nombre']}** en 0? Ahora tiene **{money(player['saldo'])} {UNIT}**.",
            view=ConfirmReset(player["nombre"], razon), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Balance(bot))
