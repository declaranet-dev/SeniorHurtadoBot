"""Eventos del gremio: el rol Admin crea eventos de Discord desde un formulario."""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot import settings
from bot.config import EVENT_DELETE_AFTER_HOURS, EVENT_DURATION_HOURS, EVENT_ROLES, EVENTOS_FILE

log = logging.getLogger("seniorhurtadobot.eventos")


def can_create_events(member: discord.Member) -> bool:
    """Rol configurado para eventos, o permiso de Administrador (incluye al dueño)."""
    if member.guild_permissions.administrator:
        return True
    role = settings.role(member.guild, "rol_admin_eventos")
    return role is not None and role in member.roles


def access_problem(member: discord.Member, channel_id: int | None) -> str | None:
    """Devuelve el motivo por el que no puede crear eventos aquí, o None."""
    events_channel = settings.get(member.guild.id)["canal_eventos"]
    if events_channel and channel_id != events_channel:
        return f"Los eventos se crean en <#{events_channel}>."
    if not can_create_events(member):
        role = settings.role(member.guild, "rol_admin_eventos")
        who = f"el rol **{role.name}**" if role else "los administradores"
        return f"Solo {who} puede crear eventos."
    return None


def check_access(interaction: discord.Interaction) -> str | None:
    return access_problem(interaction.user, interaction.channel_id)


def parse_hour_utc(text: str, now: datetime | None = None) -> datetime | None:
    """'00', '8', '20:30' → próxima vez que sea esa hora UTC (hoy o mañana)."""
    match = re.fullmatch(r"\s*(\d{1,2})(?:[:.h](\d{2}))?\s*(?:utc)?\s*", text, re.IGNORECASE)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    if hour > 23 or minute > 59:
        return None
    now = now or datetime.now(timezone.utc)
    start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if start <= now + timedelta(minutes=1):
        start += timedelta(days=1)
    return start


def build_description(participants: int, roles: list[str], info: str,
                      author: discord.Member) -> str:
    lines = [
        f"👥 Participantes: {participants}",
        f"🛡️ Roles necesarios: {', '.join(roles)}",
    ]
    if info:
        lines.append(f"ℹ️ {info}")
    lines.append(f"Organiza: {author.display_name}")
    return "\n".join(lines)[:1000]  # límite de Discord


_lock = asyncio.Lock()  # evita que dos clics a la vez pisen el archivo


def load_events() -> dict:
    if EVENTOS_FILE.exists():
        return json.loads(EVENTOS_FILE.read_text(encoding="utf-8"))
    return {}


def save_events(events: dict):
    EVENTOS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = EVENTOS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(EVENTOS_FILE)


def role_emoji(role: str) -> str:
    return EVENT_ROLES.get(role, "🔹")


def build_embed(data: dict) -> discord.Embed:
    """Anuncio del evento con la tabla de anotados por rol."""
    start = datetime.fromisoformat(data["inicio"])
    ts = int(start.timestamp())
    signups = data["anotados"]
    embed = discord.Embed(title=f"⚔️ {data['nombre']}", url=data["url"], color=discord.Color.gold())
    embed.add_field(name="🕛 Hora", value=f"{start:%H:%M} UTC\n<t:{ts}:F> (tu hora)\n<t:{ts}:R>", inline=False)
    embed.add_field(name="👥 Anotados", value=f"**{len(signups)} / {data['participantes']}**", inline=False)
    for role in data["roles"]:
        names = [f"<@{uid}>" for uid, r in signups.items() if r == role]
        embed.add_field(
            name=f"{role_emoji(role)} {role} ({len(names)})",
            value="\n".join(names) if names else "—",
        )
    if data["info"]:
        embed.add_field(name="ℹ️ Información extra", value=data["info"], inline=False)
    embed.set_footer(text=f"Organiza: {data['organiza']}")
    return embed


