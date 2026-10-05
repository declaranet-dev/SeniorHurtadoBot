"""Comandos generales del bot."""

from discord.ext import commands

HELP_TEXT = (
    "**SeniorHurtadoBot**\n"
    "Comandos disponibles:\n"
    "\n"
    "`!hurtadohelp` - Muestra esta ayuda.\n"
    "`/registro name guild [alianza]` - Registra tu jugador de Albion y tu gremio (también `!registro`).\n"
    "`/evento` - Crea un evento del gremio, solo Admin (también `!evento`).\n"
    "\n"
    "Más comandos próximamente."
)


class General(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="hurtadohelp")
    async def hurtadohelp(self, ctx: commands.Context):
        await ctx.send(HELP_TEXT)


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))
