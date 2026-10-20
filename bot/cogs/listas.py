"""Listas del servidor: /chavodel8 (miembros), /kikin (sin registrar) y /alianza."""

import json
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from bot import settings
from bot.config import REGISTROS_FILE


def _registros(guild: discord.Guild) -> dict:
    if REGISTROS_FILE.exists():
        return json.loads(REGISTROS_FILE.read_text(encoding="utf-8")).get(str(guild.id)) or {}
    return {}


def _names(members: list[discord.Member]) -> str:
    """Nombres separados por comas, sin pasar del límite de Discord."""
    names = sorted((m.display_name for m in members), key=str.lower)
    text = ", ".join(names)
    if len(text) <= 4000:
        return text or "Nadie."
    out = ""
    for n, name in enumerate(names):
        if len(out) + len(name) > 3950:
            return out + f"… y {len(names) - n} más"
        out += name + ", "
    return out.rstrip(", ")


def _with_role(guild: discord.Guild, key: str) -> tuple[discord.Role | None, list[discord.Member]]:
    role = settings.role(guild, key)
    if role is None:
        return None, []
    return role, [m for m in role.members if not m.bot]


class Listas(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _no_role(self, interaction: discord.Interaction, what: str):
        await interaction.response.send_message(f"No está configurado el rol de {what} (/configuracion).",
                                                ephemeral=True)

    @app_commands.command(name="chavodel8", description="Lista de los miembros del gremio")
    @app_commands.guild_only()
    async def chavodel8(self, interaction: discord.Interaction):
        role, members = _with_role(interaction.guild, "rol_miembro")
        if role is None:
            await self._no_role(interaction, "miembros")
            return
        embed = discord.Embed(title=f"🏠 {role.name}: {len(members)}", color=role.color,
                              description=_names(members))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="kikin", description="Quiénes aún no se registran")
    @app_commands.guild_only()
    async def kikin(self, interaction: discord.Interaction):
        role, members = _with_role(interaction.guild, "rol_nuevo")
        if role is None:
            await self._no_role(interaction, "nuevos")
            return
        now = datetime.now(timezone.utc)
        members.sort(key=lambda m: m.joined_at or now)
        lines = []
        for m in members:
            days = (now - m.joined_at).days if m.joined_at else 0
            lines.append(f"{m.mention} · entró hace {days} días" if days != 1 else f"{m.mention} · entró ayer")
        text = "\n".join(lines) or "Todos están registrados. 🎉"
        if len(text) > 4000:
            text = text[:3950].rsplit("\n", 1)[0] + "\n…"
        embed = discord.Embed(title=f"🆕 Sin registrar ({role.name}): {len(members)}",
                              color=discord.Color.orange(), description=text)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="alianza", description="Miembros de la alianza, por gremio")
    @app_commands.guild_only()
    async def alianza(self, interaction: discord.Interaction):
        guild = interaction.guild
        role, members = _with_role(guild, "rol_aliado")
        if role is None:
            await self._no_role(interaction, "alianza")
            return
        member_role = settings.role(guild, "rol_miembro")
        members = [m for m in members if member_role is None or member_role not in m.roles]
        registros = _registros(guild)
        groups: dict[str, list[discord.Member]] = {}
        for m in members:
            name = (registros.get(str(m.id)) or {}).get("gremio") or "Sin registro"
            groups.setdefault(name, []).append(m)
        embed = discord.Embed(title=f"🤝 {role.name}: {len(members)}", color=role.color)
        for name, group in sorted(groups.items(), key=lambda item: -len(item[1]))[:25]:
            value = _names(group)
            embed.add_field(name=f"{name} ({len(group)})", inline=False,
                            value=value if len(value) <= 1024 else value[:1000].rsplit(", ", 1)[0] + "…")
        if not members:
            embed.description = "Nadie tiene el rol de alianza."
        embed.set_footer(text="Gremio según su registro")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Listas(bot))
