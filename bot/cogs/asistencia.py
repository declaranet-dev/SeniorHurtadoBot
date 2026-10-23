"""Asistencia de CTA.

Los CTA son en los horarios de CTA_HOURS_UTC (22, 02, 04 y 06 UTC), y cada
horario dura CTA_WINDOW_HOURS. Un horario cuenta como pelea de CTA si se
cumplen al menos 2 de estas 3 condiciones (con las 3 es "pelea segura"):

1. 12 o más personas a la vez en CTA 1, CTA 2, Barril o Chill.
2. Es un horario de CTA (siempre se cumple, porque solo se vigilan esos horarios).
3. Las batallas del gremio en ese horario suman al menos 100M de fama.

Así, si están en el Discord de la alianza y no en el nuestro, una batalla
grande en el horario también cuenta. Tiene asistencia quien estuvo al menos
10 minutos en esos canales de voz, o salió en una de esas batallas.
Cada 3 días se publica el informe en #asistencias-a-cta.
"""

import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot import albion, settings
from bot.cogs.revision import report_channel
from bot.config import (CTA_FILE, CTA_HOURS_UTC, CTA_KEY_HOURS_UTC, CTA_MIN_FAME, CTA_MIN_MINUTES,
                        CTA_MIN_PEOPLE, CTA_REPORT_DAYS, CTA_SETTLE_MINUTES, CTA_VOICE_CHANNELS,
                        CTA_WINDOW_HOURS, REGISTROS_FILE)

log = logging.getLogger("seniorhurtadobot.asistencia")

WINDOW = timedelta(hours=CTA_WINDOW_HOURS)
SETTLE = timedelta(minutes=CTA_SETTLE_MINUTES)
GIVE_UP = timedelta(hours=3)  # si la API no responde en este tiempo, se cierra sin la fama
# La API de Albion publica las batallas con horas de retraso: tras cerrar un horario
# se vuelve a pedir su fama cada hora durante este tiempo y se actualiza si sube.
RECHECK_FOR = timedelta(hours=12)
RECHECK_EVERY = timedelta(hours=1)
KEEP_DAYS = 30
MEDALS = ["🥇", "🥈", "🥉"]


def _load(path=CTA_FILE) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save(data: dict):
    CTA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CTA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CTA_FILE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value[:19]).replace(tzinfo=timezone.utc)