class SignupButton(discord.ui.DynamicItem[discord.ui.Button],
                   template=r"hurtado:ev:(?P<idx>\d+|salir)"):
    """Botón para anotarse con un rol (o salir). Sigue funcionando tras reiniciar.

    idx es la posición del rol dentro de los roles de ese evento.
    """

    def __init__(self, idx: str, label: str = "", emoji: str | None = None):
        leave = idx == "salir"
        super().__init__(discord.ui.Button(
            label=label or ("Salir" if leave else "Rol"),
            emoji=emoji or ("❌" if leave else None),
            style=discord.ButtonStyle.secondary if leave else discord.ButtonStyle.primary,
            custom_id=f"hurtado:ev:{idx}",
        ))
        self.idx = idx

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["idx"])

    async def callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        async with _lock:
            events = load_events()
            data = events.get(str(interaction.message.id))
            if data is None:
                await interaction.response.send_message("Este evento ya no está activo.", ephemeral=True)
                return
            signups = data["anotados"]
            if self.idx == "salir":
                if signups.pop(user_id, None) is None:
                    await interaction.response.send_message("No estabas anotado.", ephemeral=True)
                    return
                log.info("[EVENTOS] %s salió de %r", interaction.user, data["nombre"])
            else:
                role = data["roles"][int(self.idx)]
                if signups.get(user_id) == role:
                    await interaction.response.send_message(f"Ya estás anotado como **{role}**.", ephemeral=True)
                    return
                if user_id not in signups and len(signups) >= data["participantes"]:
                    await interaction.response.send_message("😬 El evento ya está lleno.", ephemeral=True)
                    return
                signups[user_id] = role  # si ya estaba con otro rol, lo cambia
                log.info("[EVENTOS] %s se anotó como %s en %r", interaction.user, role, data["nombre"])
            save_events(events)
        await interaction.response.edit_message(embed=build_embed(data))


