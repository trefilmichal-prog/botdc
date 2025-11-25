import os
import discord
from discord import app_commands
from discord.ext import commands, tasks

import sqlite3
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, Any, List, Tuple, Optional
import asyncio
import random

# -------------------------------------------------
# KONFIGURACE
# -------------------------------------------------

# Token se načítá z environment proměnné DISCORD_TOKEN
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Chybí environment proměnná DISCORD_TOKEN s tokenem bota.")

DB_PATH = "wood_needs.db"         # SQLite databáze

# volitelné: role staff/admin, která má přístup do ticketů
STAFF_ROLE_ID = 0  # např. 123456789012345678 nebo 0 pokud nechceš používat

# připomínky materiálů
REMINDER_INTERVAL_HOURS = 3       # jak často se mají posílat DM (každých X hodin)
INACTIVE_THRESHOLD_HOURS = 24     # jak dlouho hráč nic neodevzdal = "dlouho nic nevložil"

# role pro oznámení giveaway
GIVEAWAY_PING_ROLE_ID = 1440268327892025438

# výchozí automatický konec giveaway po X minutách (pokud není nastaven jiný)
GIVEAWAY_AUTO_END_MINUTES = 15

# XP/coins za aktivitu
XP_PER_MESSAGE = 10
COINS_PER_MESSAGE = 5
XP_MESSAGE_MIN_CHARS = 5        # minimální délka zprávy, aby se počítala
XP_COOLDOWN_SECONDS = 30        # anti-spam – odměna max 1x za 30s na uživatele
XP_PER_LEVEL = 100              # každých 100 exp = +1 level (od levelu 1)


# -------------------------------------------------
# ENUM – DŘEVO, GIVEAWAY
# -------------------------------------------------

class WoodResource(str, Enum):
    WOOD = "wood"
    CACTUS_WOOD = "cactus wood"
    NUCLEAR_WOOD = "nuclear wood"
    UNDERWATER_WOOD = "underwater wood"
    ROYAL_WOOD = "royal wood"
    HACKER_WOOD = "hacker wood"
    DIAMOND_WOOD = "diamond wood"
    MAGMA_WOOD = "magma wood"
    HEAVEN_WOOD = "heaven wood"
    MAGIC_WOOD = "magic wood"
    CIRCUS_WOOD = "circus wood"
    JUNGLE_WOOD = "jungle wood"
    STEAMPUNK_WOOD = "steampunk wood"
    SAKURA_WOOD = "sakura wood"


class GiveawayType(str, Enum):
    COIN = "coin"
    PET = "pet"
    SCREEN = "screen"  # screen giveaway – X výherců, bez počtu


# -------------------------------------------------
# DB FUNKCE
# -------------------------------------------------

def get_connection():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = get_connection()
    c = conn.cursor()

    # dřevo
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS resources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS resource_targets (
            resource_id INTEGER PRIMARY KEY,
            required_amount INTEGER NOT NULL
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS resource_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id INTEGER NOT NULL,
            resource_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    # obecné nastavení (panel dřeva, panel timerů, giveaway, shop kanál)
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    # definice timerů
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS timers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            duration_minutes INTEGER NOT NULL
        )
        """
    )

    # běžící (aktivní) časovače – per user + název
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS active_timers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            timer_name TEXT NOT NULL,
            duration_minutes INTEGER NOT NULL,
            end_at TEXT NOT NULL,
            UNIQUE(user_id, timer_name)
        )
        """
    )

    # statistiky uživatelů – coins + exp + level + anti-spam timestamp
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS user_stats (
            discord_id INTEGER PRIMARY KEY,
            coins INTEGER NOT NULL DEFAULT 0,
            exp INTEGER NOT NULL DEFAULT 0,
            level INTEGER NOT NULL DEFAULT 1,
            last_xp_at TEXT
        )
        """
    )

    # shop položky
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS shop_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            image_url TEXT,
            price_coins INTEGER NOT NULL,
            stock INTEGER NOT NULL,
            seller_id INTEGER NOT NULL,
            channel_id INTEGER,
            message_id INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1
        )
        """
    )

    conn.commit()
    conn.close()


def set_setting(key: str, value: str):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    conn.commit()
    conn.close()


def get_setting(key: str) -> Optional[str]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


# ------------ DŘEVO DB ------------

def get_or_create_resource(name: str) -> int:
    norm_name = name.strip()
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO resources (name)
        VALUES (?)
        ON CONFLICT(name) DO NOTHING
        """,
        (norm_name,),
    )
    conn.commit()

    c.execute("SELECT id FROM resources WHERE name = ?", (norm_name,))
    row = c.fetchone()
    conn.close()
    if not row:
        raise RuntimeError("Nepodařilo se vytvořit resource.")
    return row[0]


def set_resource_need(resource_name: str, required_amount: int):
    resource_id = get_or_create_resource(resource_name)
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO resource_targets (resource_id, required_amount)
        VALUES (?, ?)
        ON CONFLICT(resource_id) DO UPDATE SET required_amount = excluded.required_amount
        """,
        (resource_id, required_amount),
    )
    conn.commit()
    conn.close()


def reset_resource_need(resource_name: Optional[str] = None):
    conn = get_connection()
    c = conn.cursor()

    if resource_name is None:
        c.execute("DELETE FROM resource_targets")
        c.execute("DELETE FROM resource_deliveries")
    else:
        resource_id = get_or_create_resource(resource_name)
        c.execute("DELETE FROM resource_targets WHERE resource_id = ?", (resource_id,))
        c.execute("DELETE FROM resource_deliveries WHERE resource_id = ?", (resource_id,))

    conn.commit()
    conn.close()


def add_delivery(discord_id: int, resource_name: str, amount: int):
    resource_id = get_or_create_resource(resource_name)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO resource_deliveries (discord_id, resource_id, amount, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (discord_id, resource_id, amount, now_str),
    )
    conn.commit()
    conn.close()


def get_resources_status() -> List[Tuple[str, int, int]]:
    """
    Vrací list (resource_name, required_amount, delivered_amount)
    """
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT
            r.name,
            t.required_amount,
            COALESCE(SUM(d.amount), 0) AS delivered
        FROM resource_targets t
        JOIN resources r ON r.id = t.resource_id
        LEFT JOIN resource_deliveries d ON d.resource_id = t.resource_id
        GROUP BY t.resource_id, r.name, t.required_amount
        ORDER BY r.name
        """
    )
    rows = c.fetchall()
    conn.close()
    return rows


def get_inactive_users(threshold_hours: int) -> List[int]:
    """
    Vrátí seznam discord_id hráčů, kteří mají nějaké odevzdávky,
    ale jejich poslední je starší než threshold_hours.
    """
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT discord_id, MAX(created_at) AS last_ts
        FROM resource_deliveries
        GROUP BY discord_id
        """
    )
    rows = c.fetchall()
    conn.close()

    inactive: List[int] = []
    now = datetime.now()
    for discord_id, last_ts in rows:
        try:
            last_dt = datetime.strptime(last_ts, "%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            continue
        if now - last_dt >= timedelta(hours=threshold_hours):
            inactive.append(discord_id)
    return inactive


# ------------ TIMER DB FUNKCE ------------

def create_or_update_timer(name: str, minutes: int) -> int:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO timers (name, duration_minutes)
        VALUES (?, ?)
        ON CONFLICT(name) DO UPDATE SET duration_minutes = excluded.duration_minutes
        """,
        (name, minutes),
    )
    conn.commit()
    c.execute("SELECT id FROM timers WHERE name = ?", (name,))
    row = c.fetchone()
    conn.close()
    if not row:
        raise RuntimeError("Nepodařilo se vytvořit / načíst timer.")
    return row[0]


def get_all_timers() -> List[Tuple[int, str, int]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT id, name, duration_minutes
        FROM timers
        ORDER BY name
        """
    )
    rows = c.fetchall()
    conn.close()
    return rows


