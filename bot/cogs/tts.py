"""TTS: el bot entra al canal de voz y lee en voz alta lo que se escribe en un canal temporal.

/jointts mete al bot en tu canal de voz y crea el canal de texto temporal
BotTTSHurtado (justo debajo de #bot-tts). Lo que ahí escriban los registrados
se lee con una voz de Edge TTS en español: "Edgar dice: ...". /desconectar
saca al bot y borra el canal; también sale solo si el canal de voz se queda
vacío un rato, y al reiniciarse borra el canal que haya quedado.
/configtts elige la voz, la velocidad y el volumen de cada servidor.
"""

import asyncio
import json
import logging
import re
import tempfile
import time
from pathlib import Path

import discord
import edge_tts
from discord import app_commands
from discord.ext import commands

from bot import settings
from bot.config import (COMMAND_PREFIX, REGISTROS_FILE, TTS_CHANNEL_NAME, TTS_DEFAULT_VOICE,
                        TTS_EMPTY_SECONDS, TTS_FILE, TTS_MAX_CHARS, TTS_QUEUE_SIZE)

log = logging.getLogger("seniorhurtadobot.tts")

AUDIO_DIR = Path(tempfile.gettempdir()) / "seniorhurtadobot-tts"
VOICES_CACHE_SECONDS = 24 * 3600


# --- Canales temporales que siguen abiertos (para borrarlos si el bot se reinicia)


def _load() -> dict:
    """{id del servidor: id del canal temporal}"""
    if TTS_FILE.exists():
        return json.loads(TTS_FILE.read_text(encoding="utf-8"))
    return {}


def _save(data: dict):
    TTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(TTS_FILE)


def _remember(guild_id: int, channel_id: int | None):
    data = _load()
    if channel_id is None:
        data.pop(str(guild_id), None)
    else:
        data[str(guild_id)] = channel_id
    _save(data)


# --- Quién puede usarlo


def is_registered(member: discord.Member) -> bool:
    """Registrado con /registro, con un rol del registro, o administrador."""
    if member.guild_permissions.manage_guild:
        return True
    final = {settings.get(member.guild.id).get(k) for k in ("rol_miembro", "rol_aliado", "rol_externo")}
    if any(r.id in final for r in member.roles):
        return True
    if REGISTROS_FILE.exists():
        data = json.loads(REGISTROS_FILE.read_text(encoding="utf-8"))
        return str(member.id) in data.get(str(member.guild.id), {})
    return False


NOT_REGISTERED = "Solo los jugadores registrados pueden usar el TTS. Regístrate primero con `/registro`."


# --- Voces de Edge en español

_voices: list[dict] = []
_voices_at = 0.0


async def spanish_voices() -> list[dict]:
    """Voces de Edge TTS en español (se piden como mucho una vez al día)."""
    global _voices, _voices_at
    if not _voices or time.time() - _voices_at > VOICES_CACHE_SECONDS:
        try:
            found = await edge_tts.list_voices()
            _voices = sorted((v for v in found if v["Locale"].startswith("es-")),
                             key=lambda v: v["ShortName"])
            _voices_at = time.time()
        except Exception as e:  # sin conexión con Edge: se usa lo último que se tenga
            log.warning("[TTS] No pude leer la lista de voces de Edge: %s", e)
    return _voices


def voice_label(v: dict) -> str:
    gender = "mujer" if v.get("Gender") == "Female" else "hombre"
    return f"{v['ShortName']} ({gender})"


async def voice_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    current = current.casefold()
    return [app_commands.Choice(name=voice_label(v), value=v["ShortName"])
            for v in await spanish_voices() if current in v["ShortName"].casefold()][:25]


def signed(n: int) -> str:
    return f"{n:+d}%"


# --- Texto que se lee

_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_CUSTOM_EMOJI = re.compile(r"<a?:\w+:\d+>")
_MARKDOWN = re.compile(r"[*_~`|>#]+")


