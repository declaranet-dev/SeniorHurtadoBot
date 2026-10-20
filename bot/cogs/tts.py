"""Texto a voz: el bot entra al canal de voz y lee lo que se escribe en un canal temporal.

/jointts conecta el bot al canal de voz de quien lo usa y crea #BotTTSHurtado;
cada mensaje de ese canal se lee con una voz de Edge ("Edgar dice: ...").
/desconectar, o 2 minutos con el canal de voz vacío, lo saca y borra el canal.
"""

import asyncio
import json
import logging
import re
import tempfile
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from bot import settings
from bot.config import (TTS_ANCHOR_CHANNEL, TTS_CHANNEL_NAME, TTS_DEFAULT_VOICE, TTS_FILE,
                        TTS_IDLE_SECONDS, TTS_MAX_CHARS)

log = logging.getLogger("seniorhurtadobot.tts")

try:
    import edge_tts
except ImportError:  # sin la librería el resto del bot sigue funcionando
    edge_tts = None

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
EMOJI_RE = re.compile(r"<a?:(\w+):\d+>")

HELP_TEXT = (
    "🔊 **Texto a voz de SeniorHurtadoBot**\n"
    f"1. Entra a un canal de voz y usa `/jointts` en #{TTS_ANCHOR_CHANNEL}: el bot entra contigo y crea el canal "
    f"**#{TTS_CHANNEL_NAME.lower()}**.\n"
    "2. Todo lo que escribas en ese canal lo lee en voz alta (\"Tu nombre dice: ...\"). "
    f"Máximo {TTS_MAX_CHARS} caracteres por mensaje; los enlaces no se leen.\n"
    "3. `/desconectar` saca al bot y borra el canal. También sale solo si el canal de voz "
    f"queda vacío {TTS_IDLE_SECONDS // 60} minutos.\n"
    "`/configtts` (administradores): voz en español, velocidad y volumen de este servidor.\n"
    "Lo pueden usar los jugadores registrados."
)

TTS_ROLES = ("rol_miembro", "rol_externo")  # quién ve el canal temporal

# Voces de Edge en español; se reemplaza por la lista real al arrancar.
_voices: list[str] = [TTS_DEFAULT_VOICE]


# --- Canales temporales guardados (para borrarlos si el bot se reinicia)

def _load() -> dict:
    try:
        return json.loads(TTS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict):
    TTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(TTS_FILE)


def _remember(guild_id: int, channel_id: int | None):
    data = _load()
    if channel_id is None:
        data.pop(str(guild_id), None)
    else:
        data[str(guild_id)] = channel_id
    _save(data)


# --- Reglas

def is_registered(member: discord.Member) -> bool:
    """Tiene alguno de los roles que da el registro (o es administrador)."""
    if member.guild_permissions.manage_guild:
        return True
    roles = [settings.role(member.guild, k) for k in ("rol_miembro", "rol_aliado", "rol_externo")]
    roles = [r for r in roles if r is not None]
    if not roles:
        return True  # registro sin configurar en este servidor: no se limita
    return any(r in member.roles for r in roles)


def clean_text(message: discord.Message) -> str:
    """Texto que se lee: sin enlaces, con nombres en vez de menciones y recortado."""
    text = URL_RE.sub("", message.clean_content)
    text = EMOJI_RE.sub(r"\1", text)
    text = " ".join(text.split())
    return text[:TTS_MAX_CHARS]


def anchor_channel(guild: discord.Guild) -> discord.TextChannel | None:
    """#bot-tts: desde ahí se usa /jointts y debajo se crea el canal temporal."""
    return discord.utils.find(lambda c: c.name.lower() == TTS_ANCHOR_CHANNEL, guild.text_channels)


def percent(n: int) -> str:
    return f"{'+' if n >= 0 else ''}{n}%"


async def load_voices():
    global _voices
    if edge_tts is None:
        return
    try:
        voices = await edge_tts.list_voices()
    except Exception as e:  # sin internet o Edge caído: se queda la voz por defecto
        log.warning("[TTS] No pude cargar la lista de voces: %s", e)
        return
    found = sorted(v["ShortName"] for v in voices if v.get("Locale", "").startswith("es-"))
    if found:
        _voices = found
        log.info("[TTS] %d voces en español disponibles", len(found))


