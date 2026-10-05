"""Tarjeta de kill: imagen con el equipo de los dos jugadores sobre el fondo de la vecindad."""

import asyncio
import io
import logging
from datetime import datetime
from pathlib import Path

import aiohttp
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from bot import prices
from bot.config import ROOT_DIR

log = logging.getLogger("seniorhurtadobot.killcard")

BACKGROUND = ROOT_DIR / "assets" / "fondo_vecindad.webp"
ICON_CACHE = ROOT_DIR / "data" / "iconos"
ICON_URL = "https://render.albiononline.com/v1/item/{type}.png?count={count}&quality={quality}&size={size}"

WIDTH = 1280
EQ_ICON = 84  # tamaño de los iconos del equipo
INV_ICON = 64  # tamaño de los iconos del inventario
GAP = 8
INV_PER_ROW = 16

# Distribución del equipo en forma de personaje (filas x 3 columnas).
SLOTS = [
    ["Bag", "Head", "Cape"],
    ["MainHand", "Armor", "OffHand"],
    ["Potion", "Shoes", "Food"],
    [None, "Mount", None],
]

FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # servidor Ubuntu
    "C:/Windows/Fonts/arialbd.ttf",  # Windows
]


def font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_PATHS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def fmt(n: int) -> str:
    return f"{int(n):,}"


