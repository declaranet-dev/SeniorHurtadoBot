"""/configuracion: asistente para configurar el bot en cada servidor de Discord."""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot import albion, settings

log = logging.getLogger("seniorhurtadobot.configuracion")

ALBION_SERVERS = {"americas": "América", "europe": "Europa", "asia": "Asia"}

# Cada página del asistente: título y claves que se editan en ella.
PAGES = [
    ("1/4 · Canales de la comunidad", ["canal_bienvenida", "canal_registro", "canal_eventos"]),
    ("2/4 · Canales del killbot", ["canal_kills", "canal_muertes", "canal_batallas", "canal_gucci"]),
    ("3/4 · Roles", ["rol_nuevo", "rol_miembro", "rol_externo", "rol_admin_eventos"]),
    ("4/4 · Gremio de Albion", []),
]


def channel_problem(guild: discord.Guild, channel: discord.TextChannel) -> str | None:
    perms = channel.permissions_for(guild.me)
    missing = [name for name, ok in (("ver", perms.view_channel), ("escribir", perms.send_messages),
                                     ("insertar enlaces", perms.embed_links),
                                     ("adjuntar archivos", perms.attach_files)) if not ok]
    return f"al bot le falta: {', '.join(missing)}" if missing else None


def role_problem(guild: discord.Guild, role: discord.Role, key: str) -> str | None:
    if key == "rol_admin_eventos":
        return None  # el bot no asigna este rol, solo lo revisa
    if not guild.me.guild_permissions.manage_roles:
        return "el bot no tiene 'Gestionar roles'"
    if role >= guild.me.top_role:
        return "está por encima del rol del bot"
    return None


def summary_embed(guild: discord.Guild, page: int | None) -> discord.Embed:
    """Resumen de toda la configuración, con avisos de lo que falta o falla."""
    config = settings.get(guild.id)
    title = "⚙️ Configuración de SeniorHurtadoBot"
    if page is not None:
        title += f" · {PAGES[page][0]}"
    embed = discord.Embed(title=title, color=discord.Color.blurple())

    lines = []
    for key, label in settings.CHANNELS.items():
        channel = settings.channel(guild, key)
        if channel is None:
            lines.append(f"❌ {label}: sin configurar")
        else:
            problem = channel_problem(guild, channel)
            lines.append(f"{'⚠️' if problem else '✅'} {label}: {channel.mention}"
                         + (f" ({problem})" if problem else ""))
    embed.add_field(name="Canales", value="\n".join(lines), inline=False)

    lines = []
    for key, label in settings.ROLES.items():
        role = settings.role(guild, key)
        if role is None:
            lines.append(f"❌ {label}: sin configurar")
        else:
            problem = role_problem(guild, role, key)
            lines.append(f"{'⚠️' if problem else '✅'} {label}: {role.mention}"
                         + (f" ({problem})" if problem else ""))
    embed.add_field(name="Roles", value="\n".join(lines), inline=False)

    gremio = config["gremio_nombre"]
    embed.add_field(name="Albion", inline=False, value=(
        f"{'✅' if gremio else '❌'} Gremio: {f'**{gremio}**' if gremio else 'sin configurar'}\n"
        f"🌎 Servidor: {ALBION_SERVERS.get(config['servidor_albion'], config['servidor_albion'])}\n"
        f"☠️ Battle board: batallas de {config['batalla_min_jugadores']}+ jugadores\n"
        f"💵 Gucci kills: desde {config['gucci_min_fama']:,} de fama"
    ))
    embed.set_footer(text="Lo que quede sin configurar simplemente no se usa en este servidor.")
    return embed


class SettingSelect:
    """Lista desplegable de canal o rol que guarda su valor al elegir."""

    @staticmethod
    def build(guild: discord.Guild, key: str) -> discord.ui.Item:
        current = settings.get(guild.id)[key]
        if key in settings.CHANNELS:
            select = discord.ui.ChannelSelect(
                placeholder=f"Canal de {settings.CHANNELS[key].lower()}",
                channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                min_values=0, max_values=1,
                default_values=[discord.Object(current)] if settings.channel(guild, key) else [],
            )
        else:
            select = discord.ui.RoleSelect(
                placeholder=settings.ROLES[key],
                min_values=0, max_values=1,
                default_values=[discord.Object(current)] if settings.role(guild, key) else [],
            )

        async def callback(interaction: discord.Interaction):
            value = select.values[0].id if select.values else None
            settings.update(interaction.guild.id, **{key: value})
            log.info("[CONFIG] %s: %s = %s (por %s)", interaction.guild.name, key, value, interaction.user)
            view: ConfigView = select.view
            await interaction.response.edit_message(embed=summary_embed(interaction.guild, view.page),
                                                    view=ConfigView(interaction.guild, view.page))

        select.callback = callback
        return select