def signup_view(roles: list[str]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for i, role in enumerate(roles):
        view.add_item(SignupButton(str(i), role, role_emoji(role)))
    view.add_item(SignupButton("salir"))
    return view


class EventoModal(discord.ui.Modal, title="Crear evento"):
    nombre = discord.ui.Label(
        text="Nombre del evento",
        component=discord.ui.TextInput(max_length=100, placeholder="Avaloniana"),
    )
    hora = discord.ui.Label(
        text="Hora del evento (UTC)",
        description="Ejemplos: 00, 8, 20:30",
        component=discord.ui.TextInput(max_length=5, placeholder="00"),
    )
    participantes = discord.ui.Label(
        text="Número de participantes",
        component=discord.ui.TextInput(max_length=3, placeholder="20"),
    )
    roles = discord.ui.Label(
        text="Roles necesarios",
        component=discord.ui.Select(
            placeholder="Elige uno o varios",
            min_values=1,
            max_values=len(EVENT_ROLES),
            options=[discord.SelectOption(label=r, emoji=e) for r, e in EVENT_ROLES.items()],
        ),
    )
    info = discord.ui.Label(
        text="Información extra",
        component=discord.ui.TextInput(
            style=discord.TextStyle.paragraph, required=False, max_length=600,
            placeholder="Salimos desde Avalanche Incline a las 00...",
        ),
    )

    async def on_submit(self, interaction: discord.Interaction):
        name = self.nombre.component.value.strip()
        hour_text = self.hora.component.value
        start = parse_hour_utc(hour_text)
        if start is None:
            await interaction.response.send_message(
                f"❌ La hora **{hour_text}** no es válida. Usa formato 00, 8 o 20:30 (UTC).",
                ephemeral=True,
            )
            return
        participants_text = self.participantes.component.value.strip()
        if not participants_text.isdigit() or int(participants_text) < 1:
            await interaction.response.send_message(
                f"❌ **{participants_text}** no es un número de participantes válido.",
                ephemeral=True,
            )
            return
        participants = int(participants_text)
        roles = list(self.roles.component.values)
        info = self.info.component.value.strip()

        await interaction.response.defer(thinking=True)
        guild = interaction.guild
        try:
            event = await guild.create_scheduled_event(
                name=name,
                start_time=start,
                end_time=start + timedelta(hours=EVENT_DURATION_HOURS),
                entity_type=discord.EntityType.external,
                privacy_level=discord.PrivacyLevel.guild_only,
                location="Albion Online",
                description=build_description(participants, roles, info, interaction.user),
                reason=f"Evento creado por {interaction.user}",
            )
        except discord.Forbidden:
            log.error("[EVENTOS] ERROR: El bot no tiene permiso para crear eventos")
            await interaction.followup.send("⚠️ El bot no tiene permiso para crear eventos.")
            return
        except discord.HTTPException as e:
            log.error("[EVENTOS] ERROR: No se pudo crear el evento: %s", e)
            await interaction.followup.send("⚠️ Discord dio un error al crear el evento.")
            return
        log.info("[EVENTOS] %s creó el evento %r para %s", interaction.user, name, start.isoformat())

        data = {
            "nombre": name,
            "inicio": start.isoformat(),
            "participantes": participants,
            "roles": roles,
            "info": info,
            "organiza": interaction.user.display_name,
            "url": event.url,
            "canal": interaction.channel_id,  # para borrar el anuncio después
            "anotados": {},  # id de usuario -> rol
        }
        message = await interaction.followup.send(
            content=f"📅 ¡Nuevo evento! Elige tu rol con los botones. Interesados en Discord: {event.url}",
            embed=build_embed(data),
            view=signup_view(roles),
            wait=True,
        )
        async with _lock:
            events = load_events()
            events[str(message.id)] = data
            save_events(events)


class EventoView(discord.ui.View):
    """Botón que abre el formulario. Persistente: sigue funcionando tras reiniciar."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Crear evento", emoji="📅",
                       style=discord.ButtonStyle.success,
                       custom_id="seniorhurtadobot:evento")
    async def open_form(self, interaction: discord.Interaction, button: discord.ui.Button):
        if (problem := check_access(interaction)):
            await interaction.response.send_message(problem, ephemeral=True)
            return
        await interaction.response.send_modal(EventoModal())


class Eventos(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(EventoView())
        bot.add_dynamic_items(SignupButton)

    async def cog_load(self):
        self.cleanup.start()

    async def cog_unload(self):
        self.cleanup.cancel()

    @tasks.loop(minutes=10)
    async def cleanup(self):
        """Borra los anuncios cuyo evento empezó hace más de EVENT_DELETE_AFTER_HOURS."""
        limit = datetime.now(timezone.utc) - timedelta(hours=EVENT_DELETE_AFTER_HOURS)
        async with _lock:
            events = load_events()
            expired = [mid for mid, data in events.items()
                       if datetime.fromisoformat(data["inicio"]) <= limit]
            for message_id in expired:
                data = events[message_id]
                channel = self.bot.get_channel(data.get("canal") or 0)
                if channel is not None:
                    try:
                        await channel.get_partial_message(int(message_id)).delete()
                        log.info("[EVENTOS] Anuncio de %r borrado (%sh después del evento)",
                                 data["nombre"], EVENT_DELETE_AFTER_HOURS)
                    except discord.NotFound:
                        pass  # ya lo había borrado alguien
                    except discord.HTTPException as e:
                        log.error("[EVENTOS] No se pudo borrar el anuncio de %r: %s", data["nombre"], e)
                        continue  # se reintenta en la próxima vuelta
                del events[message_id]
            if expired:
                save_events(events)

    @cleanup.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    @commands.command(name="evento", aliases=["eventos"])
    @commands.guild_only()
    async def evento(self, ctx: commands.Context):
        if (problem := access_problem(ctx.author, ctx.channel.id)):
            await ctx.send(problem)
            return
        await ctx.send(
            "📅 **Crear evento**\nPulsa el botón y llena el formulario.",
            view=EventoView(),
        )

    @app_commands.command(name="evento", description="Crea un evento del gremio")
    @app_commands.guild_only()
    async def evento_slash(self, interaction: discord.Interaction):
        if (problem := check_access(interaction)):
            await interaction.response.send_message(problem, ephemeral=True)
            return
        await interaction.response.send_modal(EventoModal())

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            if not guild.me.guild_permissions.manage_events:
                log.error("[EVENTOS] ERROR: El bot no tiene permiso 'Gestionar eventos' en %s", guild.name)
            channel = settings.channel(guild, "canal_eventos")
            if channel:
                log.info("[EVENTOS] %s: canal #%s", guild.name, channel.name)


async def setup(bot: commands.Bot):
    await bot.add_cog(Eventos(bot))
