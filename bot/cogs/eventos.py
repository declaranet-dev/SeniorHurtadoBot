"""Eventos del gremio: asistente por pasos para crear el evento y anuncio con lugares.

Pasos del creador (mensaje que solo ve él):
  1. Tipo de contenido (ZvZ, Avaloniana, ...)
  2. Horario y lugar de salida (formulario)
  3. Cantidad de jugadores (según el tipo)
  4. Rol de cada lugar ("Asignado por Caller" por defecto)
Después se publica el anuncio y los miembros eligen su rol en una lista.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot import settings
from bot.config import (
    EVENT_CONTENT,
    EVENT_DELETE_AFTER_HOURS,
    EVENT_DURATION_HOURS,
    EVENT_ROLES,
    EVENTOS_FILE,
    SLOT_CALLER_ROLE,
)

log = logging.getLogger("seniorhurtadobot.eventos")

SLOTS_PER_PAGE = 4  # selects por página del paso 4 (la 5.ª fila son los botones)
SLOTS_PER_FIELD = 15  # líneas por bloque de la tabla del anuncio


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
    if role == SLOT_CALLER_ROLE:
        return "📣"
    return EVENT_ROLES.get(role, "🔹")


def slot_roles() -> list[str]:
    """Opciones de cada lugar: primero "Asignado por Caller", luego los roles."""
    return [SLOT_CALLER_ROLE, *EVENT_ROLES]


def role_counts(slots: list[str]) -> str:
    counts = {}
    for role in slots:
        counts[role] = counts.get(role, 0) + 1
    return ", ".join(f"{n} {role}" for role, n in counts.items())


# ---------------------------------------------------------------- anuncio

def build_slot_embed(data: dict) -> discord.Embed:
    """Anuncio del evento con la tabla de lugares."""
    start = datetime.fromisoformat(data["inicio"])
    ts = int(start.timestamp())
    slots, signups = data["lugares"], data["anotados"]
    taken = {idx: uid for uid, idx in signups.items()}
    emoji = EVENT_CONTENT.get(data["tipo"], ("📌",))[0]
    embed = discord.Embed(title=f"{emoji} {data['nombre']}", url=data["url"], color=discord.Color.gold())
    embed.add_field(name="🕛 Hora", value=f"{start:%H:%M} UTC\n<t:{ts}:F> (tu hora)\n<t:{ts}:R>")
    embed.add_field(name="📍 Salida", value=data["lugar"])
    embed.add_field(name="👥 Anotados", value=f"**{len(signups)} / {len(slots)}**")
    for first in range(0, len(slots), SLOTS_PER_FIELD):
        lines = []
        for idx in range(first, min(first + SLOTS_PER_FIELD, len(slots))):
            who = f"<@{taken[idx]}>" if idx in taken else "*libre*"
            lines.append(f"`{idx + 1:02}` {role_emoji(slots[idx])} {slots[idx]} — {who}")
        last = min(first + SLOTS_PER_FIELD, len(slots))
        embed.add_field(name=f"Lugares {first + 1}-{last}", value="\n".join(lines), inline=False)
    if data["info"]:
        embed.add_field(name="ℹ️ Información extra", value=data["info"][:1024], inline=False)
    embed.set_footer(text=f"{data['tipo']} · Organiza: {data['organiza']}")
    return embed


def free_by_role(data: dict) -> dict[str, int]:
    taken = set(data["anotados"].values())
    free = {}
    for idx, role in enumerate(data["lugares"]):
        if idx not in taken:
            free[role] = free.get(role, 0) + 1
    return free


class SlotSignupView(discord.ui.View):
    """Lista para elegir rol y botón para salir. Persistente: sigue funcionando
    tras reiniciar (las opciones salen de cada mensaje)."""

    def __init__(self, data: dict | None = None):
        super().__init__(timeout=None)
        free = free_by_role(data) if data else {}
        options = [discord.SelectOption(label=f"{role} ({n} {'libre' if n == 1 else 'libres'})"[:100], value=role,
                                        emoji=role_emoji(role))
                   for role, n in free.items()][:25]
        self.choose.options = options or [discord.SelectOption(label="Evento lleno", value="-")]
        self.choose.disabled = data is not None and not free
        self.choose.placeholder = "Elige el rol que vas a jugar" if free or data is None else "Evento lleno"

    @discord.ui.select(custom_id="hurtado:slot:elegir", min_values=1, max_values=1,
                       options=[discord.SelectOption(label="-")])
    async def choose(self, interaction: discord.Interaction, select: discord.ui.Select):
        role = select.values[0]
        user_id = str(interaction.user.id)
        async with _lock:
            events = load_events()
            data = events.get(str(interaction.message.id))
            if data is None or "lugares" not in data:
                await interaction.response.send_message("Este evento ya no está activo.", ephemeral=True)
                return
            current = data["anotados"].get(user_id)
            if current is not None and data["lugares"][current] == role:
                await interaction.response.send_message(f"Ya estás anotado como **{role}**.", ephemeral=True)
                return
            taken = set(data["anotados"].values()) - {current}
            free = [i for i, r in enumerate(data["lugares"]) if r == role and i not in taken]
            if not free:
                await interaction.response.send_message(f"Ya no quedan lugares de **{role}**.", ephemeral=True)
                return
            data["anotados"][user_id] = free[0]  # si ya tenía otro lugar, se cambia
            save_events(events)
            log.info("[EVENTOS] %s se anotó como %s (lugar %d) en %r", interaction.user, role,
                     free[0] + 1, data["nombre"])
        await interaction.response.edit_message(embed=build_slot_embed(data), view=SlotSignupView(data))

    @discord.ui.button(custom_id="hurtado:slot:salir", label="Salir", emoji="❌",
                       style=discord.ButtonStyle.secondary)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with _lock:
            events = load_events()
            data = events.get(str(interaction.message.id))
            if data is None or "lugares" not in data:
                await interaction.response.send_message("Este evento ya no está activo.", ephemeral=True)
                return
            if data["anotados"].pop(str(interaction.user.id), None) is None:
                await interaction.response.send_message("No estabas anotado.", ephemeral=True)
                return
            save_events(events)
            log.info("[EVENTOS] %s salió de %r", interaction.user, data["nombre"])
        await interaction.response.edit_message(embed=build_slot_embed(data), view=SlotSignupView(data))


# ---------------------------------------------------------------- asistente

class ScheduleModal(discord.ui.Modal, title="Paso 2 · Horario y salida"):
    def __init__(self, wizard: "EventWizard"):
        super().__init__()
        self.wizard = wizard
        self.hora = discord.ui.TextInput(label="Horario (hora UTC, ej. 00 o 20:30)", max_length=8,
                                         default=wizard.hour_text or None)
        self.lugar = discord.ui.TextInput(label="Lugar de salida", max_length=100,
                                          placeholder="Avalanche Incline", default=wizard.lugar or None)
        self.nombre = discord.ui.TextInput(label="Nombre del evento (opcional)", required=False,
                                           max_length=80, placeholder=wizard.tipo,
                                           default=wizard.nombre or None)
        self.info = discord.ui.TextInput(label="Información extra (opcional)", required=False,
                                         style=discord.TextStyle.paragraph, max_length=600,
                                         default=wizard.info or None)
        for item in (self.hora, self.lugar, self.nombre, self.info):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        start = parse_hour_utc(self.hora.value)
        if start is None:
            await interaction.response.send_message(
                f"❌ El horario **{self.hora.value}** no es válido. Usa 00, 8 o 20:30 (hora UTC).",
                ephemeral=True)
            return
        w = self.wizard
        w.start, w.hour_text = start, self.hora.value.strip()
        w.lugar = self.lugar.value.strip()
        w.nombre = self.nombre.value.strip()
        w.info = self.info.value.strip()
        w.step = 3
        await w.refresh(interaction)


class EventWizard(discord.ui.View):
    """Mensaje privado del creador con los pasos del evento."""

    def __init__(self, author: discord.Member):
        super().__init__(timeout=1800)
        self.author = author
        self.step = 1
        self.tipo: str | None = None
        self.start: datetime | None = None
        self.hour_text = ""
        self.lugar = ""
        self.nombre = ""
        self.info = ""
        self.slots: list[str] = []
        self.page = 0
        self.render()

    # --- pintar cada paso

    def embed(self) -> discord.Embed:
        titles = {1: "Paso 1 · Tipo de contenido", 3: "Paso 3 · Cantidad de jugadores",
                  4: "Paso 4 · Roles de cada lugar"}
        embed = discord.Embed(title=f"📅 Crear evento · {titles.get(self.step, '')}",
                              color=discord.Color.green())
        lines = []
        if self.tipo:
            lines.append(f"**Tipo:** {EVENT_CONTENT[self.tipo][0]} {self.tipo}")
        if self.start:
            ts = int(self.start.timestamp())
            lines.append(f"**Horario:** {self.start:%H:%M} UTC (<t:{ts}:F>, <t:{ts}:R>)")
            lines.append(f"**Salida:** {self.lugar}")
            if self.nombre:
                lines.append(f"**Nombre:** {self.nombre}")
        if self.slots:
            lines.append(f"**Jugadores:** {len(self.slots)} ({role_counts(self.slots)})")
        if self.step == 4:
            pages = (len(self.slots) + SLOTS_PER_PAGE - 1) // SLOTS_PER_PAGE
            lines.append(f"\nElige el rol de cada lugar. Página {self.page + 1} de {pages}. "
                         f"Los que no cambies quedan como **{SLOT_CALLER_ROLE}**.")
        embed.description = "\n".join(lines) or "Elige el tipo de contenido."
        return embed

    def render(self):
        self.clear_items()
        if self.step == 1:
            select = discord.ui.Select(placeholder="Tipo de contenido", options=[
                discord.SelectOption(label=t, emoji=e, default=t == self.tipo)
                for t, (e, _) in EVENT_CONTENT.items()])
            select.callback = self._on_type
            self.add_item(select)
            self._button("Siguiente: horario y salida", "▶️", self._open_schedule, row=1,
                         disabled=self.tipo is None)
        elif self.step == 3:
            sizes = EVENT_CONTENT[self.tipo][1]
            select = discord.ui.Select(placeholder="¿Cuántos jugadores?", options=[
                discord.SelectOption(label=f"{n} jugadores", value=str(n), default=n == len(self.slots))
                for n in sizes][:25])
            select.callback = self._on_size
            self.add_item(select)
            self._button("Atrás", "◀️", self._back_to_schedule, row=1, style=discord.ButtonStyle.secondary)
            self._button("Siguiente: roles", "▶️", self._to_roles, row=1, disabled=not self.slots)
        elif self.step == 4:
            first = self.page * SLOTS_PER_PAGE
            for row, idx in enumerate(range(first, min(first + SLOTS_PER_PAGE, len(self.slots)))):
                select = discord.ui.Select(row=row, placeholder=f"Jugador {idx + 1}", options=[
                    discord.SelectOption(label=f"Jugador {idx + 1}: {role}", value=role,
                                         emoji=role_emoji(role), default=role == self.slots[idx])
                    for role in slot_roles()])
                select.callback = self._slot_callback(idx)
                self.add_item(select)
            pages = (len(self.slots) + SLOTS_PER_PAGE - 1) // SLOTS_PER_PAGE
            self._button("Anterior", "◀️", self._prev_page, row=4, style=discord.ButtonStyle.secondary,
                         disabled=self.page == 0)
            self._button("Siguiente", "▶️", self._next_page, row=4, style=discord.ButtonStyle.secondary,
                         disabled=self.page >= pages - 1)
            self._button("Cantidad", "🔢", self._back_to_size, row=4, style=discord.ButtonStyle.secondary)
            self._button("Publicar evento", "✅", self._publish, row=4)

    def _button(self, label, emoji, callback, row, disabled=False, style=discord.ButtonStyle.success):
        button = discord.ui.Button(label=label, emoji=emoji, style=style, row=row, disabled=disabled)
        button.callback = callback
        self.add_item(button)

    async def refresh(self, interaction: discord.Interaction):
        self.render()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author.id

    # --- acciones

    async def _on_type(self, interaction: discord.Interaction):
        self.tipo = interaction.data["values"][0]
        self.slots = []  # cada tipo tiene sus cantidades
        await self.refresh(interaction)

    async def _open_schedule(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ScheduleModal(self))

    async def _back_to_schedule(self, interaction: discord.Interaction):
        self.step = 1
        await self.refresh(interaction)

    async def _on_size(self, interaction: discord.Interaction):
        size = int(interaction.data["values"][0])
        # Se conservan los roles ya elegidos; los lugares nuevos quedan para el caller.
        self.slots = (self.slots + [SLOT_CALLER_ROLE] * size)[:size]
        await self.refresh(interaction)

    async def _to_roles(self, interaction: discord.Interaction):
        self.step, self.page = 4, 0
        await self.refresh(interaction)

    async def _back_to_size(self, interaction: discord.Interaction):
        self.step = 3
        await self.refresh(interaction)

    def _slot_callback(self, idx: int):
        async def callback(interaction: discord.Interaction):
            self.slots[idx] = interaction.data["values"][0]
            await self.refresh(interaction)
        return callback

    async def _prev_page(self, interaction: discord.Interaction):
        self.page -= 1
        await self.refresh(interaction)

    async def _next_page(self, interaction: discord.Interaction):
        self.page += 1
        await self.refresh(interaction)

    async def _publish(self, interaction: discord.Interaction):
        name = self.nombre or self.tipo
        await interaction.response.edit_message(content="⏳ Publicando el evento...", embed=self.embed(),
                                                view=None)
        description = "\n".join([
            f"{EVENT_CONTENT[self.tipo][0]} {self.tipo}",
            f"📍 Salida: {self.lugar}",
            f"👥 {len(self.slots)} jugadores: {role_counts(self.slots)}",
            *( [f"ℹ️ {self.info}"] if self.info else [] ),
            f"Organiza: {self.author.display_name}",
        ])[:1000]
        try:
            event = await interaction.guild.create_scheduled_event(
                name=name[:100],
                start_time=self.start,
                end_time=self.start + timedelta(hours=EVENT_DURATION_HOURS),
                entity_type=discord.EntityType.external,
                privacy_level=discord.PrivacyLevel.guild_only,
                location=self.lugar[:100],
                description=description,
                reason=f"Evento creado por {self.author}",
            )
        except discord.HTTPException as e:
            log.error("[EVENTOS] ERROR: No se pudo crear el evento: %s", e)
            await interaction.edit_original_response(
                content="⚠️ Discord no dejó crear el evento. Revisa que el bot tenga 'Gestionar eventos'.")
            return

        data = {
            "tipo": self.tipo,
            "nombre": name,
            "inicio": self.start.isoformat(),
            "lugar": self.lugar,
            "info": self.info,
            "organiza": self.author.display_name,
            "url": event.url,
            "canal": interaction.channel_id,  # para borrar el anuncio después
            "lugares": self.slots,
            "anotados": {},  # id de usuario -> número de lugar (desde 0)
        }
        try:
            message = await interaction.channel.send(
                content=f"📅 ¡Nuevo evento! Elige tu rol en la lista. Interesados en Discord: {event.url}",
                embed=build_slot_embed(data), view=SlotSignupView(data))
        except discord.HTTPException as e:
            log.error("[EVENTOS] ERROR: No se pudo publicar el anuncio: %s", e)
            await interaction.edit_original_response(content="⚠️ No pude publicar el anuncio en este canal.")
            return
        async with _lock:
            events = load_events()
            events[str(message.id)] = data
            save_events(events)
        log.info("[EVENTOS] %s creó %r (%s, %d jugadores) para %s", self.author, name, self.tipo,
                 len(self.slots), self.start.isoformat())
        await interaction.edit_original_response(content=f"✅ Evento publicado: {message.jump_url}")
        self.stop()


# ---------------------------------------------------------------- eventos de antes

def build_legacy_embed(data: dict) -> discord.Embed:
    """Anuncio de los eventos creados antes del asistente (botón por rol)."""
    start = datetime.fromisoformat(data["inicio"])
    ts = int(start.timestamp())
    signups = data["anotados"]
    embed = discord.Embed(title=f"⚔️ {data['nombre']}", url=data["url"], color=discord.Color.gold())
    embed.add_field(name="🕛 Hora", value=f"{start:%H:%M} UTC\n<t:{ts}:F> (tu hora)\n<t:{ts}:R>", inline=False)
    embed.add_field(name="👥 Anotados", value=f"**{len(signups)} / {data['participantes']}**", inline=False)
    for role in data["roles"]:
        names = [f"<@{uid}>" for uid, r in signups.items() if r == role]
        embed.add_field(name=f"{role_emoji(role)} {role} ({len(names)})",
                        value="\n".join(names) if names else "—")
    if data["info"]:
        embed.add_field(name="ℹ️ Información extra", value=data["info"], inline=False)
    embed.set_footer(text=f"Organiza: {data['organiza']}")
    return embed


class SignupButton(discord.ui.DynamicItem[discord.ui.Button],
                   template=r"hurtado:ev:(?P<idx>\d+|salir)"):
    """Botones de los eventos creados antes del asistente."""

    def __init__(self, idx: str):
        super().__init__(discord.ui.Button(label="Rol", custom_id=f"hurtado:ev:{idx}"))
        self.idx = idx

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["idx"])

    async def callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        async with _lock:
            events = load_events()
            data = events.get(str(interaction.message.id))
            if data is None or "roles" not in data:
                await interaction.response.send_message("Este evento ya no está activo.", ephemeral=True)
                return
            signups = data["anotados"]
            if self.idx == "salir":
                if signups.pop(user_id, None) is None:
                    await interaction.response.send_message("No estabas anotado.", ephemeral=True)
                    return
            else:
                role = data["roles"][int(self.idx)]
                if signups.get(user_id) == role:
                    await interaction.response.send_message(f"Ya estás anotado como **{role}**.", ephemeral=True)
                    return
                if user_id not in signups and len(signups) >= data["participantes"]:
                    await interaction.response.send_message("😬 El evento ya está lleno.", ephemeral=True)
                    return
                signups[user_id] = role
            save_events(events)
        await interaction.response.edit_message(embed=build_legacy_embed(data))


# ---------------------------------------------------------------- cog

class EventoView(discord.ui.View):
    """Botón que abre el asistente. Persistente: sigue funcionando tras reiniciar."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Crear evento", emoji="📅",
                       style=discord.ButtonStyle.success,
                       custom_id="seniorhurtadobot:evento")
    async def open_form(self, interaction: discord.Interaction, button: discord.ui.Button):
        await start_wizard(interaction)


async def start_wizard(interaction: discord.Interaction):
    if (problem := check_access(interaction)):
        await interaction.response.send_message(problem, ephemeral=True)
        return
    wizard = EventWizard(interaction.user)
    await interaction.response.send_message(embed=wizard.embed(), view=wizard, ephemeral=True)


class Eventos(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        bot.add_view(EventoView())
        bot.add_view(SlotSignupView())
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
        await ctx.send("📅 **Crear evento**\nPulsa el botón para empezar.", view=EventoView())

    @app_commands.command(name="evento", description="Crea un evento del gremio")
    @app_commands.guild_only()
    async def evento_slash(self, interaction: discord.Interaction):
        await start_wizard(interaction)

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
