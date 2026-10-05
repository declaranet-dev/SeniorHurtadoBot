"""Punto de entrada de SeniorHurtadoBot."""

import logging
import sys

import discord

from bot.client import SeniorHurtadoBot
from bot.config import get_token


def main():
    # Consola de Windows: forzar UTF-8 para que se vean los acentos.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    token = get_token()
    # Se intenta primero con todos los intents privilegiados. Si alguno no
    # está activado en el Developer Portal, se arranca sin él y se avisa.
    for message_content, members in [(True, True), (True, False), (False, False)]:
        try:
            SeniorHurtadoBot(message_content, members).run(token, log_handler=None)
            return
        except discord.LoginFailure:
            logging.error("Token inválido. Revisa DISCORD_TOKEN en .env.")
            return
        except discord.PrivilegedIntentsRequired:
            logging.warning(
                "Falta un intent privilegiado en el Developer Portal "
                "(Bot > Privileged Gateway Intents). Reintentando con menos "
                "funciones..."
            )


if __name__ == "__main__":
    main()