def delete_timer(name: str) -> bool:
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM timers WHERE name = ?", (name,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return deleted > 0


def upsert_active_timer(user_id: int, timer_name: str, duration_minutes: int, end_at: datetime):
    conn = get_connection()
    c = conn.cursor()
    end_at_str = end_at.strftime("%Y-%m-%d %H:%M:%S")
    c.execute(
        """
        INSERT INTO active_timers (user_id, timer_name, duration_minutes, end_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, timer_name)
        DO UPDATE SET duration_minutes = excluded.duration_minutes,
                      end_at = excluded.end_at
        """,
        (user_id, timer_name, duration_minutes, end_at_str),
    )
    conn.commit()
    conn.close()


def delete_active_timer(user_id: int, timer_name: str):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "DELETE FROM active_timers WHERE user_id = ? AND timer_name = ?",
        (user_id, timer_name),
    )
    conn.commit()
    conn.close()


def delete_active_timers_for_name(timer_name: str):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM active_timers WHERE timer_name = ?", (timer_name,))
    conn.commit()
    conn.close()


def get_all_active_timers() -> List[Tuple[int, str, int, str]]:
    """
    Vrací list (user_id, timer_name, duration_minutes, end_at_str)
    """
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT user_id, timer_name, duration_minutes, end_at
        FROM active_timers
        """
    )
    rows = c.fetchall()
    conn.close()
    return rows


# ------------ USER STATS (XP/COINS/LEVEL) ------------

def get_or_create_user_stats(discord_id: int) -> Tuple[int, int, int, Optional[str]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT coins, exp, level, last_xp_at
        FROM user_stats
        WHERE discord_id = ?
        """,
        (discord_id,),
    )
    row = c.fetchone()
    if row is None:
        c.execute(
            """
            INSERT INTO user_stats (discord_id, coins, exp, level, last_xp_at)
            VALUES (?, 0, 0, 1, NULL)
            """,
            (discord_id,),
        )
        conn.commit()
        conn.close()
        return 0, 0, 1, None
    conn.close()
    return int(row[0]), int(row[1]), int(row[2]), row[3]


def update_user_stats(
    discord_id: int,
    coins: Optional[int] = None,
    exp: Optional[int] = None,
    level: Optional[int] = None,
    last_xp_at: Optional[Optional[str]] = None,
):
    conn = get_connection()
    c = conn.cursor()

    set_parts = []
    params: List[Any] = []

    if coins is not None:
        set_parts.append("coins = ?")
        params.append(coins)
    if exp is not None:
        set_parts.append("exp = ?")
        params.append(exp)
    if level is not None:
        set_parts.append("level = ?")
        params.append(level)
    if last_xp_at is not None:
        set_parts.append("last_xp_at = ?")
        params.append(last_xp_at)

    if not set_parts:
        conn.close()
        return

    sql = f"UPDATE user_stats SET {', '.join(set_parts)} WHERE discord_id = ?"
    params.append(discord_id)

    c.execute(sql, tuple(params))
    conn.commit()
    conn.close()


def add_message_activity(discord_id: int) -> None:
    """
    Anti-spam logika: XP/coins jen pokud:
    - zpráva není příliš krátká (řeší se v on_message – délka),
    - od poslední odměny uplynulo >= XP_COOLDOWN_SECONDS.
    """
    coins, exp, level, last_xp_at = get_or_create_user_stats(discord_id)

    now = datetime.utcnow()
    if last_xp_at:
        try:
            last_dt = datetime.strptime(last_xp_at, "%Y-%m-%d %H:%M:%S")
            if (now - last_dt).total_seconds() < XP_COOLDOWN_SECONDS:
                return  # spam – zatím nedáme odměnu
        except ValueError:
            pass

    new_exp = exp + XP_PER_MESSAGE
    new_coins = coins + COINS_PER_MESSAGE

    new_level = max(level, 1)
    lvl_from_exp = (new_exp // XP_PER_LEVEL) + 1
    if lvl_from_exp > new_level:
        new_level = lvl_from_exp

    update_user_stats(
        discord_id,
        coins=new_coins,
        exp=new_exp,
        level=new_level,
        last_xp_at=now.strftime("%Y-%m-%d %H:%M:%S"),
    )


# ------------ SHOP DB FUNKCE ------------

def create_shop_item(
    title: str,
    image_url: Optional[str],
    price_coins: int,
    stock: int,
    seller_id: int,
) -> int:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO shop_items (title, image_url, price_coins, stock, seller_id, is_active)
        VALUES (?, ?, ?, ?, ?, 1)
        """,
        (title, image_url, price_coins, stock, seller_id),
    )
    conn.commit()
    item_id = c.lastrowid
    conn.close()
    return int(item_id)


def set_shop_item_message(item_id: int, channel_id: int, message_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        UPDATE shop_items
        SET channel_id = ?, message_id = ?
        WHERE id = ?
        """,
        (channel_id, message_id, item_id),
    )
    conn.commit()
    conn.close()


def get_shop_item(item_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT id, title, image_url, price_coins, stock, seller_id, channel_id, message_id, is_active
        FROM shop_items
        WHERE id = ?
        """,
        (item_id,),
    )
    row = c.fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "id": int(row[0]),
        "title": row[1],
        "image_url": row[2],
        "price_coins": int(row[3]),
        "stock": int(row[4]),
        "seller_id": int(row[5]),
        "channel_id": int(row[6]) if row[6] is not None else None,
        "message_id": int(row[7]) if row[7] is not None else None,
        "is_active": int(row[8]),
    }


def decrement_shop_item_stock(item_id: int) -> Tuple[bool, int]:
    """
    Vrátí (success, remaining_stock).
    Pokud už není skladem nebo není aktivní, success=False.
    """
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT stock, is_active FROM shop_items WHERE id = ?",
        (item_id,),
    )
    row = c.fetchone()
    if row is None:
        conn.close()
        return False, 0

    stock = int(row[0])
    is_active = int(row[1])
    if is_active == 0 or stock <= 0:
        conn.close()
        return False, max(stock, 0)

    new_stock = stock - 1
    new_active = 1 if new_stock > 0 else 0

    c.execute(
        """
        UPDATE shop_items
        SET stock = ?, is_active = ?
        WHERE id = ?
        """,
        (new_stock, new_active, item_id),
    )
    conn.commit()
    conn.close()
    return True, new_stock


def get_active_shop_item_ids() -> List[int]:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT id
        FROM shop_items
        WHERE is_active = 1 AND channel_id IS NOT NULL AND message_id IS NOT NULL
        """
    )
    rows = c.fetchall()
    conn.close()
    return [int(r[0]) for r in rows]