def short(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(int(n))


async def _icon(session: aiohttp.ClientSession, item: dict, size: int) -> Image.Image | None:
    """Icono del objeto (con su calidad y cantidad), con caché en disco."""
    count, quality = item.get("Count") or 1, item.get("Quality") or 1
    name = f"{item['Type']}_q{quality}_c{count}_{size}.png".replace("@", "-")
    path = ICON_CACHE / name
    if path.exists():
        return Image.open(path).convert("RGBA")
    url = ICON_URL.format(type=item["Type"], count=count, quality=quality, size=size)
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            data = await resp.read()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None
    ICON_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return Image.open(io.BytesIO(data)).convert("RGBA")


def _background(height: int) -> Image.Image:
    """Fondo de la vecindad recortado al tamaño de la tarjeta y algo oscurecido."""
    bg = Image.open(BACKGROUND).convert("RGB")
    scale = max(WIDTH / bg.width, height / bg.height)
    bg = bg.resize((round(bg.width * scale), round(bg.height * scale)), Image.LANCZOS)
    left = (bg.width - WIDTH) // 2
    top = (bg.height - height) // 2
    bg = bg.crop((left, top, left + WIDTH, top + height))
    return ImageEnhance.Brightness(bg).enhance(0.75).convert("RGBA")


def _panel(card: Image.Image, box: tuple[int, int, int, int], alpha: int = 150):
    overlay = Image.new("RGBA", card.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rounded_rectangle(box, radius=14, fill=(15, 10, 5, alpha))
    card.alpha_composite(overlay)


def _text(draw, xy, text, size, fill="white", anchor="la"):
    draw.text(xy, text, font=font(size), fill=fill, anchor=anchor,
              stroke_width=3, stroke_fill="black")


def _empty_slot(card: Image.Image, x: int, y: int, size: int):
    overlay = Image.new("RGBA", card.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rounded_rectangle((x + 4, y + 4, x + size - 4, y + size - 4),
                                              radius=8, fill=(0, 0, 0, 90))
    card.alpha_composite(overlay)


def _player_header(p: dict) -> tuple[str, str]:
    guild = p.get("GuildName") or "Sin gremio"
    if p.get("AllianceName"):
        guild = f"[{p['AllianceName']}] {guild}"
    return p["Name"], f"{guild} · IP {round(p.get('AverageItemPower') or 0)}"


async def render_card(event: dict, server: str = "americas") -> io.BytesIO:
    """Dibuja la tarjeta de la kill y la devuelve como JPEG en memoria."""
    killer, victim = event["Killer"], event["Victim"]
    inventory = [i for i in victim.get("Inventory") or [] if i]

    grid_w = 3 * EQ_ICON + 2 * GAP
    grid_h = len(SLOTS) * EQ_ICON + (len(SLOTS) - 1) * GAP
    top = 150
    inv_rows = (len(inventory) + INV_PER_ROW - 1) // INV_PER_ROW
    inv_top = top + grid_h + 60
    height = inv_top + (inv_rows * (INV_ICON + GAP) + 70 if inventory else 0) + 30

    # Iconos y precios en paralelo.
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        def eq(p):
            return [(slot, item) for slot, item in (p.get("Equipment") or {}).items() if item]
        tasks = {("k", s): _icon(session, it, EQ_ICON) for s, it in eq(killer)}
        tasks |= {("v", s): _icon(session, it, EQ_ICON) for s, it in eq(victim)}
        tasks |= {("i", n): _icon(session, it, INV_ICON) for n, it in enumerate(inventory)}
        keys = list(tasks)
        icons = dict(zip(keys, await asyncio.gather(*tasks.values())))
    victim_build = await prices.value_of(prices.equipment_items(victim), server)
    killer_build = await prices.value_of(prices.equipment_items(killer), server)
    inv_value = await prices.value_of(prices.inventory_items(victim), server)

    card = _background(height)
    draw = ImageDraw.Draw(card)
    margin = 60
    left_x, right_x = margin, WIDTH - margin - grid_w

    # Encabezados: asesino a la izquierda, víctima a la derecha.
    _panel(card, (20, 20, WIDTH - 20, top + grid_h + 30))
    name, sub = _player_header(killer)
    _text(draw, (left_x, 40), name, 34, fill=(120, 200, 255))
    _text(draw, (left_x, 84), sub, 18, fill=(230, 230, 230))
    name, sub = _player_header(victim)
    _text(draw, (WIDTH - margin, 40), name, 34, fill=(255, 120, 110), anchor="ra")
    _text(draw, (WIDTH - margin, 84), sub, 18, fill=(230, 230, 230), anchor="ra")

    # Equipo de cada uno.
    for side, x0 in (("k", left_x), ("v", right_x)):
        for r, row in enumerate(SLOTS):
            for c, slot in enumerate(row):
                if slot is None:
                    continue
                x, y = x0 + c * (EQ_ICON + GAP), top + r * (EQ_ICON + GAP)
                icon = icons.get((side, slot))
                if icon:
                    card.alpha_composite(icon.resize((EQ_ICON, EQ_ICON)), (x, y))
                else:
                    _empty_slot(card, x, y, EQ_ICON)
    _text(draw, (left_x, top + grid_h + 6), f"Build: {short(killer_build)}", 16, fill=(220, 220, 220))
    _text(draw, (right_x + grid_w, top + grid_h + 6), f"Build: {short(victim_build)}", 16,
          fill=(220, 220, 220), anchor="ra")

    # Centro: resultado, fama, valor y hora.
    cx = WIDTH // 2
    _text(draw, (cx, 40), "MATÓ A", 30, fill=(255, 215, 90), anchor="ma")
    participants = len(event.get("Participants") or [])
    _text(draw, (cx, 84), "Solo" if participants <= 1 else f"{participants} participantes",
          18, fill=(230, 230, 230), anchor="ma")
    fame = event.get("TotalVictimKillFame") or 0
    _text(draw, (cx, top + 40), "Fama", 20, fill=(200, 200, 200), anchor="ma")
    _text(draw, (cx, top + 66), fmt(fame), 34, fill=(255, 215, 90), anchor="ma")
    _text(draw, (cx, top + 130), "Valor aprox. (equipo + inventario)", 20, fill=(200, 200, 200), anchor="ma")
    _text(draw, (cx, top + 156), f"{fmt(victim_build + inv_value)} plata", 30,
          fill=(170, 230, 140), anchor="ma")
    when = datetime.fromisoformat(event["TimeStamp"][:19])
    _text(draw, (cx, top + 230), when.strftime("%H:%M UTC · %d/%m/%Y"), 20, fill=(220, 220, 220), anchor="ma")

    # Inventario de la víctima.
    if inventory:
        _panel(card, (20, inv_top - 10, WIDTH - 20, height - 20))
        _text(draw, (cx, inv_top + 4), f"Inventario de {victim['Name']} · {short(inv_value)}", 22,
              anchor="ma")
        row_w = min(len(inventory), INV_PER_ROW) * (INV_ICON + GAP) - GAP
        for n in range(len(inventory)):
            r, c = divmod(n, INV_PER_ROW)
            x = (WIDTH - row_w) // 2 + c * (INV_ICON + GAP)
            y = inv_top + 44 + r * (INV_ICON + GAP)
            icon = icons.get(("i", n))
            if icon:
                card.alpha_composite(icon.resize((INV_ICON, INV_ICON)), (x, y))
            else:
                _empty_slot(card, x, y, INV_ICON)

    out = io.BytesIO()
    card.convert("RGB").save(out, "JPEG", quality=88)
    out.seek(0)
    return out