async def voice_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    current = current.lower()
    names = [v for v in _voices if current in v.lower()]
    return [app_commands.Choice(name=v, value=v) for v in names[:25]]


# --- Sesión de un servidor: conexión de voz, canal temporal y cola de lectura

class Session:
    def __init__(self, voice: discord.VoiceClient, channel: discord.TextChannel):
        self.voice = voice
        self.channel = channel
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.worker = asyncio.create_task(self._read_loop())
        self.idle_task: asyncio.Task | None = None

    async def _read_loop(self):
        while True:
            text = await self.queue.get()
            try:
                await self._speak(text)
            except Exception:
                log.exception("[TTS] %s: no pude leer un mensaje", self.channel.guild.name)

    async def _speak(self, text: str):
        if not self.voice.is_connected():
            return
        config = settings.get(self.channel.guild.id)
        voice = config["tts_voz"] if config["tts_voz"] in _voices else TTS_DEFAULT_VOICE
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tts.mp3"
            await edge_tts.Communicate(text, voice, rate=percent(config["tts_velocidad"]),
                                       volume=percent(config["tts_volumen"])).save(str(path))
            done = asyncio.Event()
            loop = asyncio.get_running_loop()
            self.voice.play(discord.FFmpegPCMAudio(str(path)),
                            after=lambda e: loop.call_soon_threadsafe(done.set))
            await done.wait()

    def stop(self):
        self.worker.cancel()
        if self.idle_task:
            self.idle_task.cancel()