# -------------------------------------------------
# DISCORD BOT – STAV
# -------------------------------------------------

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# pending_tickets[channel_id] = {"user_id": int, "resource": str}
pending_tickets: Dict[int, Dict[str, Any]] = {}

# běžící časovače: (user_id, timer_name) -> asyncio.Task
running_timers: Dict[tuple[int, str], asyncio.Task] = {}

# aktivní giveaway: message_id -> dict(state)
# state: {
#   "type": GiveawayType,
#   "amount" / "pet_name" / "click_value",
#   "participants": set[int],
#   "ended": bool,
#   "channel_id": int,
#   "host_id": int,
#   "image_url": Optional[str],
#   "winners_count": Optional[int],  # jen pro SCREEN
#   "duration": int                  # délka giveaway v minutách
# }
active_giveaways: Dict[int, Dict[str, Any]] = {}


# -------------------------------------------------
# HELPERY – PANEL DŘEVA
# -------------------------------------------------

async def update_panel():
    channel_id_str = get_setting("panel_channel_id")
    message_id_str = get_setting("panel_message_id")
    if not channel_id_str or not message_id_str:
        return

    try:
        channel_id = int(channel_id_str)
        message_id = int(message_id_str)
    except ValueError:
        return

    channel = bot.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return

    try:
        message = await channel.fetch_message(message_id)
    except (discord.NotFound, discord.Forbidden):
        return

    header_embed = discord.Embed(
        title="Suroviny – těžba dřeva (Ultimate Rebirth Champions)",
        description=(
            "Přehled, **kolik čeho je potřeba** a kolik už bylo celkem odevzdáno.\n"
            "K nahlášení odevzdaného dřeva použij tlačítko níže."
        ),
        color=0x00AAFF,
    )

    resources_embed = discord.Embed(
        title="Přehled dřev",
        color=0x00AAFF,
    )

    rows = get_resources_status()
    if not rows:
        resources_embed.add_field(
            name="Žádná data",
            value="Zatím není nastaveno, kolik čeho je potřeba.\nPoužij `/set_need`.",
            inline=False,
        )
    else:
        for resource_name, required_amount, delivered in rows:
            remaining = max(required_amount - delivered, 0)
            emoji = "✅" if delivered >= required_amount else "⏳"
            resources_embed.add_field(
                name=f"{emoji} {resource_name}",
                value=f"Odevzdáno: **{delivered}/{required_amount}** (zbývá {remaining})",
                inline=False,
            )

    await message.edit(embeds=[header_embed, resources_embed])


def build_needed_materials_embed(needed_rows: List[Tuple[str, int, int]]) -> discord.Embed:
    embed = discord.Embed(
        title="Potřebné materiály",
        description=(
            "Některé materiály stále chybí, budeme rádi za tvoji pomoc.\n"
            "Aktuální přehled níže:"
        ),
        color=0xFF8800,
    )

    for resource_name, required_amount, delivered in needed_rows:
        remaining = max(required_amount - delivered, 0)
        embed.add_field(
            name=resource_name,
            value=f"Potřeba: **{required_amount}**\nOdevzdáno: **{delivered}**\nZbývá: **{remaining}**",
            inline=False,
        )

    return embed


# ---------------------------------------------
# VIEW – výběr dřeva v ticketu
# ---------------------------------------------

class WoodSelectView(discord.ui.View):
    def __init__(self, ticket_owner_id: int, channel_id: int):
        super().__init__(timeout=None)
        self.ticket_owner_id = ticket_owner_id
        self.channel_id = channel_id

    @discord.ui.select(
        placeholder="Vyber typ dřeva",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label=res.value, value=res.value)
            for res in WoodResource
        ],
        custom_id="wood_select"
    )
    async def select_wood(
        self,
        interaction: discord.Interaction,
        select: discord.ui.Select,
    ):
        if interaction.user.id != self.ticket_owner_id:
            await interaction.response.send_message(
                "Toto je ticket jiného hráče.",
                ephemeral=True,
            )
            return

        resource_value = select.values[0]

        pending_tickets[self.channel_id] = {
            "user_id": self.ticket_owner_id,
            "resource": resource_value,
        }

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content=(
                f"Vybral jsi: **{resource_value}**.\n"
                f"Napiš do tohoto ticketu **jen číslo** (množství), například `64`.\n"
                f"Jakmile číslo odešleš, záznam se uloží a ticket se uzavře (kanál se smaže)."
            ),
            view=self,
        )


# ---------------------------------------------
# VIEW – tlačítko „Vytvořit ticket“
# ---------------------------------------------

class TicketButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Vytvořit ticket na odevzdání dřeva",
        style=discord.ButtonStyle.primary,
        custom_id="create_wood_ticket",
    )
    async def create_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Tento příkaz lze použít jen na serveru.",
                ephemeral=True,
            )
            return

        base_channel = interaction.channel
        category = base_channel.category if isinstance(base_channel, discord.TextChannel) else None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            ),
        }

        if STAFF_ROLE_ID != 0:
            staff_role = guild.get_role(STAFF_ROLE_ID)
            if staff_role is not None:
                overwrites[staff_role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_messages=True,
                )

        safe_name = interaction.user.name.lower().replace(" ", "-")
        channel_name = f"ticket-wood-{safe_name}"[:90]

        ticket_channel = await guild.create_text_channel(
            name=channel_name,
            overwrites=overwrites,
            category=category,
            reason=f"Ticket na dřevo od {interaction.user} ({interaction.user.id})",
        )

        view = WoodSelectView(ticket_owner_id=interaction.user.id, channel_id=ticket_channel.id)

        await ticket_channel.send(
            content=interaction.user.mention,
            embed=discord.Embed(
                title="Ticket – odevzdání dřeva",
                description=(
                    "1) V dropdown menu níže **vyber typ dřeva**.\n"
                    "2) Potom napiš **jen číslo** (množství), které jsi odevzdal.\n"
                    "3) Jakmile číslo odešleš, záznam se uloží a ticket se uzavře (kanál se smaže)."
                ),
                color=0x00AA00,
            ),
            view=view,
        )

        await interaction.response.send_message(
            f"Ticket byl vytvořen: {ticket_channel.mention}",
            ephemeral=True,
        )


# ------------ TIMERS VIEW & HELPERS ------------

