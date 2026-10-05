"""Comandos generales del bot: ayuda con instalación y uso."""

import discord
from discord import app_commands
from discord.ext import commands

INVITE_URL = ("https://discord.com/oauth2/authorize?client_id=1556691846376325131"
              "&scope=bot+applications.commands&permissions=17601178749952")

INSTALL_TEXT = (
    f"1. **Invita el bot** con [este enlace]({INVITE_URL}). Ya pide los permisos que necesita: "
    "gestionar roles, apodos y eventos, ver canales, escribir, insertar enlaces y adjuntar archivos.\n"
    "2. **Sube el rol del bot** por encima de los roles que va a asignar "
    "(Ajustes del servidor > Roles). Si no, no podrá darlos.\n"
    "3. **Usa `/configuracion`** (necesitas el permiso *Gestionar servidor*). Son 4 páginas:\n"
    "   • Canales de la comunidad: bienvenida, registro, eventos\n"
    "   • Canales del killbot: kills, muertes, battle board, gucci kills\n"
    "   • Roles: al entrar, miembros del gremio, otros gremios, quién crea eventos\n"
    "   • Gremio de Albion, servidor (América/Europa/Asia) y mínimos\n"
    "4. **Revisa el resumen**: ✅ listo, ❌ sin configurar, ⚠️ falta un permiso. "
    "Lo que no configures simplemente no se usa."
)

USAGE_TEXT = (
    "**Para todos**\n"
    "`/registro name guild [alianza]`: registra tu personaje de Albion. El bot lo valida con la API, "
    "te da el rol de tu gremio y te cambia el apodo. También `!registro`, que abre un formulario.\n"
    "`/helphurtado`: muestra esta ayuda (también `!hurtadohelp`).\n"
    "\n"
    "**Para quien crea eventos**\n"
    "`/evento`: formulario con nombre, hora UTC, participantes, roles necesarios e información extra. "
    "Crea el evento de Discord y un anuncio con botones para anotarse por rol. También `!evento`.\n"
    "\n"
    "**Para administradores**\n"
    "`/configuracion`: canales, roles y gremio del bot en este servidor.\n"
    "\n"
    "**Automático**\n"
    "• Bienvenida con rol a cada miembro nuevo.\n"
    "• Kills y muertes del gremio con tarjeta de equipo, fama y valor en plata.\n"
    "• Gucci kills de todo Albion y resumen de batallas grandes (battle board)."
)


def help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📖 Ayuda de SeniorHurtadoBot",
        description="Bot para gremios de Albion Online: registro, eventos, killbot y battle board.",
        color=discord.Color.gold(),
    )
    embed.add_field(name="🛠️ Instalación (administradores)", value=INSTALL_TEXT, inline=False)
    embed.add_field(name="🎮 Uso", value=USAGE_TEXT, inline=False)
    return embed


class General(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="hurtadohelp", aliases=["helphurtado"])
    async def hurtadohelp(self, ctx: commands.Context):
        await ctx.send(embed=help_embed())

    @app_commands.command(name="helphurtado", description="Instrucciones de instalación y uso del bot")
    async def helphurtado(self, interaction: discord.Interaction):
        # Solo lo ve quien lo pide, para no llenar el canal.
        await interaction.response.send_message(embed=help_embed(), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))