def speech_text(message: discord.Message) -> str:
    """Lo que se lee de un mensaje: sin enlaces, emojis propios ni formato, y como mucho TTS_MAX_CHARS."""
    text = _URL.sub(" ", message.clean_content)
    text = _CUSTOM_EMOJI.sub(" ", text)
    text = _MARKDOWN.sub(" ", text)
    text = " ".join(text.split())[:TTS_MAX_CHARS]
    if not text:
        return ""
    return f"{message.author.display_name} dice: {text}"


# --- Sesiones (una por servidor)


class Session:
    def __init__(self, voice: discord.VoiceClient, channel: discord.TextChannel):
        self.voice = voice
        self.channel_id = channel.id
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=TTS_QUEUE_SIZE)
        self.worker: asyncio.Task | None = None
        self.empty_timer: asyncio.Task | None = None


class TTS(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: dict[int, Session] = {}
        self._cleaned = False

    async def cog_unload(self):
        for guild_id in list(self.sessions):
            await self.close(guild_id, "el módulo se descargó")

    # --- Reproducción

    async def _speak(self, guild_id: int, session: Session, text: str):
        config = settings.get(guild_id)
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        path = AUDIO_DIR / f"{guild_id}.mp3"
        await edge_tts.Communicate(text, config["tts_voz"], rate=signed(config["tts_velocidad"]),
                                   volume=signed(config["tts_volumen"])).save(str(path))
        if not session.voice.is_connected():
            return
        loop = asyncio.get_running_loop()
        done = asyncio.Event()

        def after(error):
            if error:
                log.warning("[TTS] Error al reproducir: %s", error)
            loop.call_soon_threadsafe(done.set)

        session.voice.play(discord.FFmpegPCMAudio(str(path)), after=after)
        await done.wait()

    async def _worker(self, guild_id: int, session: Session):
        while True:
            text = await session.queue.get()
            try:
                await self._speak(guild_id, session, text)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("[TTS] No pude leer un mensaje en %s: %s", guild_id, e)

    # --- Abrir y cerrar

    @staticmethod
    def _place(guild: discord.Guild, voice_channel: discord.VoiceChannel):
        """(categoría, canal debajo del cual va el temporal): debajo de #bot-tts si existe."""
        anchor = discord.utils.find(lambda c: "bot-tts" in c.name.casefold(), guild.text_channels)
        if anchor is not None:
            return anchor.category, anchor
        category = discord.utils.find(lambda c: "vecindad" in c.name.casefold(), guild.categories)
        return category or voice_channel.category, None

    async def open(self, member: discord.Member, voice_channel: discord.VoiceChannel) -> discord.TextChannel:
        guild = member.guild
        voice = await voice_channel.connect(self_deaf=True, timeout=20)
        category, anchor = self._place(guild, voice_channel)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                            read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, add_reactions=True,
                                                  manage_channels=True, read_message_history=True),
        }
        try:
            channel = await guild.create_text_channel(
                TTS_CHANNEL_NAME, category=category, overwrites=overwrites,
                topic=f"Lo que escribas aquí se lee en voz alta en 🔊 {voice_channel.name}. "
                      "Se borra con /desconectar.",
                reason=f"TTS abierto por {member}")
            if anchor is not None and anchor.category == category:
                await channel.move(after=anchor)
        except discord.HTTPException:
            await voice.disconnect(force=True)
            raise
        session = Session(voice, channel)
        session.worker = asyncio.create_task(self._worker(guild.id, session))
        self.sessions[guild.id] = session
        _remember(guild.id, channel.id)
        log.info("[TTS] %s: conectado a %s por %s; canal #%s", guild.name, voice_channel.name, member,
                 channel.name)
        return channel

    async def close(self, guild_id: int, reason: str):
        """Saca al bot del canal de voz y borra el canal temporal (se puede llamar varias veces)."""
        session = self.sessions.pop(guild_id, None)
        if session is None:
            return
        for task in (session.worker, session.empty_timer):
            if task is not None and task is not asyncio.current_task():
                task.cancel()
        if session.voice.is_connected():
            await session.voice.disconnect(force=True)
        guild = self.bot.get_guild(guild_id)
        channel = guild.get_channel(session.channel_id) if guild else None
        if channel is not None:
            try:
                await channel.delete(reason=f"TTS cerrado: {reason}")
            except discord.HTTPException as e:
                log.warning("[TTS] No pude borrar el canal temporal: %s", e)
        _remember(guild_id, None)
        log.info("[TTS] %s: desconectado (%s)", guild.name if guild else guild_id, reason)

    async def _close_when_empty(self, guild_id: int):
        await asyncio.sleep(TTS_EMPTY_SECONDS)
        session = self.sessions.get(guild_id)
        if session is not None and not self._listeners(session):
            await self.close(guild_id, "el canal de voz se quedó vacío")

    @staticmethod
    def _listeners(session: Session) -> list[discord.Member]:
        channel = session.voice.channel
        return [m for m in channel.members if not m.bot] if channel else []

    # --- Eventos

    @commands.Cog.listener()
    async def on_ready(self):
        """Borra los canales temporales que quedaron de antes del reinicio."""
        if self._cleaned:
            return
        self._cleaned = True
        for guild_id, channel_id in _load().items():
            guild = self.bot.get_guild(int(guild_id))
            channel = guild.get_channel(channel_id) if guild else None
            if channel is not None:
                try:
                    await channel.delete(reason="TTS: el bot se reinició")
                    log.info("[TTS] %s: borrado el canal temporal que quedó abierto", guild.name)
                except discord.HTTPException as e:
                    log.warning("[TTS] No pude borrar el canal temporal viejo: %s", e)
        _save({})

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState):
        session = self.sessions.get(member.guild.id)
        if session is None:
            return
        if member.id == self.bot.user.id:
            if after.channel is None:  # lo sacaron del canal de voz
                await self.close(member.guild.id, "sacaron al bot del canal de voz")
            return
        if self._listeners(session):
            if session.empty_timer is not None:
                session.empty_timer.cancel()
                session.empty_timer = None
        elif session.empty_timer is None or session.empty_timer.done():
            session.empty_timer = asyncio.create_task(self._close_when_empty(member.guild.id))

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        session = self.sessions.get(channel.guild.id)
        if session is not None and session.channel_id == channel.id:
            await self.close(channel.guild.id, "borraron el canal temporal")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        session = self.sessions.get(message.guild.id)
        if session is None or message.channel.id != session.channel_id:
            return
        if message.content.startswith(COMMAND_PREFIX):
            return
        if not isinstance(message.author, discord.Member) or not is_registered(message.author):
            await message.add_reaction("🚫")
            return
        text = speech_text(message)
        if not text:
            return
        try:
            session.queue.put_nowait(text)
        except asyncio.QueueFull:
            await message.add_reaction("⏳")

    # --- Comandos

    @app_commands.command(name="hurtadotts", description="Cómo usar el TTS del bot (lee en voz alta)")
    @app_commands.guild_only()
    async def hurtadotts(self, interaction: discord.Interaction):
        config = settings.get(interaction.guild_id)
        embed = discord.Embed(title="🔊 TTS de SeniorHurtadoBot", color=discord.Color.gold(), description=(
            "`/jointts`: el bot entra a tu canal de voz y crea el canal **#botttshurtado**. "
            f"Lo que escribas ahí se lee en voz alta (*Edgar dice: …*), hasta {TTS_MAX_CHARS} "
            "caracteres por mensaje y sin leer enlaces.\n"
            "`/desconectar`: el bot sale del canal de voz y borra el canal temporal.\n"
            "`/configtts` *(administradores)*: voz, velocidad y volumen de este servidor.\n\n"
            f"Solo para jugadores registrados. Si el canal de voz se queda vacío "
            f"{TTS_EMPTY_SECONDS // 60} minutos, el bot sale solo.\n"
            f"Voz actual: **{config['tts_voz']}** · velocidad {signed(config['tts_velocidad'])} · "
            f"volumen {signed(config['tts_volumen'])}"))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="jointts", description="El bot entra a tu canal de voz y lee lo que escriban")
    @app_commands.guild_only()
    async def jointts(self, interaction: discord.Interaction):
        member = interaction.user
        if not is_registered(member):
            await interaction.response.send_message(NOT_REGISTERED, ephemeral=True)
            return
        voice_channel = member.voice.channel if member.voice else None
        if voice_channel is None:
            await interaction.response.send_message("Entra primero a un canal de voz.", ephemeral=True)
            return
        session = self.sessions.get(interaction.guild_id)
        if session is not None:
            where = session.voice.channel.mention if session.voice.channel else "otro canal"
            await interaction.response.send_message(
                f"Ya estoy leyendo en {where}: escribe en <#{session.channel_id}>. "
                "Para moverme, usa primero `/desconectar`.", ephemeral=True)
            return
        perms = voice_channel.permissions_for(interaction.guild.me)
        if not (perms.connect and perms.speak):
            await interaction.response.send_message(
                f"No tengo permiso para entrar o hablar en {voice_channel.mention}.", ephemeral=True)
            return
        if not interaction.guild.me.guild_permissions.manage_channels:
            await interaction.response.send_message(
                "Necesito el permiso **Gestionar canales** para crear el canal del TTS.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        try:
            channel = await self.open(member, voice_channel)
        except (discord.HTTPException, discord.ClientException, asyncio.TimeoutError) as e:
            log.error("[TTS] %s: no pude conectarme a %s: %s", interaction.guild.name, voice_channel.name, e)
            await interaction.followup.send(f"No pude entrar a {voice_channel.mention}. Intenta de nuevo.")
            return
        config = settings.get(interaction.guild_id)
        await channel.send(
            f"🔊 Estoy en **{voice_channel.name}**. Escribe aquí y lo leo en voz alta "
            f"(voz **{config['tts_voz']}**). Cuando terminen, usen `/desconectar`.")
        await interaction.followup.send(
            f"🔊 Entré a {voice_channel.mention}. Escribe en {channel.mention} y lo leo en voz alta.")

    @app_commands.command(name="desconectar", description="El bot sale del canal de voz y borra el canal del TTS")
    @app_commands.guild_only()
    async def desconectar(self, interaction: discord.Interaction):
        if not is_registered(interaction.user):
            await interaction.response.send_message(NOT_REGISTERED, ephemeral=True)
            return
        if interaction.guild_id not in self.sessions:
            await interaction.response.send_message("No estoy en ningún canal de voz.", ephemeral=True)
            return
        await interaction.response.send_message("👋 Salgo del canal de voz y borro el canal del TTS.")
        await self.close(interaction.guild_id, f"/desconectar de {interaction.user}")

    @app_commands.command(name="configtts", description="Voz, velocidad y volumen del TTS en este servidor")
    @app_commands.describe(voz="Voz de Edge en español", velocidad="De -50 a +100 (%)",
                           volumen="De -50 a +50 (%)")
    @app_commands.autocomplete(voz=voice_autocomplete)
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def configtts(self, interaction: discord.Interaction, voz: str | None = None,
                        velocidad: app_commands.Range[int, -50, 100] | None = None,
                        volumen: app_commands.Range[int, -50, 50] | None = None):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "Solo quien puede gestionar el servidor puede configurar el TTS.", ephemeral=True)
            return
        values = {}
        if voz is not None:
            voices = await spanish_voices()
            match = next((v["ShortName"] for v in voices if v["ShortName"].casefold() == voz.casefold()), None)
            if match is None:
                await interaction.response.send_message(
                    f"**{voz}** no es una voz de Edge en español. Elige una de la lista.", ephemeral=True)
                return
            values["tts_voz"] = match
        if velocidad is not None:
            values["tts_velocidad"] = velocidad
        if volumen is not None:
            values["tts_volumen"] = volumen
        if values:
            settings.update(interaction.guild_id, **values)
            log.info("[TTS] %s: configuración %s (por %s)", interaction.guild.name, values, interaction.user)
        config = settings.get(interaction.guild_id)
        await interaction.response.send_message(
            ("✅ Guardado. " if values else "") +
            f"Voz: **{config['tts_voz']}** · velocidad **{signed(config['tts_velocidad'])}** · "
            f"volumen **{signed(config['tts_volumen'])}**", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TTS(bot))