class TimerButton(discord.ui.Button):
    def __init__(self, timer_id: int, name: str, minutes: int):
        super().__init__(
            label=name,
            style=discord.ButtonStyle.secondary,
            custom_id=f"timer_{timer_id}",
        )
        self.timer_id = timer_id
        self.timer_name = name
        self.minutes = minutes

    async def callback(self, interaction: discord.Interaction):
        user = interaction.user
        key = (user.id, self.timer_name)

        if key in running_timers and not running_timers[key].done():
            await interaction.response.send_message(
                f"Časovač **{self.timer_name}** ti už běží. "
                f"Počkej, až doběhne, nebo použij jiný časovač.",
                ephemeral=True,
            )
            return

        end_at = datetime.utcnow() + timedelta(minutes=self.minutes)
        upsert_active_timer(user.id, self.timer_name, self.minutes, end_at)

        await interaction.response.send_message(
            f"Časovač **{self.timer_name}** odstartoval na **{self.minutes}** minut.",
            ephemeral=True,
        )

        task = bot.loop.create_task(
            run_user_timer(user.id, self.timer_name, self.minutes, end_at, key)
        )
        running_timers[key] = task


class TimersView(discord.ui.View):
    def __init__(self, timers: List[Tuple[int, str, int]]):
        super().__init__(timeout=None)
        for timer_id, name, minutes in timers[:25]:
            self.add_item(TimerButton(timer_id, name, minutes))


async def run_user_timer(
    user_id: int,
    timer_name: str,
    duration_minutes: int,
    end_at: datetime,
    key: tuple[int, str],
):
    try:
        now = datetime.utcnow()
        seconds = (end_at - now).total_seconds()
        if seconds < 0:
            seconds = 0
        await asyncio.sleep(seconds)

        user = bot.get_user(user_id)
        if user is not None:
            try:
                await user.send(
                    f"Tvůj časovač **{timer_name}** (**{duration_minutes} min**) právě skončil."
                )
            except discord.Forbidden:
                pass
    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"Chyba v run_user_timer: {e}")
    finally:
        running_timers.pop(key, None)
        delete_active_timer(user_id, timer_name)


async def update_timers_panel():
    channel_id_str = get_setting("timers_channel_id")
    message_id_str = get_setting("timers_message_id")
    if not channel_id_str or not message_id_str:
        return

    try:
        channel_id = int(channel_id_str)
        message_id = int(message_id_str)
    except ValueError:
        return

    channel = bot.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return

    try:
        message = await channel.fetch_message(message_id)
    except (discord.NotFound, discord.Forbidden):
        return

    timers = get_all_timers()

    embed = discord.Embed(
        title="Panel časovačů",
        description=(
            "Stiskni tlačítko pro časovač, který chceš spustit.\n"
            "Každý časovač běží **pro tebe zvlášť** a po skončení ti přijde soukromá zpráva.\n"
            "Stejný časovač ti nemůže běžet dvakrát najednou."
        ),
        color=0x00CC66,
    )

    if not timers:
        embed.add_field(
            name="Žádné časovače",
            value="Zatím nejsou definované žádné časovače. Přidej je příkazem `/settimer`.",
            inline=False,
        )
        view = TimersView([])
    else:
        desc_lines = [
            f"- **{name}** – {minutes} min"
            for _, name, minutes in timers
        ]
        embed.add_field(
            name="Dostupné časovače",
            value="\n".join(desc_lines),
            inline=False,
        )
        view = TimersView(timers)

    await message.edit(embed=embed, view=view)