def short(n: float) -> str:
    n = float(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.0f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(int(n))


def window_start(now: datetime) -> datetime | None:
    """Inicio del horario de CTA en el que cae `now`, o None si no hay ninguno."""
    for hour in CTA_HOURS_UTC:
        start = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if start > now:
            start -= timedelta(days=1)
        if now < start + WINDOW:
            return start
    return None


def next_window(now: datetime) -> datetime:
    starts = []
    for hour in CTA_HOURS_UTC:
        start = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        starts.append(start if start > now else start + timedelta(days=1))
    return min(starts)


def window_label(start: datetime) -> str:
    star = " ⭐" if start.hour in CTA_KEY_HOURS_UTC else ""
    return f"{start:%d/%m} {start:%H} UTC{star}"


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " " + re.sub(r"[^a-z0-9]+", " ", text).strip() + " "


def cta_channels(guild: discord.Guild) -> list:
    """Canales de voz CTA 1, CTA 2, Barril y Chill (aunque lleven emojis en el nombre)."""
    out = []
    for channel in (*guild.voice_channels, *guild.stage_channels):
        name = _norm(channel.name)
        if any(f" {key} " in name for key in CTA_VOICE_CHANNELS):
            out.append(channel)
    return out


def present_members(guild: discord.Guild) -> list[discord.Member]:
    """Personas en esos canales ahora (sin bots ni ensordecidos)."""
    return [m for channel in cta_channels(guild) for m in channel.members
            if not m.bot and m.voice and not (m.voice.self_deaf or m.voice.deaf)]


def _name_index(guild: discord.Guild) -> dict[str, int]:
    """Nombre de Albion (en minúsculas) -> id de Discord, con el registro y el apodo."""
    index = {m.display_name.lower(): m.id for m in guild.members if not m.bot}
    for member_id, entry in (_load(REGISTROS_FILE).get(str(guild.id)) or {}).items():
        if entry.get("jugador"):
            index[entry["jugador"].lower()] = int(member_id)
    return index


async def close_window(guild: discord.Guild, config: dict, window: dict) -> dict:
    """Revisa las 3 condiciones de un horario que ya terminó. Puede lanzar AlbionAPIError."""
    start = _parse(window["inicio"])
    fame, battles, fighters = 0, 0, set()
    if config["gremio_id"]:
        found = await albion.get_guild_battles(config["gremio_id"], 50, config["servidor_albion"])
        for battle in found:
            if start <= _parse(battle["startTime"]) < start + WINDOW:
                battles += 1
                fame += battle.get("totalFame") or 0
                fighters |= {p["name"] for p in battle["players"].values()
                             if p.get("guildId") == config["gremio_id"]}
    return _summary(guild, window, fame, battles, fighters)


def _summary(guild: discord.Guild, window: dict, fame: int | None, battles: int,
             fighters: set[str]) -> dict:
    voice_ok = window["max_voz"] >= CTA_MIN_PEOPLE
    fame_ok = fame is not None and fame >= CTA_MIN_FAME
    met = 1 + voice_ok + fame_ok  # la condición del horario siempre se cumple
    summary = {"inicio": window["inicio"], "max_voz": window["max_voz"], "fama": fame,
               "batallas": battles, "voz_ok": voice_ok, "fama_ok": fame_ok,
               "cumple": met, "cta": met >= 2, "segura": met == 3, "asistentes": []}
    if summary["cta"]:
        people = {member_id for member_id, minutes in window["minutos"].items()
                  if minutes >= CTA_MIN_MINUTES}
        index = _name_index(guild)
        for name in fighters:
            member_id = index.get(name.lower())
            people.add(str(member_id) if member_id else f"albion:{name}")
        summary["asistentes"] = sorted(people)
    return summary


def _who(guild: discord.Guild, key: str) -> str:
    if key.startswith("albion:"):
        return f"**{key[7:]}** (Albion)"
    member = guild.get_member(int(key))
    return member.mention if member else f"<@{key}>"


def _join(lines: list[str], limit: int = 1000) -> str:
    out, used = [], 0
    for n, line in enumerate(lines):
        if used + len(line) + 1 > limit:
            out.append(f"… y {len(lines) - n} más")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def fight_line(cta: dict) -> str:
    mark = "✅" if cta["segura"] else "🟡"
    fame = "sin datos" if cta["fama"] is None else short(cta["fama"])
    return (f"{mark} {window_label(_parse(cta['inicio']))} · 🎙️ {cta['max_voz']} en voz · "
            f"⚔️ {fame} de fama ({cta['batallas']} batallas)")


def attendance(ctas: list[dict]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for cta in ctas:
        for key in cta["asistentes"]:
            counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items(), key=lambda item: -item[1])


def report_embed(guild: discord.Guild, ctas: list[dict], since: datetime, until: datetime) -> discord.Embed:
    total = len(ctas)
    embed = discord.Embed(
        title="⚔️ Asistencia de CTA", color=discord.Color.red(), timestamp=until,
        description=(f"Del {since:%d/%m %H:%M} al {until:%d/%m %H:%M} UTC.\n"
                     f"Peleas de CTA detectadas: **{total}** "
                     f"(✅ seguras: {sum(c['segura'] for c in ctas)}, 🟡 probables: "
                     f"{sum(not c['segura'] for c in ctas)})."))
    if not ctas:
        embed.add_field(name="Peleas", inline=False, value="No se detectó ningún CTA en estos días.")
        return embed
    embed.add_field(name="Peleas", inline=False, value=_join([fight_line(c) for c in ctas]))
    ranked = attendance(ctas)
    lines = []
    for n, (key, count) in enumerate(ranked):
        place = MEDALS[n] if n < len(MEDALS) else f"**{n + 1}.**"
        lines.append(f"{place} {_who(guild, key)} · {count}/{total}")
    embed.add_field(name="Asistencia", inline=False, value=_join(lines))
    member_role = settings.role(guild, "rol_miembro")
    if member_role is not None:
        came = {key for key, _ in ranked}
        missing = [m.mention for m in member_role.members if not m.bot and str(m.id) not in came]
        if missing:
            embed.add_field(name=f"Sin asistir ({member_role.name}: {len(missing)})", inline=False,
                            value=_join([", ".join(missing)]) if len(", ".join(missing)) <= 1000
                            else _join(missing))
    embed.set_footer(text="✅ cumple las 3 condiciones · 🟡 cumple 2 · ⭐ horario importante")
    return embed


class Asistencia(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tick.start()

    def cog_unload(self):
        self.tick.cancel()

    @tasks.loop(minutes=1)
    async def tick(self):
        data = _load()
        now = _now()
        start = window_start(now)
        for guild in self.bot.guilds:
            config = settings.get(guild.id)
            if not cta_channels(guild) and not config["gremio_id"]:
                continue
            entry = data.setdefault(str(guild.id), {})
            entry.setdefault("pendientes", [])
            entry.setdefault("ctas", [])
            entry.setdefault("ultimo_informe", now.isoformat(timespec="seconds"))
            current = entry.get("actual")
            if current and (start is None or current["inicio"] != start.isoformat()):
                entry["pendientes"].append(current)
                entry["actual"] = current = None
            if start is not None:
                if current is None:
                    current = entry["actual"] = {"inicio": start.isoformat(), "max_voz": 0, "minutos": {}}
                people = present_members(guild)
                current["max_voz"] = max(current["max_voz"], len(people))
                for member in people:
                    key = str(member.id)
                    current["minutos"][key] = current["minutos"].get(key, 0) + 1
            await self._close_pending(guild, config, entry, now)
            await self._maybe_report(guild, entry, now)
        _save(data)

    async def _close_pending(self, guild: discord.Guild, config: dict, entry: dict, now: datetime):
        still = []
        for window in entry["pendientes"]:
            end = _parse(window["inicio"]) + WINDOW
            # Con 12 o más en voz ya es pelea: se marca al terminar el horario,
            # y el battleboard solo la confirma después (se revisa cada hora).
            wait = timedelta(0) if window["max_voz"] >= CTA_MIN_PEOPLE else SETTLE
            if now < end + wait:
                still.append(window)
                continue
            try:
                summary = await close_window(guild, config, window)
            except albion.AlbionAPIError as e:
                if now < end + GIVE_UP:
                    log.warning("[CTA] %s: sin batallas de %s todavía: %s", guild.name, window["inicio"], e)
                    still.append(window)
                    continue
                summary = _summary(guild, window, None, 0, set())
            self._store(guild, entry, summary)
            entry.setdefault("revisar", []).append({**window, "revisado": now.isoformat(timespec="seconds")})
        entry["pendientes"] = still
        await self._recheck(guild, config, entry, now)
        cutoff = now - timedelta(days=KEEP_DAYS)
        entry["ctas"] = [c for c in entry["ctas"] if _parse(c["inicio"]) >= cutoff]

    def _store(self, guild: discord.Guild, entry: dict, summary: dict):
        """Guarda el resultado de un horario (o lo reemplaza si ya estaba)."""
        if not entry.get("ultimo") or entry["ultimo"]["inicio"] <= summary["inicio"]:
            entry["ultimo"] = {k: v for k, v in summary.items() if k != "asistentes"}
            entry["ultimo"]["personas"] = len(summary["asistentes"])
        entry["ctas"] = [c for c in entry["ctas"] if c["inicio"] != summary["inicio"]]
        if summary["cta"]:
            entry["ctas"].append(summary)
            entry["ctas"].sort(key=lambda c: c["inicio"])
        log.info("[CTA] %s: horario %s: voz %d, fama %s, %d de 3 -> %s", guild.name, summary["inicio"],
                 summary["max_voz"], summary["fama"], summary["cumple"],
                 "CTA" if summary["cta"] else "no fue CTA")

    async def _recheck(self, guild: discord.Guild, config: dict, entry: dict, now: datetime):
        """Vuelve a pedir la fama de los horarios recientes, por si la API publicó más batallas."""
        keep = []
        for window in entry.get("revisar", []):
            end = _parse(window["inicio"]) + WINDOW
            if now > end + RECHECK_FOR:
                continue  # ya no se revisa más
            keep.append(window)
            if now - _parse(window["revisado"]) < RECHECK_EVERY:
                continue
            window["revisado"] = now.isoformat(timespec="seconds")
            try:
                summary = await close_window(guild, config, window)
            except albion.AlbionAPIError:
                continue
            old = next((c for c in entry["ctas"] if c["inicio"] == window["inicio"]), None)
            old_fame = (old or (entry.get("ultimo") if (entry.get("ultimo") or {}).get("inicio") == window["inicio"]
                                else {}) or {}).get("fama") or 0
            changed_people = old is not None and set(old["asistentes"]) != set(summary["asistentes"])
            if (summary["fama"] or 0) > old_fame or changed_people or (summary["cta"] and old is None):
                self._store(guild, entry, summary)
        entry["revisar"] = keep

    async def _maybe_report(self, guild: discord.Guild, entry: dict, now: datetime):
        since = _parse(entry["ultimo_informe"])
        if now - since < timedelta(days=CTA_REPORT_DAYS):
            return
        channel = report_channel(guild)
        if channel is None:
            return
        ctas = [c for c in entry["ctas"] if _parse(c["inicio"]) >= since]
        try:
            await channel.send(embed=report_embed(guild, ctas, since, now),
                               allowed_mentions=discord.AllowedMentions.none())
            log.info("[CTA] %s: informe enviado a #%s (%d CTAs)", guild.name, channel.name, len(ctas))
        except discord.HTTPException as e:
            log.error("[CTA] %s: no pude enviar el informe: %s", guild.name, e)
            return
        entry["ultimo_informe"] = now.isoformat(timespec="seconds")

    @tick.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="cta", description="Cómo va el CTA actual (o cómo fue el último)")
    @app_commands.guild_only()
    async def cta(self, interaction: discord.Interaction):
        guild = interaction.guild
        entry = _load().get(str(guild.id)) or {}
        now = _now()
        start = window_start(now)
        embed = discord.Embed(title="⚔️ CTA", color=discord.Color.red())
        current = entry.get("actual")
        if start is not None:
            people = len(present_members(guild))
            top = max(people, (current or {}).get("max_voz", 0))
            end = start + WINDOW
            embed.add_field(name=f"🟢 Horario de las {start:%H} UTC en curso (hasta las {end:%H:%M} UTC)",
                            inline=False, value=(
                                f"🎙️ En voz ahora: **{people}/{CTA_MIN_PEOPLE}** · máximo: **{top}/{CTA_MIN_PEOPLE}**\n"
                                f"⚔️ La fama de las batallas se revisa {CTA_SETTLE_MINUTES} min después de terminar."))
        # Horarios que ya terminaron pero aún esperan la fama de las batallas.
        for window in entry.get("pendientes", []):
            wstart = _parse(window["inicio"])
            check = wstart + WINDOW + SETTLE
            voice_ok = window["max_voz"] >= CTA_MIN_PEOPLE
            embed.add_field(name=f"⏳ Horario {window_label(wstart)} (terminó, revisando)", inline=False, value=(
                f"🎙️ Máximo en voz: **{window['max_voz']}/{CTA_MIN_PEOPLE}** {'✅' if voice_ok else '❌'}\n"
                f"⚔️ La fama se revisa a las {check:%H:%M} UTC. "
                + ("Con la voz y el horario ya cuenta como pelea de CTA." if voice_ok
                   else "Si las batallas suman 100M o más, cuenta como pelea de CTA.")))
        last = entry.get("ultimo")
        if last:
            fame = "sin datos" if last["fama"] is None else short(last["fama"])
            verdict = ("✅ pelea segura" if last["segura"] else "🟡 pelea de CTA") if last["cta"] else "❌ no fue CTA"
            embed.add_field(name=f"Último horario: {window_label(_parse(last['inicio']))}", inline=False, value=(
                f"🎙️ Máximo en voz: **{last['max_voz']}/{CTA_MIN_PEOPLE}** · "
                f"⚔️ Fama: **{fame}/{short(CTA_MIN_FAME)}** ({last['batallas']} batallas)\n"
                f"Cumplió **{last['cumple']} de 3** → {verdict}"
                + (f" · {last['personas']} con asistencia" if last["cta"] else "")))
        if start is None and not last and not entry.get("pendientes"):
            embed.description = "0/0 registros por ahora."
        since = _parse(entry["ultimo_informe"]) if entry.get("ultimo_informe") else now
        count = sum(1 for c in entry.get("ctas", []) if _parse(c["inicio"]) >= since)
        nxt = next_window(now)
        embed.set_footer(text=f"Peleas desde el último informe: {count} · "
                              f"Próximo horario: {nxt:%H} UTC (en {fmt_wait(nxt - now)})")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="asistenciah", description="Resumen del servidor por gremio y asistencia a CTA")
    @app_commands.guild_only()
    async def asistenciah(self, interaction: discord.Interaction):
        guild = interaction.guild
        embed = composition_embed(guild)
        entry = _load().get(str(guild.id)) or {}
        since = _parse(entry["ultimo_informe"]) if entry.get("ultimo_informe") else _now()
        ctas = [c for c in entry.get("ctas", []) if _parse(c["inicio"]) >= since]
        if ctas:
            lines = [f"{_who(guild, key)} · {count}/{len(ctas)}" for key, count in attendance(ctas)[:15]]
            embed.add_field(name=f"⚔️ Asistencia a CTA desde el {since:%d/%m} ({len(ctas)} peleas)",
                            inline=False, value=_join(lines))
        else:
            embed.add_field(name=f"⚔️ Asistencia a CTA desde el {since:%d/%m}", inline=False,
                            value="0/0 registros por ahora.")
        await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())