class TTS(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: dict[int, Session] = {}
        self._cleaned = False

    async def cog_unload(self):
        for guild_id in list(self.sessions):
            await self.close(guild_id, "se descargó el módulo")

    @commands.Cog.listener()
    async def on_ready(self):
        if self._cleaned:
            return
        self._cleaned = True
        await load_voices()
        # Canales que quedaron de antes de reiniciar.
        for guild_id, channel_id in _load().items():
            if int(guild_id) in self.sessions:
                continue
            channel = self.bot.get_channel(int(channel_id))
            if channel is not None:
                try:
                    await channel.delete(reason="TTS: el bot se reinició")
                    log.info("[TTS] Borré #%s que quedó de antes de reiniciar", channel.name)
                except discord.HTTPException as e:
                    log.warning("[TTS] No pude borrar #%s: %s", channel.name, e)
            _remember(int(guild_id), None)

    async def create_channel(self, voice_channel: discord.VoiceChannel) -> discord.TextChannel:
        """Crea #BotTTSHurtado justo debajo de #bot-tts (o en la categoría del canal de voz)."""
        guild = voice_channel.guild
        anchor = anchor_channel(guild)
        category = anchor.category if anchor else voice_channel.category
        # Solo permisos que el bot ya tiene: Discord rechaza dar en un canal lo que el bot no puede
        # (por eso no se copian los de la categoría).
        # Lo ven los roles de miembros y de otros gremios (Chavo del 8 y Chusma); si no están
        # configurados, todos.
        allow = dict(view_channel=True, send_messages=True, read_message_history=True)
        readers = [r for r in (settings.role(guild, k) for k in TTS_ROLES) if r is not None]
        overwrites = {guild.me: discord.PermissionOverwrite(**allow)}
        if readers:
            overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
            for role in readers:
                overwrites[role] = discord.PermissionOverwrite(**allow)
        else:
            overwrites[guild.default_role] = discord.PermissionOverwrite(**allow)
        topic = (f"Lo que escribas aquí se lee en voz alta en 🔊 {voice_channel.name}. "
                 "Se borra al usar /desconectar.")
        try:
            channel = await guild.create_text_channel(
                TTS_CHANNEL_NAME, category=category, overwrites=overwrites, topic=topic,
                reason="TTS: /jointts")
        except discord.Forbidden as e:
            # Sin "Gestionar permisos" no puede poner permisos propios: se queda con los de la categoría.
            # Solo si con esos el bot ve el canal; si no, no podría leerlo ni borrarlo después.
            if category is not None and not category.permissions_for(guild.me).view_channel:
                raise
            log.warning("[TTS] %s: no pude poner permisos al canal (%s); uso los de la categoría",
                        guild.name, e.text)
            channel = await guild.create_text_channel(
                TTS_CHANNEL_NAME, category=category, topic=topic, reason="TTS: /jointts")
        if anchor is not None:
            try:
                await channel.move(after=anchor, reason="TTS: debajo de #bot-tts")
            except discord.HTTPException as e:
                log.warning("[TTS] No pude mover #%s debajo de #%s: %s", channel.name, anchor.name, e)
        return channel

    async def close(self, guild_id: int, reason: str):
        session = self.sessions.pop(guild_id, None)
        if session is None:
            return
        session.stop()
        if session.voice.is_connected():
            await session.voice.disconnect(force=True)
        try:
            await session.channel.delete(reason=f"TTS: {reason}")
        except discord.NotFound:
            pass
        except discord.HTTPException as e:
            log.warning("[TTS] No pude borrar #%s: %s", session.channel.name, e)
            return  # se queda guardado para borrarlo al reiniciar
        _remember(guild_id, None)
        log.info("[TTS] %s: salí del canal de voz (%s)", session.channel.guild.name, reason)

    # --- Comandos

    @app_commands.command(name="hurtadotts", description="Cómo usar el texto a voz del bot")
    @app_commands.guild_only()
    async def hurtadotts(self, interaction: discord.Interaction):
        await interaction.response.send_message(HELP_TEXT, ephemeral=True)

    @app_commands.command(name="jointts", description="El bot entra a tu canal de voz y lee lo que escribas")
    @app_commands.guild_only()
    async def jointts(self, interaction: discord.Interaction):
        member = interaction.user
        if not is_registered(member):
            await interaction.response.send_message(
                "Solo los jugadores registrados pueden usar el TTS. Regístrate con /registro.", ephemeral=True)
            return
        if edge_tts is None:
            await interaction.response.send_message("El TTS no está instalado en el bot.", ephemeral=True)
            return
        anchor = anchor_channel(interaction.guild)
        if anchor is not None and interaction.channel_id != anchor.id:
            await interaction.response.send_message(f"Usa `/jointts` en {anchor.mention}.", ephemeral=True)
            return
        if member.voice is None or member.voice.channel is None:
            await interaction.response.send_message("Primero entra a un canal de voz.", ephemeral=True)
            return
        target = member.voice.channel
        session = self.sessions.get(interaction.guild_id)
        if session is not None:
            where = "aquí" if session.voice.channel == target else f"en 🔊 {session.voice.channel.name}"
            await interaction.response.send_message(
                f"Ya estoy {where}. Escribe en {session.channel.mention}.", ephemeral=True)
            return
        perms = target.permissions_for(interaction.guild.me)
        if not (perms.connect and perms.speak):
            await interaction.response.send_message(
                f"No tengo permiso para entrar o hablar en 🔊 {target.name}.", ephemeral=True)
            return
        category = anchor.category if anchor else target.category
        if category is not None and not category.permissions_for(interaction.guild.me).view_channel:
            await interaction.response.send_message(
                f"No puedo crear el canal de texto: no veo la categoría **{category.name}**. Dale al rol "
                f"{interaction.guild.me.top_role.mention} *Ver el canal*, *Gestionar canal* y *Gestionar "
                "permisos* en esa categoría (Editar categoría > Permisos).", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        try:
            voice = await target.connect(self_deaf=True, timeout=20)
        except (discord.ClientException, asyncio.TimeoutError, discord.HTTPException) as e:
            log.error("[TTS] %s: no pude entrar a %s: %s", interaction.guild.name, target.name, e)
            await interaction.followup.send("❌ No pude entrar al canal de voz. Inténtalo de nuevo.")
            return
        try:
            channel = await self.create_channel(target)
        except discord.HTTPException as e:
            log.error("[TTS] %s: no pude crear el canal: %s", interaction.guild.name, e)
            await voice.disconnect(force=True)
            await interaction.followup.send(f"❌ No pude crear el canal de texto: {e.text or e}")
            return
        self.sessions[interaction.guild_id] = Session(voice, channel)
        _remember(interaction.guild_id, channel.id)
        log.info("[TTS] %s: entré a %s por %s", interaction.guild.name, target.name, member)
        await channel.send(f"🔊 Estoy en **{target.name}**. Todo lo que escriban aquí lo leo en voz alta. "
                           "Usa `/desconectar` para sacarme.")
        await interaction.followup.send(f"🔊 Entré a **{target.name}**. Escribe en {channel.mention}.")

    @app_commands.command(name="desconectar", description="Saca al bot del canal de voz y borra el canal de TTS")
    @app_commands.guild_only()
    async def desconectar(self, interaction: discord.Interaction):
        if not is_registered(interaction.user):
            await interaction.response.send_message(
                "Solo los jugadores registrados pueden usar el TTS.", ephemeral=True)
            return
        if interaction.guild_id not in self.sessions:
            await interaction.response.send_message("No estoy en ningún canal de voz.", ephemeral=True)
            return
        await interaction.response.send_message("👋 Salgo del canal de voz y borro el canal de TTS.")
        await self.close(interaction.guild_id, f"/desconectar de {interaction.user}")

    @app_commands.command(name="configtts", description="Voz, velocidad y volumen del TTS en este servidor")
    @app_commands.describe(voz="Voz de Edge en español", velocidad="De -50 (lento) a 100 (rápido); 0 es normal",
                           volumen="De -50 (bajo) a 50 (fuerte); 0 es normal")
    @app_commands.autocomplete(voz=voice_autocomplete)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def configtts(self, interaction: discord.Interaction, voz: str | None = None,
                        velocidad: app_commands.Range[int, -50, 100] | None = None,
                        volumen: app_commands.Range[int, -50, 50] | None = None):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Solo administradores.", ephemeral=True)
            return
        changes = {}
        if voz is not None:
            if voz not in _voices:
                await interaction.response.send_message(
                    f"No conozco la voz **{voz}**. Elige una de la lista.", ephemeral=True)
                return
            changes["tts_voz"] = voz
        if velocidad is not None:
            changes["tts_velocidad"] = velocidad
        if volumen is not None:
            changes["tts_volumen"] = volumen
        if changes:
            settings.update(interaction.guild_id, **changes)
            log.info("[TTS] %s: %s (por %s)", interaction.guild.name, changes, interaction.user)
        config = settings.get(interaction.guild_id)
        await interaction.response.send_message(
            f"🔊 **TTS de este servidor**\nVoz: **{config['tts_voz']}**\n"
            f"Velocidad: **{percent(config['tts_velocidad'])}** · Volumen: **{percent(config['tts_volumen'])}**",
            ephemeral=True)

    # --- Eventos

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        session = self.sessions.get(message.guild.id)
        if session is None or message.channel.id != session.channel.id:
            return
        if not isinstance(message.author, discord.Member) or not is_registered(message.author):
            return
        text = clean_text(message)
        if text:
            await session.queue.put(f"{message.author.display_name} dice: {text}")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState):
        session = self.sessions.get(member.guild.id)
        if session is None:
            return
        if member.id == self.bot.user.id and after.channel is None:
            await self.close(member.guild.id, "me sacaron del canal de voz")
            return
        channel = session.voice.channel
        if channel is None:
            return
        people = [m for m in channel.members if not m.bot]
        if people:
            if session.idle_task:
                session.idle_task.cancel()
                session.idle_task = None
        elif session.idle_task is None:
            session.idle_task = asyncio.create_task(self._leave_if_idle(member.guild.id))

    async def _leave_if_idle(self, guild_id: int):
        await asyncio.sleep(TTS_IDLE_SECONDS)
        session = self.sessions.get(guild_id)
        if session is None:
            return
        session.idle_task = None
        channel = session.voice.channel
        if channel is not None and not [m for m in channel.members if not m.bot]:
            await self.close(guild_id, "canal de voz vacío")


async def setup(bot: commands.Bot):
    await bot.add_cog(TTS(bot))