async def resume_timers():
    rows = get_all_active_timers()
    if not rows:
        return

    now = datetime.utcnow()

    for user_id, timer_name, duration_minutes, end_at_str in rows:
        try:
            end_at = datetime.strptime(end_at_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            delete_active_timer(user_id, timer_name)
            continue

        key = (user_id, timer_name)
        if key in running_timers and not running_timers[key].done():
            continue

        seconds = (end_at - now).total_seconds()
        if seconds <= 0:
            user = bot.get_user(user_id)
            if user is not None:
                try:
                    await user.send(
                        f"Tvůj časovač **{timer_name}** (**{duration_minutes} min**) právě skončil."
                    )
                except discord.Forbidden:
                    pass
            delete_active_timer(user_id, timer_name)
            continue

        task = bot.loop.create_task(
            run_user_timer(user_id, timer_name, duration_minutes, end_at, key)
        )
        running_timers[key] = task


# ---------------------------------------------
# SHOP – VIEW/TLAČÍTKO
# ---------------------------------------------

class BuyButton(discord.ui.Button):
    def __init__(self, item_id: int):
        super().__init__(
            label="Koupit",
            style=discord.ButtonStyle.primary,
            custom_id=f"shop_buy_{item_id}",
        )
        self.item_id = item_id

    async def callback(self, interaction: discord.Interaction):
        user = interaction.user
        if user.bot:
            await interaction.response.send_message(
                "Bot nemůže nakupovat.",
                ephemeral=True,
            )
            return

        item = get_shop_item(self.item_id)
        if item is None or item["is_active"] == 0 or item["stock"] <= 0:
            await interaction.response.send_message(
                "Tato položka už není dostupná (vyprodáno nebo odstraněno).",
                ephemeral=True,
            )
            return

        buyer_id = user.id
        coins, exp, level, _last = get_or_create_user_stats(buyer_id)

        price = item["price_coins"]
        if coins < price:
            await interaction.response.send_message(
                f"Nemáš dost coinů. Potřebuješ **{price}**, máš **{coins}**.",
                ephemeral=True,
            )
            return

        # Nejprve zkusíme odečíst sklad
        success, remaining_stock = decrement_shop_item_stock(self.item_id)
        if not success:
            await interaction.response.send_message(
                "Tuto položku už někdo těsně před tebou koupil – je vyprodána.",
                ephemeral=True,
            )
            return

        # Odečtení coinů kupujícímu
        new_coins = coins - price
        update_user_stats(buyer_id, coins=new_coins)

        # DM prodejci
        seller_id = item["seller_id"]
        seller_user = bot.get_user(seller_id)
        if seller_user is None:
            for guild in bot.guilds:
                member = guild.get_member(seller_id)
                if member is not None:
                    seller_user = member
                    break

        title = item["title"]
        try:
            if seller_user is not None:
                await seller_user.send(
                    f"🛒 Položka **{title}** byla právě koupena uživatelem {user.mention} "
                    f"za **{price}** coinů. Zbývající kusy: **{remaining_stock}**."
                )
        except discord.Forbidden:
            pass

        # DM kupujícímu
        try:
            await user.send(
                f"✅ Koupil jsi si položku **{title}** za **{price}** coinů.\n"
                f"Zůstatek: **{new_coins}** coinů."
            )
        except discord.Forbidden:
            pass

        # Aktualizace / smazání zprávy v shopu
        message = interaction.message
        if remaining_stock <= 0:
            # vyprodáno – smažeme zprávu
            try:
                await message.delete()
            except discord.Forbidden:
                # fallback – vypneme tlačítko
                for child in interaction.message.components[0].children:
                    child.disabled = True
                embed = message.embeds[0] if message.embeds else discord.Embed()
                embed = embed.copy()
                embed.description = f"**{title}** – vyprodáno."
                await message.edit(embed=embed, view=None)
        else:
            # jen update embedu se skladovým množstvím
            embed = message.embeds[0] if message.embeds else discord.Embed()
            embed = embed.copy()
            embed.title = title
            desc_lines = [
                f"Cena: **{price}** coinů",
                f"Skladem: **{remaining_stock}** ks"
            ]
            embed.description = "\n".join(desc_lines)
            await message.edit(embed=embed, view=self.view)

        await interaction.response.send_message(
            f"Koupil jsi **{title}** za **{price}** coinů.",
            ephemeral=True,
        )


class ShopItemView(discord.ui.View):
    def __init__(self, item_id: int):
        super().__init__(timeout=None)
        self.add_item(BuyButton(item_id))


async def register_shop_views():
    """
    Po restartu bota zaregistruje persistentní view pro všechny aktivní shop položky.
    """
    item_ids = get_active_shop_item_ids()
    for item_id in item_ids:
        bot.add_view(ShopItemView(item_id))


# ---------------------------------------------
# GIVEAWAY – LOGIKA LOSOVÁNÍ + DM VÝHERŮM
# ---------------------------------------------

async def finalize_giveaway(
    message: discord.Message,
    state: Dict[str, Any],
    view: "GiveawayView",
):
    """
    Náhodné losování (vizuální), úprava embedu, DM výhercům.
    """
    if state.get("ended"):
        return

    participants: set[int] = state.get("participants", set())
    if not participants:
        return

    state["ended"] = True

    embed = message.embeds[0] if message.embeds else discord.Embed(color=0xFFD700)
    embed = embed.copy()
    embed.color = 0xFFA500

    participants_list = list(participants)
    guild = message.guild
    guild_name = guild.name if guild else "serveru"
    host_id = state.get("host_id")
    host_mention = f"<@{host_id}>" if host_id else "organizátorem giveaway"

    # vizuální "rolování"
    for _ in range(5):
        candidate_id = random.choice(participants_list)
        embed.description = (
            "🎲 **Losuji výherce...**\n"
            f"Aktuální kandidát: <@{candidate_id}>"
        )
        await message.edit(embed=embed, view=view)
        await asyncio.sleep(0.8)

    gtype: GiveawayType = state["type"]
    winners_ids: List[int] = []

    if gtype == GiveawayType.COIN:
        amount: int = state["amount"]
        winners_count = min(3, len(participants_list))
        winners_ids = random.sample(participants_list, winners_count)

        base = amount // winners_count
        remainder = amount % winners_count

        winners_lines = []
        for idx, uid in enumerate(winners_ids):
            share = base + (1 if idx < remainder else 0)
            winners_lines.append(f"• <@{uid}> – **{share}** coinů")

        extra_message = f"Celkem rozdáno: **{amount}** coinů mezi {winners_count} hráče."
        embed.title = "🎉 Coin giveaway – výsledky"
        embed.description = extra_message + "\n\n" + "\n".join(winners_lines)

    elif gtype == GiveawayType.PET:
        pet_name: str = state["pet_name"]
        click_value: str = state["click_value"]
        winner_id = random.choice(participants_list)
        winners_ids = [winner_id]

        embed.title = "🎉 Pet giveaway – výsledky"
        embed.description = (
            f"Výherce peta **{pet_name}** (click hodnota: `{click_value}`):\n\n"
            f"🥇 <@{winner_id}>"
        )

    else:  # SCREEN
        configured = int(state.get("winners_count", 3))
        winners_count = min(configured, len(participants_list))
        winners_ids = random.sample(participants_list, winners_count)
        winners_lines = [f"• <@{uid}>" for uid in winners_ids]

        embed.title = "🎉 Screen giveaway – výsledky"
        embed.description = (
            f"Výherci z giveaway (nastaveno {configured} výherců, losováno {winners_count}):\n\n"
            + "\n".join(winners_lines)
        )

    embed.color = 0x00CC66
    embed.set_footer(text=f"Účastníků celkem: {len(participants_list)}")

    # vypnout tlačítka
    for child in view.children:
        child.disabled = True

    await message.edit(embed=embed, view=view)

    # DM výhercům
    for uid in winners_ids:
        user = bot.get_user(uid)
        if user is None and guild is not None:
            user = guild.get_member(uid)

        if user is None:
            continue

        try:
            if gtype == GiveawayType.COIN:
                amount: int = state["amount"]
                winners_count = len(winners_ids)
                base = amount // winners_count
                remainder = amount % winners_count
                idx = winners_ids.index(uid)
                share = base + (1 if idx < remainder else 0)

                dm_text = (
                    f"Ahoj, gratuluji! Vyhrál jsi v **coin giveaway** na serveru **{guild_name}**.\n"
                    f"Tvoje výhra: **{share}** coinů.\n"
                    f"Prosím, ozvi se {host_mention} na serveru (předzdivka / domluva ohledně předání výhry)."
                )

            elif gtype == GiveawayType.PET:
                pet_name: str = state["pet_name"]
                click_value: str = state["click_value"]
                dm_text = (
                    f"Ahoj, gratuluji! Vyhrál jsi v **pet giveaway** na serveru **{guild_name}**.\n"
                    f"Dostáváš peta **{pet_name}** (click hodnota: `{click_value}`).\n"
                    f"Prosím, ozvi se {host_mention} na serveru (předzdivka / předání výhry)."
                )
            else:  # SCREEN
                dm_text = (
                    f"Ahoj, gratuluji! Vyhrál jsi v **screen giveaway** na serveru **{guild_name}**.\n"
                    f"Odměny jsou vidět v obrázku v giveaway.\n"
                    f"Prosím, ozvi se {host_mention} na serveru (předzdivka / domluva ohledně přesné výhry)."
                )

            await user.send(dm_text)
        except discord.Forbidden:
            pass


async def schedule_giveaway_auto_end(message_id: int, duration_minutes: int):
    """
    Automaticky ukončí giveaway po duration_minutes, pokud ji někdo
    neukončí ručně dřív.
    """
    try:
        await asyncio.sleep(duration_minutes * 60)
    except asyncio.CancelledError:
        return

    state = active_giveaways.get(message_id)
    if not state or state.get("ended"):
        return

    channel_id = state.get("channel_id")
    if channel_id is None:
        return

    channel = bot.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return

    try:
        message = await channel.fetch_message(message_id)
    except (discord.NotFound, discord.Forbidden):
        return

    view = GiveawayView()
    await finalize_giveaway(message, state, view)

    await channel.send(
        f"Giveaway byla **automaticky ukončena** po {duration_minutes} minutách, výherci jsou zobrazeni v embedu."
    )


# ---------------------------------------------
# GIVEAWAY VIEW
# ---------------------------------------------

class GiveawayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Připojit se do giveaway",
        style=discord.ButtonStyle.success,
        custom_id="giveaway_join",
    )
    async def join_giveaway(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        message = interaction.message
        if message is None:
            await interaction.response.send_message(
                "Nelze načíst informaci o giveaway.",
                ephemeral=True,
            )
            return

        state = active_giveaways.get(message.id)
        if not state or state.get("ended"):
            await interaction.response.send_message(
                "Tato giveaway už není aktivní.",
                ephemeral=True,
            )
            return

        user_id = interaction.user.id
        participants: set[int] = state.setdefault("participants", set())

        if user_id in participants:
            await interaction.response.send_message(
                "Už jsi v této giveaway přihlášen.",
                ephemeral=True,
            )
            return

        participants.add(user_id)

        embed = message.embeds[0] if message.embeds else None
        if embed is None:
            embed = discord.Embed(color=0xFFD700)

        embed = embed.copy()
        embed.set_footer(text=f"Počet účastníků: {len(participants)}")

        await message.edit(embed=embed, view=self)
        await interaction.response.send_message(
            "Přihlásil ses do giveaway.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Ukončit giveaway",
        style=discord.ButtonStyle.danger,
        custom_id="giveaway_end",
    )
    async def end_giveaway(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Tuto giveaway může ukončit jen administrátor.",
                ephemeral=True,
            )
            return

        message = interaction.message
        if message is None:
            await interaction.response.send_message(
                "Nelze načíst informaci o giveaway.",
                ephemeral=True,
            )
            return

        state = active_giveaways.get(message.id)
        if not state or state.get("ended"):
            await interaction.response.send_message(
                "Tato giveaway už není aktivní.",
                ephemeral=True,
            )
            return

        participants: set[int] = state.get("participants", set())
        if not participants:
            await interaction.response.send_message(
                "Nikdo se nepřihlásil, giveaway nejde ukončit.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        await finalize_giveaway(message, state, self)

        await interaction.followup.send(
            "Giveaway byla ukončena, výherci jsou zobrazeni v embedu.",
            ephemeral=False,
        )


# ---------------------------------------------
# PERIODICKÉ PŘIPOMÍNKY MATERIÁLŮ
# ---------------------------------------------

@tasks.loop(hours=REMINDER_INTERVAL_HOURS)
async def materials_reminder_loop():
    try:
        rows = get_resources_status()
        needed = [
            (resource_name, required_amount, delivered)
            for (resource_name, required_amount, delivered) in rows
            if required_amount > delivered
        ]

        if not needed:
            return

        inactive_user_ids = get_inactive_users(INACTIVE_THRESHOLD_HOURS)
        if not inactive_user_ids:
            return

        embed = build_needed_materials_embed(needed)

        for user_id in inactive_user_ids:
            user = bot.get_user(user_id)
            if user is None:
                for guild in bot.guilds:
                    member = guild.get_member(user_id)
                    if member is not None:
                        user = member
                        break

            if user is None:
                continue

            try:
                await user.send(
                    content=(
                        "Ahoj, už delší dobu jsi nic neodevzdal z materiálů a **stále nám chybí suroviny**.\n"
                        "Kdyby ses mohl zapojit, pomůžeš celému týmu."
                    ),
                    embed=embed,
                )
            except discord.Forbidden:
                continue

    except Exception as e:
        print(f"Chyba v materials_reminder_loop: {e}")


# ---------------------------------------------
# EVENT: on_ready
# ---------------------------------------------

@bot.event
async def on_ready():
    bot.add_view(TicketButtonView())
    bot.add_view(GiveawayView())
    # persistentní view pro shop položky
    await register_shop_views()

    try:
        await bot.tree.sync()
        print(f"Přihlášen jako {bot.user} (ID: {bot.user.id})")
    except Exception as e:
        print(f"Chyba při syncu příkazů: {e}")

    await update_panel()
    await update_timers_panel()
    await resume_timers()

    if not materials_reminder_loop.is_running():
        materials_reminder_loop.start()


# ---------------------------------------------
# EVENT: on_message – XP/coins + ticket číslo
# ---------------------------------------------

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # XP/coins za aktivitu – anti-spam (délka + cooldown)
    if len(message.content.strip()) >= XP_MESSAGE_MIN_CHARS:
        try:
            add_message_activity(message.author.id)
        except Exception as e:
            print(f"Chyba při přidávání XP: {e}")

    channel_id = message.channel.id

    # ticket logika
    if channel_id not in pending_tickets:
        await bot.process_commands(message)
        return

    info = pending_tickets[channel_id]
    expected_user_id = info["user_id"]
    resource_name = info["resource"]

    if message.author.id != expected_user_id:
        await message.channel.send(
            "Toto je ticket jiného hráče. Jen vlastník ticketu sem může zadat číslo.",
            delete_after=10,
        )
        return

    content = message.content.strip()
    try:
        amount = int(content)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.channel.send(
            "Napiš prosím **jen kladné celé číslo** (např. `64`).",
            delete_after=10,
        )
        return

    target_user = message.author

    add_delivery(target_user.id, resource_name, amount)

    await message.channel.send(
        f"Zaznamenáno: {target_user.mention} – **{amount} × {resource_name}**.\n"
        f"Ticket kanál se nyní odstraní.",
    )

    pending_tickets.pop(channel_id, None)

    try:
        await update_panel()
    except Exception as e:
        print(f"Chyba update_panel po ticketu: {e}")

    try:
        await message.channel.delete(reason="Ticket uzavřen po zadání množství.")
    except discord.Forbidden:
        print("Bot nemá právo mazat kanály.")

    await bot.process_commands(message)


# -------------------------------------------------
# SLASH COMMANDS – PANEL DŘEVA
# -------------------------------------------------

@bot.tree.command(
    name="setup_panel",
    description="Vytvoří hlavní panel se surovinami a tlačítkem pro ticket (admin).",
)
@app_commands.checks.has_permissions(administrator=True)
async def setup_panel_cmd(interaction: discord.Interaction):
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message(
            "Tento příkaz lze použít pouze v textovém kanálu.",
            ephemeral=True,
        )
        return

    header_embed = discord.Embed(
        title="Suroviny – těžba dřeva (Ultimate Rebirth Champions)",
        description=(
            "Zde bude přehled, kolik je potřeba kterého dřeva a kolik už je odevzdáno.\n"
            "K nahlášení použij tlačítko níže."
        ),
        color=0x00AAFF,
    )

    resources_embed = discord.Embed(
        title="Přehled dřev",
        description="Zatím žádná potřeba není nastavená. Použij `/set_need`.",
        color=0x00AAFF,
    )

    msg = await channel.send(embeds=[header_embed, resources_embed], view=TicketButtonView())

    set_setting("panel_channel_id", str(channel.id))
    set_setting("panel_message_id", str(msg.id))

    await interaction.response.send_message(
        "Panel vytvořen v tomto kanálu.",
        ephemeral=True,
    )


@bot.tree.command(
    name="set_need",
    description="Nastaví, kolik je potřeba určitého dřeva.",
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    resource="Typ dřeva, pro který nastavuješ potřebu",
    required="Požadované množství (např. 1000)",
)
async def set_need_cmd(
    interaction: discord.Interaction,
    resource: WoodResource,
    required: app_commands.Range[int, 1, 10_000_000],
):
    resource_name = resource.value
    set_resource_need(resource_name, required)

    await interaction.response.send_message(
        f"Nastavena potřeba pro **{resource_name}**: **{required}** kusů.",
        ephemeral=True,
    )

    await update_panel()


@bot.tree.command(
    name="reset_need",
    description="Resetuje potřeby a odevzdané množství (globálně nebo pro jedno dřevo).",
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    resource="Konkrétní dřevo, které chceš resetovat (prázdné = všechno)",
)
async def reset_need_cmd(
    interaction: discord.Interaction,
    resource: Optional[WoodResource] = None,
):
    if resource is None:
        reset_resource_need(None)
        msg = "Resetovány všechny potřeby a všechna odevzdaná množství."
    else:
        reset_resource_need(resource.value)
        msg = f"Resetována potřeba a odevzdané množství pro **{resource.value}**."

    await interaction.response.send_message(msg, ephemeral=True)
    await update_panel()


@bot.tree.command(
    name="resources",
    description="Ukáže přehled nastavených potřeb a odevzdaného množství.",
)
async def resources_cmd(interaction: discord.Interaction):
    rows = get_resources_status()
    if not rows:
        await interaction.response.send_message(
            "Zatím není nastaveno, kolik čeho je potřeba.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="Aktuální stav surovin",
        color=0x00AAFF,
    )

    for resource_name, required_amount, delivered in rows:
        remaining = max(required_amount - delivered, 0)
        emoji = "✅" if delivered >= required_amount else "⏳"
        embed.add_field(
            name=f"{emoji} {resource_name}",
            value=f"Odevzdáno: **{delivered}/{required_amount}** (zbývá {remaining})",
            inline=False,
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


# -------------------------------------------------
# SLASH COMMANDS – TIMERY
# -------------------------------------------------

@bot.tree.command(
    name="setuptimers",
    description="Vloží do této místnosti panel s tlačítky časovačů (admin).",
)
@app_commands.checks.has_permissions(administrator=True)
async def setuptimers_cmd(interaction: discord.Interaction):
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message(
            "Tento příkaz lze použít pouze v textovém kanálu.",
            ephemeral=True,
        )
        return

    timers = get_all_timers()

    embed = discord.Embed(
        title="Panel časovačů",
        description=(
            "Stiskni tlačítko pro časovač, který chceš spustit.\n"
            "Každý hráč má vlastní časovače – běží odděleně.\n"
            "Stejný časovač ti nemůže běžet dvakrát současně.\n"
            "Po skončení přijde soukromá zpráva s názvem časovače."
        ),
        color=0x00CC66,
    )

    if not timers:
        embed.add_field(
            name="Žádné časovače",
            value="Zatím nejsou definované žádné časovače. Přidej je příkazem `/settimer`.",
            inline=False,
        )
        view = TimersView([])
    else:
        desc_lines = [
            f"- **{name}** – {minutes} min"
            for _, name, minutes in timers
        ]
        embed.add_field(
            name="Dostupné časovače",
            value="\n".join(desc_lines),
            inline=False,
        )
        view = TimersView(timers)

    msg = await channel.send(embed=embed, view=view)

    set_setting("timers_channel_id", str(channel.id))
    set_setting("timers_message_id", str(msg.id))

    await interaction.response.send_message(
        "Panel časovačů vytvořen v tomto kanálu.",
        ephemeral=True,
    )


@bot.tree.command(
    name="settimer",
    description="Přidá nebo upraví definici časovače (název + čas v minutách).",
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    name="Název časovače (např. Boost, Farm, Event)",
    minutes="Délka časovače v minutách",
)
async def settimer_cmd(
    interaction: discord.Interaction,
    name: str,
    minutes: app_commands.Range[int, 1, 10_000],
):
    create_or_update_timer(name, minutes)

    await interaction.response.send_message(
        f"Časovač **{name}** nastaven na **{minutes}** minut.",
        ephemeral=True,
    )

    await update_timers_panel()


@bot.tree.command(
    name="removetimer",
    description="Odstraní definici časovače podle názvu.",
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    name="Název časovače, který chceš smazat (stejný jako je v panelu).",
)
async def removetimer_cmd(
    interaction: discord.Interaction,
    name: str,
):
    ok = delete_timer(name)
    if not ok:
        await interaction.response.send_message(
            f"Časovač s názvem **{name}** nebyl nalezen.",
            ephemeral=True,
        )
        return

    keys_to_cancel = [key for key in running_timers.keys() if key[1] == name]
    for key in keys_to_cancel:
        task = running_timers.get(key)
        if task and not task.done():
            task.cancel()
        running_timers.pop(key, None)

    delete_active_timers_for_name(name)

    await interaction.response.send_message(
        f"Časovač **{name}** byl odstraněn (včetně běžících instancí).",
        ephemeral=True,
    )

    await update_timers_panel()


# -------------------------------------------------
# SLASH COMMANDS – GIVEAWAY
# -------------------------------------------------

@bot.tree.command(
    name="setupgiveaway",
    description="Nastaví tento kanál jako roomku pro giveaway (admin).",
)
@app_commands.checks.has_permissions(administrator=True)
async def setupgiveaway_cmd(interaction: discord.Interaction):
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message(
            "Tento příkaz lze použít pouze v textovém kanálu.",
            ephemeral=True,
        )
        return

    set_setting("giveaway_channel_id", str(channel.id))

    await interaction.response.send_message(
        f"Tento kanál byl nastaven jako giveaway roomka: {channel.mention}",
        ephemeral=True,
    )


@bot.tree.command(
    name="start_giveaway",
    description="Spustí giveaway typu coin, pet nebo screen v nastavené roomce.",
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    typ="Typ giveaway (coin, pet nebo screen)",
    amount="Počet coinů (pouze pro typ coin)",
    pet_name="Název peta (pouze pro typ pet)",
    click_value="Click hodnota peta jako text (pouze pro typ pet)",
    image="Screenshot / obrázek (volitelné u coin/pet, doporučené u screen)",
    screen_winners="Počet výherců pro screen giveaway (min 1, max 10)",
    duration_minutes="Za kolik minut se má giveaway automaticky ukončit (prázdné = 15)",
)
async def start_giveaway_cmd(
    interaction: discord.Interaction,
    typ: GiveawayType,
    amount: Optional[app_commands.Range[int, 1, 10_000_000]] = None,
    pet_name: Optional[str] = None,
    click_value: Optional[str] = None,
    image: Optional[discord.Attachment] = None,
    screen_winners: Optional[app_commands.Range[int, 1, 10]] = None,
    duration_minutes: Optional[app_commands.Range[int, 1, 1440]] = None,
):
    channel_id_str = get_setting("giveaway_channel_id")
    if not channel_id_str:
        await interaction.response.send_message(
            "Nejprve nastav giveaway roomku příkazem `/setupgiveaway`.",
            ephemeral=True,
        )
        return

    try:
        channel_id = int(channel_id_str)
    except ValueError:
        await interaction.response.send_message(
            "Uložená giveaway roomka má neplatné ID.",
            ephemeral=True,
        )
        return

    channel = bot.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message(
            "Giveaway roomka není textový kanál nebo se nenašla.",
            ephemeral=True,
        )
        return

    image_url: Optional[str] = image.url if image is not None else None
    duration = int(duration_minutes) if duration_minutes is not None else GIVEAWAY_AUTO_END_MINUTES

    if typ == GiveawayType.COIN:
        if amount is None:
            await interaction.response.send_message(
                "Pro typ `coin` je povinný parametr `amount`.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🎁 Coin giveaway",
            description=(
                f"Typ: **coins**\n"
                f"Celkem: **{amount}** coinů\n\n"
                "Klikni na tlačítko níže a připoj se.\n"
                "Po ukončení budou coiny **náhodně rozděleny** mezi až 3 výherce.\n"
                f"Giveaway se automaticky ukončí za {duration} minut."
            ),
            color=0xFFD700,
        )

        state: Dict[str, Any] = {
            "type": GiveawayType.COIN,
            "amount": int(amount),
            "participants": set(),
            "ended": False,
            "channel_id": channel.id,
            "host_id": interaction.user.id,
            "image_url": image_url,
            "duration": duration,
        }

    elif typ == GiveawayType.PET:
        if not pet_name or not click_value:
            await interaction.response.send_message(
                "Pro typ `pet` je povinný `pet_name` i `click_value`.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🎁 Pet giveaway",
            description=(
                f"Pet: **{pet_name}**\n"
                f"Click hodnota: `{click_value}`\n\n"
                "Klikni na tlačítko níže a připoj se.\n"
                "Po ukončení bude **náhodně vylosován jeden výherce**.\n"
                f"Giveaway se automaticky ukončí za {duration} minut."
            ),
            color=0xFF69B4,
        )

        state = {
            "type": GiveawayType.PET,
            "pet_name": pet_name,
            "click_value": click_value,
            "participants": set(),
            "ended": False,
            "channel_id": channel.id,
            "host_id": interaction.user.id,
            "image_url": image_url,
            "duration": duration,
        }

    else:  # SCREEN
        winners_count = int(screen_winners) if screen_winners is not None else 3

        embed = discord.Embed(
            title="🎁 Screen giveaway",
            description=(
                "Giveaway podle screenu / obrázku níže.\n\n"
                "Klikni na tlačítko níže a připoj se.\n"
                f"Po ukončení budou **náhodně vylosováni až {winners_count} výherci**.\n"
                f"Giveaway se automaticky ukončí za {duration} minut."
            ),
            color=0x00BFFF,
        )

        state = {
            "type": GiveawayType.SCREEN,
            "participants": set(),
            "ended": False,
            "channel_id": channel.id,
            "host_id": interaction.user.id,
            "image_url": image_url,
            "winners_count": winners_count,
            "duration": duration,
        }

    if image_url:
        embed.set_image(url=image_url)

    view = GiveawayView()

    content = ""
    if GIVEAWAY_PING_ROLE_ID:
        content = f"<@&{GIVEAWAY_PING_ROLE_ID}>"

    msg = await channel.send(content=content, embed=embed, view=view)

    active_giveaways[msg.id] = state

    bot.loop.create_task(schedule_giveaway_auto_end(msg.id, duration))

    await interaction.response.send_message(
        f"Giveaway spuštěna v {channel.mention} a automaticky se ukončí za {duration} minut.",
        ephemeral=True,
    )


# -------------------------------------------------
# SLASH COMMANDS – SHOP
# -------------------------------------------------

@bot.tree.command(
    name="setupshop",
    description="Nastaví tento kanál jako roomku pro shop (admin).",
)
@app_commands.checks.has_permissions(administrator=True)
async def setupshop_cmd(interaction: discord.Interaction):
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message(
            "Tento příkaz lze použít pouze v textovém kanálu.",
            ephemeral=True,
        )
        return

    set_setting("shop_channel_id", str(channel.id))

    await interaction.response.send_message(
        f"Tento kanál byl nastaven jako shop roomka: {channel.mention}",
        ephemeral=True,
    )


@bot.tree.command(
    name="addshopitem",
    description="Přidá položku do shopu (screen, cena, počet kusů).",
)
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    title="Název položky",
    price_coins="Cena v coinech",
    stock="Počet kusů skladem",
    image="Screenshot / obrázek položky",
)
async def addshopitem_cmd(
    interaction: discord.Interaction,
    title: str,
    price_coins: app_commands.Range[int, 1, 10_000_000],
    stock: app_commands.Range[int, 1, 10_000],
    image: discord.Attachment,
):
    shop_channel_id_str = get_setting("shop_channel_id")
    if not shop_channel_id_str:
        await interaction.response.send_message(
            "Nejprve nastav shop roomku příkazem `/setupshop`.",
            ephemeral=True,
        )
        return

    try:
        shop_channel_id = int(shop_channel_id_str)
    except ValueError:
        await interaction.response.send_message(
            "Uložená shop roomka má neplatné ID.",
            ephemeral=True,
        )
        return

    channel = bot.get_channel(shop_channel_id)
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message(
            "Shop roomka není textový kanál nebo se nenašla.",
            ephemeral=True,
        )
        return

    image_url = image.url if image is not None else None

    item_id = create_shop_item(
        title=title,
        image_url=image_url,
        price_coins=int(price_coins),
        stock=int(stock),
        seller_id=interaction.user.id,
    )

    embed = discord.Embed(
        title=title,
        description=f"Cena: **{price_coins}** coinů\nSkladem: **{stock}** ks",
        color=0x00CCFF,
    )
    if image_url:
        embed.set_image(url=image_url)

    view = ShopItemView(item_id)

    msg = await channel.send(embed=embed, view=view)

    set_shop_item_message(item_id, channel.id, msg.id)

    await interaction.response.send_message(
        f"Položka **{title}** byla přidána do shopu v {channel.mention}.",
        ephemeral=True,
    )


# -------------------------------------------------
# SLASH COMMAND – PROFIL (coins/exp/level)
# -------------------------------------------------

@bot.tree.command(
    name="profile",
    description="Ukáže coiny, exp a level hráče.",
)
@app_commands.describe(
    user="Kterého uživatele zobrazit (prázdné = ty).",
)
async def profile_cmd(
    interaction: discord.Interaction,
    user: Optional[discord.Member] = None,
):
    target = user or interaction.user
    coins, exp, level, _last = get_or_create_user_stats(target.id)

    embed = discord.Embed(
        title=f"Profil – {target.display_name}",
        color=0x00DD88,
    )
    embed.add_field(name="Level", value=str(level), inline=True)
    embed.add_field(name="Exp", value=str(exp), inline=True)
    embed.add_field(name="Coiny", value=str(coins), inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


# -------------------------------------------------
# START
# -------------------------------------------------

if __name__ == "__main__":
    init_db()
    bot.run(TOKEN)