class AlbionModal(discord.ui.Modal, title="Gremio de Albion"):
    def __init__(self, guild: discord.Guild, page: int):
        super().__init__()
        self.page = page
        config = settings.get(guild.id)
        self.gremio = discord.ui.TextInput(label="Nombre del gremio (como sale en Albion)",
                                           default=config["gremio_nombre"] or "", max_length=64)
        self.servidor = discord.ui.TextInput(label="Servidor: americas, europe o asia",
                                             default=config["servidor_albion"], max_length=10)
        self.min_jugadores = discord.ui.TextInput(label="Battle board: jugadores mínimos",
                                                  default=str(config["batalla_min_jugadores"]), max_length=3)
        self.gucci = discord.ui.TextInput(label="Gucci kills: fama mínima",
                                          default=str(config["gucci_min_fama"]), max_length=12)
        for item in (self.gremio, self.servidor, self.min_jugadores, self.gucci):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        region = self.servidor.value.strip().lower()
        if region not in ALBION_SERVERS:
            await interaction.response.send_message(
                "❌ El servidor debe ser **americas**, **europe** o **asia**.", ephemeral=True)
            return
        min_players = self.min_jugadores.value.strip()
        gucci = self.gucci.value.strip().replace(",", "").replace(".", "")
        if not min_players.isdigit() or not gucci.isdigit():
            await interaction.response.send_message(
                "❌ Los jugadores mínimos y la fama mínima deben ser números.", ephemeral=True)
            return

        name = self.gremio.value.strip()
        await interaction.response.defer()
        try:
            guilds = await albion.find_guild(name, region)
        except albion.AlbionAPIError:
            await interaction.followup.send("⚠️ La API de Albion no responde. Inténtalo en unos minutos.",
                                            ephemeral=True)
            return
        exact = [g for g in guilds if g.get("Name", "").lower() == name.lower()]
        if not exact:
            options = ", ".join(f"**{g['Name']}**" for g in guilds[:5])
            await interaction.followup.send(
                f"❌ No encontré el gremio **{name}** en {ALBION_SERVERS[region]}."
                + (f" ¿Quisiste decir: {options}?" if options else ""), ephemeral=True)
            return

        found = exact[0]
        settings.update(interaction.guild.id, gremio_id=found["Id"], gremio_nombre=found["Name"],
                        servidor_albion=region, batalla_min_jugadores=int(min_players),
                        gucci_min_fama=int(gucci))
        log.info("[CONFIG] %s: gremio %s (%s, %s) por %s", interaction.guild.name, found["Name"],
                 found["Id"], region, interaction.user)
        await interaction.edit_original_response(embed=summary_embed(interaction.guild, self.page),
                                                 view=ConfigView(interaction.guild, self.page))


class ConfigView(discord.ui.View):
    def __init__(self, guild: discord.Guild, page: int = 0):
        super().__init__(timeout=900)
        self.page = page
        for row, key in enumerate(PAGES[page][1]):
            item = SettingSelect.build(guild, key)
            item.row = row
            self.add_item(item)
        if page == len(PAGES) - 1:
            button = discord.ui.Button(label="Elegir gremio y opciones", emoji="🏰",
                                       style=discord.ButtonStyle.primary, row=0)

            async def open_modal(interaction: discord.Interaction):
                await interaction.response.send_modal(AlbionModal(interaction.guild, self.page))

            button.callback = open_modal
            self.add_item(button)
        self.previous.disabled = page == 0
        self.next.disabled = page == len(PAGES) - 1

    async def _go(self, interaction: discord.Interaction, page: int):
        await interaction.response.edit_message(embed=summary_embed(interaction.guild, page),
                                                view=ConfigView(interaction.guild, page))

    @discord.ui.button(label="Anterior", emoji="◀️", style=discord.ButtonStyle.secondary, row=4)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._go(interaction, self.page - 1)

    @discord.ui.button(label="Siguiente", emoji="▶️", style=discord.ButtonStyle.secondary, row=4)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._go(interaction, self.page + 1)

    @discord.ui.button(label="Terminar", emoji="✅", style=discord.ButtonStyle.success, row=4)
    async def finish(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = summary_embed(interaction.guild, None)
        embed.description = "Configuración guardada. Puedes cambiarla cuando quieras con `/configuracion`."
        await interaction.response.edit_message(embed=embed, view=None)


class Configuracion(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="configuracion", description="Configura canales, roles y gremio del bot")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def configuracion(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "Solo quien puede gestionar el servidor puede configurar el bot.", ephemeral=True)
            return
        await interaction.response.send_message(embed=summary_embed(interaction.guild, 0),
                                                view=ConfigView(interaction.guild, 0), ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            settings.migrate_from_env(guild)


async def setup(bot: commands.Bot):
    await bot.add_cog(Configuracion(bot))