def fmt_wait(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


def composition_embed(guild: discord.Guild) -> discord.Embed:
    """Cuántos hay de cada gremio: miembros, alianza por gremio, otros y sin registrar."""
    config = settings.get(guild.id)
    registros = _load(REGISTROS_FILE).get(str(guild.id)) or {}
    member_role = settings.role(guild, "rol_miembro")
    ally_role = settings.role(guild, "rol_aliado")
    other_role = settings.role(guild, "rol_externo")
    new_role = settings.role(guild, "rol_nuevo")
    humans = [m for m in guild.members if not m.bot]
    members = [m for m in humans if member_role and member_role in m.roles]
    allies = [m for m in humans if ally_role and ally_role in m.roles and m not in members]
    others = [m for m in humans if other_role and other_role in m.roles and m not in members and m not in allies]
    new = [m for m in humans if new_role and new_role in m.roles]
    by_guild: dict[str, int] = {}
    for m in allies:
        name = (registros.get(str(m.id)) or {}).get("gremio") or "sin registro"
        by_guild[name] = by_guild.get(name, 0) + 1
    lines = [f"🏠 **{len(members)}** de {config['gremio_nombre'] or 'el gremio'}"
             + (f" ({member_role.name})" if member_role else "")]
    for name, count in sorted(by_guild.items(), key=lambda item: -item[1]):
        lines.append(f"🤝 **{count}** de {name}")
    if other_role:
        lines.append(f"👥 **{len(others)}** de otros gremios ({other_role.name})")
    if new_role:
        lines.append(f"🆕 **{len(new)}** aún no se registran ({new_role.name})")
    embed = discord.Embed(title=f"📊 {guild.name}", color=discord.Color.purple(),
                          description="\n".join(lines))
    embed.set_footer(text="/chavodel8 · /alianza · /kikin para ver las listas")
    return embed


async def setup(bot: commands.Bot):
    await bot.add_cog(Asistencia(bot))
