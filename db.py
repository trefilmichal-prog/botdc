import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, List, Tuple, Any, Dict

from config import DB_PATH, INACTIVE_THRESHOLD_HOURS, CLAN_TICKET_CLEANUP_MINUTES


def normalize_clan_member_name(text: str) -> str:
    if not text:
        return ""
    raw_text = str(text)
    without_format_chars = "".join(
        char for char in raw_text if unicodedata.category(char) != "Cf"
    )
    normalized = without_format_chars.casefold().strip()
    normalized = re.sub(r"[\u00a0\u200b\u200c\u200d\ufeff]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def increment_secret_drop_stat(date_value: str, user_id: int, amount: int = 1) -> None:
    conn = None
    try:
        conn = get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO secret_drop_stats (date, user_id, count)
                VALUES (?, ?, ?)
                ON CONFLICT(date, user_id)
                DO UPDATE SET count = count + excluded.count
                """,
                (date_value, user_id, amount),
            )
    finally:
        if conn is not None:
            conn.close()


def add_secret_drop_event(occurred_at: datetime, user_id: int, rarity: str) -> None:
    conn = None
    try:
        conn = get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO secret_drop_events (occurred_at, user_id, rarity)
                VALUES (?, ?, ?)
                """,
                (occurred_at.isoformat(), user_id, rarity),
            )
    finally:
        if conn is not None:
            conn.close()


def get_secret_drop_breakdown_since(since: datetime) -> Dict[int, Dict[str, int]]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.execute(
            """
            SELECT user_id, rarity, COUNT(*) AS total_count
            FROM secret_drop_events
            WHERE occurred_at >= ?
            GROUP BY user_id, rarity
            """,
            (since.isoformat(),),
        )
        results: Dict[int, Dict[str, int]] = {}
        for user_id, rarity, total_count in cursor.fetchall():
            results.setdefault(int(user_id), {})[str(rarity)] = int(total_count)
        return results
    finally:
        if conn is not None:
            conn.close()


def get_secret_drop_leaderboard(limit: int = 10) -> List[Tuple[int, int]]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.execute(
            """
            SELECT user_id, SUM(count) AS total_count
            FROM secret_drop_stats
            GROUP BY user_id
            ORDER BY total_count DESC, user_id ASC
            LIMIT ?
            """,
            (limit,),
        )
        return [(int(row[0]), int(row[1])) for row in cursor.fetchall()]
    finally:
        if conn is not None:
            conn.close()


def get_secret_drop_totals() -> Dict[int, int]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.execute(
            """
            SELECT user_id, SUM(count) AS total_count
            FROM secret_drop_stats
            GROUP BY user_id
            """
        )
        return {int(row[0]): int(row[1]) for row in cursor.fetchall()}
    finally:
        if conn is not None:
            conn.close()


def reset_secret_drop_stats() -> None:
    conn = None
    try:
        conn = get_connection()
        with conn:
            conn.execute("DELETE FROM secret_drop_stats")
            conn.execute("DELETE FROM secret_drop_events")
    finally:
        if conn is not None:
            conn.close()


def init_db():
    conn = get_connection()
    c = conn.cursor()

    # Dřevo
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

    # Obecné nastavení
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS secret_drop_stats (
            date TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (date, user_id)
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS secret_drop_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            rarity TEXT NOT NULL
        )
        """
    )

    c.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_secret_drop_events_occurred_at
        ON secret_drop_events (occurred_at)
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS discord_write_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation TEXT NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_error TEXT
        )
        """
    )

    # Roblox sledování aktivity
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS roblox_tracking_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            tracking_enabled INTEGER NOT NULL,
            session_started_at TEXT NOT NULL,
            session_ended_at TEXT,
            last_channel_report_at TEXT
        )
        """
    )

    try:
        c.execute(
            "ALTER TABLE roblox_tracking_state ADD COLUMN last_channel_report_at TEXT"
        )
    except sqlite3.OperationalError:
        # Column already exists – ignore.
        pass

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS roblox_duration_totals (
            user_id INTEGER PRIMARY KEY,
            online_seconds REAL NOT NULL DEFAULT 0,
            offline_seconds REAL NOT NULL DEFAULT 0,
            label TEXT
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS roblox_presence_state (
            user_id INTEGER PRIMARY KEY,
            status INTEGER,
            last_change TEXT,
            last_update TEXT,
            count_offline INTEGER
        )
        """
    )

    try:
        c.execute("ALTER TABLE roblox_presence_state ADD COLUMN count_offline INTEGER")
    except sqlite3.OperationalError:
        pass

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS clan_panels (
            message_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS clan_application_panels (
            message_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS clan_panel_configs (
            guild_id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            us_requirements TEXT NOT NULL,
            cz_requirements TEXT NOT NULL
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS clan_clans (
            guild_id INTEGER NOT NULL,
            clan_key TEXT NOT NULL,
            display_name TEXT NOT NULL,
            description TEXT NOT NULL,
            accept_role_id INTEGER,
            accept_role_id_cz INTEGER,
            accept_role_id_en INTEGER,
            accept_category_id INTEGER,
            review_role_id INTEGER,
            sort_order INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, clan_key)
        )
        """
    )

    try:
        c.execute("ALTER TABLE clan_clans ADD COLUMN accept_role_id_cz INTEGER")
    except sqlite3.OperationalError:
        pass

    try:
        c.execute("ALTER TABLE clan_clans ADD COLUMN accept_role_id_en INTEGER")
    except sqlite3.OperationalError:
        pass

    try:
        c.execute("ALTER TABLE clan_clans ADD COLUMN accept_category_id INTEGER")
    except sqlite3.OperationalError:
        pass

    try:
        c.execute("ALTER TABLE clan_clans ADD COLUMN sort_order INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS leaderboard_panels (
            message_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS dropstats_panels (
            message_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS sp_panels (
            message_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL
        )
        """
    )

    # Timery
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS timers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            duration_minutes INTEGER NOT NULL
        )
        """
    )

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

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS active_giveaways (
            message_id INTEGER PRIMARY KEY,
            channel_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            host_id INTEGER,
            amount INTEGER,
            pet_name TEXT,
            click_value TEXT,
            auction_item TEXT,
            starting_bid INTEGER,
            image_url TEXT,
            winners_count INTEGER,
            duration_minutes INTEGER NOT NULL,
            end_at TEXT NOT NULL,
            participants_json TEXT NOT NULL,
            bids_json TEXT
        )
        """
    )

    try:
        c.execute("ALTER TABLE active_giveaways ADD COLUMN auction_item TEXT")
    except sqlite3.OperationalError:
        pass

    try:
        c.execute("ALTER TABLE active_giveaways ADD COLUMN starting_bid INTEGER")
    except sqlite3.OperationalError:
        pass

    try:
        c.execute("ALTER TABLE active_giveaways ADD COLUMN bids_json TEXT")
    except sqlite3.OperationalError:
        pass

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS attendance_panels (
            message_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            statuses_json TEXT NOT NULL
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS prophecy_logs (
            message_id INTEGER PRIMARY KEY,
            channel_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            model TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS windows_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    # Statistiky uživatelů (XP/coins/level/messages)
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

    ensure_user_stats_columns()

    # Shop položky
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

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS shop_purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            buyer_id INTEGER NOT NULL,
            seller_id INTEGER NOT NULL,
            price_coins INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0,
            quantity INTEGER NOT NULL DEFAULT 1
        )
        """
    )

    c.execute("PRAGMA table_info(shop_purchases)")
    shop_purchases_columns = [row[1] for row in c.fetchall()]
    if "quantity" not in shop_purchases_columns:
        c.execute(
            "ALTER TABLE shop_purchases ADD COLUMN quantity INTEGER NOT NULL DEFAULT 1"
        )

    # CLAN – přihlášky do klanu
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS clan_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            roblox_nick TEXT,
            hours_per_day TEXT,
            rebirths TEXT,
            locale TEXT NOT NULL DEFAULT 'en',
            status TEXT NOT NULL,       -- 'open', 'accepted', 'rejected'
            created_at TEXT NOT NULL,   -- %Y-%m-%d %H:%M:%S
            decided_at TEXT,            -- %Y-%m-%d %H:%M:%S
            last_message_at TEXT,       -- %Y-%m-%d %H:%M:%S
            last_message_by_bot INTEGER NOT NULL DEFAULT 0,
            last_ping_at TEXT,          -- %Y-%m-%d %H:%M:%S
            deleted INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS clan_ticket_vacations (
            channel_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            clan_key TEXT,
            prev_category_id INTEGER,
            removed_role_ids_json TEXT NOT NULL,
            vacation_role_id INTEGER NOT NULL,
            moved_at TEXT NOT NULL
        )
        """
    )

    c.execute("PRAGMA table_info(clan_applications)")
    columns = [row[1] for row in c.fetchall()]
    if "locale" not in columns:
        c.execute(
            "ALTER TABLE clan_applications ADD COLUMN locale TEXT NOT NULL DEFAULT 'en'"
        )
    if "last_message_at" not in columns:
        c.execute(
            "ALTER TABLE clan_applications ADD COLUMN last_message_at TEXT"
        )
    if "last_message_by_bot" not in columns:
        c.execute(
            "ALTER TABLE clan_applications ADD COLUMN last_message_by_bot INTEGER NOT NULL DEFAULT 0"
        )
    if "last_ping_at" not in columns:
        c.execute(
            "ALTER TABLE clan_applications ADD COLUMN last_ping_at TEXT"
        )

    conn.commit()
    conn.close()


# ---------- SETTINGS ----------

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


def set_secret_notifications_role_ids(role_ids: List[int]) -> None:
    normalized = [int(role_id) for role_id in role_ids if role_id]
    set_setting("secret_notifications_role_ids", json.dumps(normalized))


def get_secret_notifications_role_ids() -> List[int]:
    value = get_setting("secret_notifications_role_ids")
    if not value:
        return []
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    result: List[int] = []
    for entry in payload:
        try:
            role_id = int(entry)
        except (TypeError, ValueError):
            continue
        result.append(role_id)
    return result


def clan_member_nick_exists(nick: str) -> bool:
    if not nick:
        return False
    normalized = normalize_clan_member_name(nick)
    if not normalized:
        return False
    payload = get_setting("secret_notifications_clan_member_cache")
    if not payload:
        return False
    try:
        cache = json.loads(payload)
    except json.JSONDecodeError:
        return False
    if not isinstance(cache, dict):
        return False
    return normalized in cache


def set_clan_stats_channel(channel_id: int):
    set_setting("clan_stats_channel_id", str(channel_id))


def get_clan_stats_channel() -> Optional[int]:
    value = get_setting("clan_stats_channel_id")
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def set_log_channel_id(channel_id: int) -> None:
    set_setting("log_channel_id", str(channel_id))


def get_log_channel_id() -> Optional[int]:
    value = get_setting("log_channel_id")
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


# ---------- GIVEAWAYS ----------


def save_giveaway_state(message_id: int, state: Dict[str, Any]):
    conn = get_connection()
    c = conn.cursor()

    participants = state.get("participants", set())
    if participants is None:
        participants = set()
    participants_json = json.dumps(list(participants))

    bids = state.get("bids", {})
    if bids is None:
        bids = {}
    bids_json = json.dumps({str(k): v for k, v in bids.items()})

    end_at = state.get("end_at")
    end_at_str = end_at.isoformat() if isinstance(end_at, datetime) else ""

    gtype = state.get("type")
    gtype_value = gtype.value if isinstance(gtype, Enum) else str(gtype)

    c.execute(
        """
        INSERT INTO active_giveaways (
            message_id, channel_id, type, host_id, amount, pet_name, click_value,
            auction_item, starting_bid, image_url, winners_count, duration_minutes,
            end_at, participants_json, bids_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(message_id) DO UPDATE SET
            channel_id = excluded.channel_id,
            type = excluded.type,
            host_id = excluded.host_id,
            amount = excluded.amount,
            pet_name = excluded.pet_name,
            click_value = excluded.click_value,
            auction_item = excluded.auction_item,
            starting_bid = excluded.starting_bid,
            image_url = excluded.image_url,
            winners_count = excluded.winners_count,
            duration_minutes = excluded.duration_minutes,
            end_at = excluded.end_at,
            participants_json = excluded.participants_json,
            bids_json = excluded.bids_json
        """,
        (
            message_id,
            int(state.get("channel_id", 0)),
            gtype_value,
            state.get("host_id"),
            state.get("amount"),
            state.get("pet_name"),
            state.get("click_value"),
            state.get("auction_item"),
            state.get("starting_bid"),
            state.get("image_url"),
            state.get("winners_count"),
            state.get("duration", 0),
            end_at_str,
            participants_json,
            bids_json,
        ),
    )
    conn.commit()
    conn.close()


def load_active_giveaways() -> List[Tuple[int, Dict[str, Any]]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT message_id, channel_id, type, host_id, amount, pet_name, click_value,
               auction_item, starting_bid, image_url, winners_count, duration_minutes,
               end_at, participants_json, bids_json
        FROM active_giveaways
        """
    )
    rows = c.fetchall()
    conn.close()

    giveaways: List[Tuple[int, Dict[str, Any]]] = []
    for row in rows:
        (
            message_id,
            channel_id,
            gtype,
            host_id,
            amount,
            pet_name,
            click_value,
            auction_item,
            starting_bid,
            image_url,
            winners_count,
            duration_minutes,
            end_at_str,
            participants_json,
            bids_json,
        ) = row

        participants = set(json.loads(participants_json)) if participants_json else set()
        bids_payload = json.loads(bids_json) if bids_json else {}
        bids = {int(key): int(value) for key, value in bids_payload.items()}
        end_at = datetime.fromisoformat(end_at_str) if end_at_str else None

        giveaways.append(
            (
                message_id,
                {
                    "channel_id": channel_id,
                    "type": gtype,
                    "host_id": host_id,
                    "amount": amount,
                    "pet_name": pet_name,
                    "click_value": click_value,
                    "auction_item": auction_item,
                    "starting_bid": starting_bid,
                    "image_url": image_url,
                    "winners_count": winners_count,
                    "duration": duration_minutes,
                    "end_at": end_at,
                    "participants": participants,
                    "bids": bids,
                    "ended": False,
                },
            )
        )

    return giveaways


def get_active_giveaway(message_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT message_id, channel_id, type, host_id, amount, pet_name, click_value,
               auction_item, starting_bid, image_url, winners_count, duration_minutes,
               end_at, participants_json, bids_json
        FROM active_giveaways
        WHERE message_id = ?
        """,
        (message_id,),
    )
    row = c.fetchone()
    conn.close()

    if row is None:
        return None

    (
        _message_id,
        channel_id,
        gtype,
        host_id,
        amount,
        pet_name,
        click_value,
        auction_item,
        starting_bid,
        image_url,
        winners_count,
        duration_minutes,
        end_at_str,
        participants_json,
        bids_json,
    ) = row

    participants = set(json.loads(participants_json)) if participants_json else set()
    bids_payload = json.loads(bids_json) if bids_json else {}
    bids = {int(key): int(value) for key, value in bids_payload.items()}
    end_at = datetime.fromisoformat(end_at_str) if end_at_str else None

    return {
        "channel_id": channel_id,
        "type": gtype,
        "host_id": host_id,
        "amount": amount,
        "pet_name": pet_name,
        "click_value": click_value,
        "auction_item": auction_item,
        "starting_bid": starting_bid,
        "image_url": image_url,
        "winners_count": winners_count,
        "duration": duration_minutes,
        "end_at": end_at,
        "participants": participants,
        "bids": bids,
        "ended": False,
    }


def delete_giveaway_state(message_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM active_giveaways WHERE message_id = ?", (message_id,))
    conn.commit()
    conn.close()


# ---------- ATTENDANCE PANELY ----------


def save_attendance_panel(
    message_id: int,
    guild_id: int,
    channel_id: int,
    role_id: int,
    statuses: Dict[int, str],
):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO attendance_panels (message_id, guild_id, channel_id, role_id, statuses_json)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(message_id) DO UPDATE SET
            guild_id = excluded.guild_id,
            channel_id = excluded.channel_id,
            role_id = excluded.role_id,
            statuses_json = excluded.statuses_json
        """,
        (message_id, guild_id, channel_id, role_id, json.dumps(statuses)),
    )
    conn.commit()
    conn.close()


def delete_attendance_panel(message_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM attendance_panels WHERE message_id = ?", (message_id,))
    conn.commit()
    conn.close()


def load_attendance_panels() -> list[tuple[int, int, int, int, Dict[int, str]]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT message_id, guild_id, channel_id, role_id, statuses_json FROM attendance_panels"
    )
    rows = c.fetchall()
    conn.close()
    panels: list[tuple[int, int, int, int, Dict[int, str]]] = []
    for message_id, guild_id, channel_id, role_id, statuses_json in rows:
        try:
            statuses = json.loads(statuses_json) if statuses_json else {}
            panels.append(
                (
                    int(message_id),
                    int(guild_id),
                    int(channel_id),
                    int(role_id),
                    {int(uid): str(status) for uid, status in statuses.items()},
                )
            )
        except json.JSONDecodeError:
            continue

    return panels


# ---------- PROPHECY LOGS ----------


def log_prophecy(
    message_id: int,
    channel_id: int,
    author_id: int,
    question: str,
    answer: str,
    model: str,
    created_at: datetime,
):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO prophecy_logs (message_id, channel_id, author_id, question, answer, model, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(message_id) DO UPDATE SET
            channel_id = excluded.channel_id,
            author_id = excluded.author_id,
            question = excluded.question,
            answer = excluded.answer,
            model = excluded.model,
            created_at = excluded.created_at
        """,
        (
            message_id,
            channel_id,
            author_id,
            question,
            answer,
            model,
            created_at.isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def get_recent_prophecies(limit: int = 50) -> list[dict[str, object]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT message_id, channel_id, author_id, question, answer, model, created_at
        FROM prophecy_logs
        ORDER BY datetime(created_at) DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = c.fetchall()
    conn.close()

    results: list[dict[str, object]] = []
    for row in rows:
        (
            message_id,
            channel_id,
            author_id,
            question,
            answer,
            model,
            created_at,
        ) = row
        results.append(
            {
                "message_id": int(message_id),
                "channel_id": int(channel_id),
                "author_id": int(author_id),
                "question": str(question),
                "answer": str(answer),
                "model": str(model),
                "created_at": created_at,
            }
        )

    return results


# ---------- CLAN PANELY ----------

def add_clan_panel(guild_id: int, channel_id: int, message_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO clan_panels (message_id, guild_id, channel_id)
        VALUES (?, ?, ?)
        ON CONFLICT(message_id) DO UPDATE SET
            guild_id = excluded.guild_id,
            channel_id = excluded.channel_id
        """,
        (message_id, guild_id, channel_id),
    )
    conn.commit()
    conn.close()


def remove_clan_panel(message_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM clan_panels WHERE message_id = ?", (message_id,))
    conn.commit()
    conn.close()


def get_all_clan_panels() -> list[tuple[int, int, int]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT guild_id, channel_id, message_id FROM clan_panels")
    rows = c.fetchall()
    conn.close()
    return [(int(g), int(ch), int(msg)) for g, ch, msg in rows]


def set_clan_panel_config(guild_id: int, title: str, us_requirements: str, cz_requirements: str):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO clan_panel_configs (guild_id, title, us_requirements, cz_requirements)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            title = excluded.title,
            us_requirements = excluded.us_requirements,
            cz_requirements = excluded.cz_requirements
        """,
        (guild_id, title, us_requirements, cz_requirements),
    )
    conn.commit()
    conn.close()


def get_clan_panel_config(guild_id: int) -> tuple[str, str, str] | None:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT title, us_requirements, cz_requirements FROM clan_panel_configs WHERE guild_id = ?",
        (guild_id,),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    title, us_requirements, cz_requirements = row
    return str(title), str(us_requirements), str(cz_requirements)


# ---------- CLAN APPLICATION PANELY ----------

def add_clan_application_panel(guild_id: int, channel_id: int, message_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO clan_application_panels (message_id, guild_id, channel_id)
        VALUES (?, ?, ?)
        ON CONFLICT(message_id) DO UPDATE SET
            guild_id = excluded.guild_id,
            channel_id = excluded.channel_id
        """,
        (message_id, guild_id, channel_id),
    )
    conn.commit()
    conn.close()


def remove_clan_application_panel(message_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM clan_application_panels WHERE message_id = ?", (message_id,))
    conn.commit()
    conn.close()


def get_all_clan_application_panels() -> list[tuple[int, int, int]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT guild_id, channel_id, message_id FROM clan_application_panels"
    )
    rows = c.fetchall()
    conn.close()
    return [(int(g), int(ch), int(msg)) for g, ch, msg in rows]


# ---------- CLAN DEFINITIONS ----------


def upsert_clan_definition(
    guild_id: int,
    clan_key: str,
    display_name: str,
    description: str,
    accept_role_id: int | None,
    accept_role_id_cz: int | None,
    accept_role_id_en: int | None,
    accept_category_id: int | None,
    review_role_id: int | None,
    sort_order: int,
):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO clan_clans (
            guild_id,
            clan_key,
            display_name,
            description,
            accept_role_id,
            accept_role_id_cz,
            accept_role_id_en,
            accept_category_id,
            review_role_id,
            sort_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id, clan_key) DO UPDATE SET
            display_name = excluded.display_name,
            description = excluded.description,
            accept_role_id = excluded.accept_role_id,
            accept_role_id_cz = excluded.accept_role_id_cz,
            accept_role_id_en = excluded.accept_role_id_en,
            accept_category_id = excluded.accept_category_id,
            review_role_id = excluded.review_role_id,
            sort_order = excluded.sort_order
        """,
        (
            guild_id,
            clan_key,
            display_name,
            description,
            accept_role_id,
            accept_role_id_cz,
            accept_role_id_en,
            accept_category_id,
            review_role_id,
            sort_order,
        ),
    )
    conn.commit()
    conn.close()


def delete_clan_definition(guild_id: int, clan_key: str):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "DELETE FROM clan_clans WHERE guild_id = ? AND clan_key = ?",
        (guild_id, clan_key),
    )
    conn.commit()
    conn.close()


def get_clan_definition(guild_id: int, clan_key: str):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT clan_key, display_name, description, accept_role_id, accept_role_id_cz, accept_role_id_en, accept_category_id, review_role_id, sort_order
        FROM clan_clans
        WHERE guild_id = ? AND clan_key = ?
        """,
        (guild_id, clan_key),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    (
        key,
        name,
        description,
        accept_role_id,
        accept_role_id_cz,
        accept_role_id_en,
        accept_category_id,
        review_role_id,
        sort_order,
    ) = row
    return {
        "clan_key": str(key),
        "display_name": str(name),
        "description": str(description),
        "accept_role_id": int(accept_role_id) if accept_role_id is not None else None,
        "accept_role_id_cz": int(accept_role_id_cz) if accept_role_id_cz is not None else None,
        "accept_role_id_en": int(accept_role_id_en) if accept_role_id_en is not None else None,
        "accept_category_id": int(accept_category_id) if accept_category_id is not None else None,
        "review_role_id": int(review_role_id) if review_role_id is not None else None,
        "sort_order": int(sort_order) if sort_order is not None else 0,
    }


def list_clan_definitions(guild_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT clan_key, display_name, description, accept_role_id, accept_role_id_cz, accept_role_id_en, accept_category_id, review_role_id, sort_order
        FROM clan_clans
        WHERE guild_id = ?
        ORDER BY sort_order ASC, clan_key COLLATE NOCASE
        """,
        (guild_id,),
    )
    rows = c.fetchall()
    conn.close()
    results = []
    for (
        key,
        name,
        description,
        accept_role_id,
        accept_role_id_cz,
        accept_role_id_en,
        accept_category_id,
        review_role_id,
        sort_order,
    ) in rows:
        results.append(
            {
                "clan_key": str(key),
                "display_name": str(name),
                "description": str(description),
                "accept_role_id": int(accept_role_id) if accept_role_id is not None else None,
                "accept_role_id_cz": int(accept_role_id_cz) if accept_role_id_cz is not None else None,
                "accept_role_id_en": int(accept_role_id_en) if accept_role_id_en is not None else None,
                "accept_category_id": int(accept_category_id) if accept_category_id is not None else None,
                "review_role_id": int(review_role_id) if review_role_id is not None else None,
                "sort_order": int(sort_order) if sort_order is not None else 0,
            }
        )
    return results


def get_next_clan_sort_order(guild_id: int) -> int:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT COALESCE(MAX(sort_order), -1) FROM clan_clans WHERE guild_id = ?",
        (guild_id,),
    )
    row = c.fetchone()
    conn.close()
    current_max = int(row[0]) if row and row[0] is not None else -1
    return current_max + 1


# ---------- LEADERBOARD PANELY ----------

def add_leaderboard_panel(guild_id: int, channel_id: int, message_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO leaderboard_panels (message_id, guild_id, channel_id)
        VALUES (?, ?, ?)
        ON CONFLICT(message_id) DO UPDATE SET
            guild_id = excluded.guild_id,
            channel_id = excluded.channel_id
        """,
        (message_id, guild_id, channel_id),
    )
    conn.commit()
    conn.close()


def remove_leaderboard_panel(message_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM leaderboard_panels WHERE message_id = ?", (message_id,))
    conn.commit()
    conn.close()


def get_all_leaderboard_panels() -> list[tuple[int, int, int]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT guild_id, channel_id, message_id FROM leaderboard_panels")
    rows = c.fetchall()
    conn.close()
    return [(int(g), int(ch), int(msg)) for g, ch, msg in rows]


# ---------- DROPSTATS PANELY ----------

def add_dropstats_panel(guild_id: int, channel_id: int, message_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO dropstats_panels (message_id, guild_id, channel_id)
        VALUES (?, ?, ?)
        ON CONFLICT(message_id) DO UPDATE SET
            guild_id = excluded.guild_id,
            channel_id = excluded.channel_id
        """,
        (message_id, guild_id, channel_id),
    )
    conn.commit()
    conn.close()


def remove_dropstats_panel(message_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM dropstats_panels WHERE message_id = ?", (message_id,))
    conn.commit()
    conn.close()


def get_all_dropstats_panels() -> list[tuple[int, int, int]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT guild_id, channel_id, message_id FROM dropstats_panels")
    rows = c.fetchall()
    conn.close()
    return [(int(g), int(ch), int(msg)) for g, ch, msg in rows]


# ---------- SP PANELY ----------


def add_sp_panel(guild_id: int, channel_id: int, message_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO sp_panels (message_id, guild_id, channel_id)
        VALUES (?, ?, ?)
        ON CONFLICT(message_id) DO UPDATE SET
            guild_id = excluded.guild_id,
            channel_id = excluded.channel_id
        """,
        (message_id, guild_id, channel_id),
    )
    conn.commit()
    conn.close()


def remove_sp_panel(message_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM sp_panels WHERE message_id = ?", (message_id,))
    conn.commit()
    conn.close()


def get_all_sp_panels() -> list[tuple[int, int, int]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT guild_id, channel_id, message_id FROM sp_panels")
    rows = c.fetchall()
    conn.close()
    return [(int(g), int(ch), int(msg)) for g, ch, msg in rows]


def get_sp_panel_for_guild(guild_id: int) -> Optional[tuple[int, int, int]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT guild_id, channel_id, message_id FROM sp_panels WHERE guild_id = ?",
        (guild_id,),
    )
    row = c.fetchone()
    conn.close()
    if row is None:
        return None
    guild_id_val, channel_id, message_id = row
    return (int(guild_id_val), int(channel_id), int(message_id))


# ---------- DŘEVO ----------

def get_or_create_resource(name: str) -> int:
    norm_name = name.strip()
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO resources (name) VALUES (?) ON CONFLICT(name) DO NOTHING",
        (norm_name,),
    )
    conn.commit()
    c.execute("SELECT id FROM resources WHERE name = ?", (norm_name,))
    row = c.fetchone()
    conn.close()
    if not row:
        raise RuntimeError("Nepodařilo se vytvořit resource.")
    return int(row[0])


def set_resource_need(resource_name: str, required_amount: int):
    rid = get_or_create_resource(resource_name)
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO resource_targets (resource_id, required_amount)
        VALUES (?, ?)
        ON CONFLICT(resource_id) DO UPDATE SET required_amount = excluded.required_amount
        """,
        (rid, required_amount),
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
        rid = get_or_create_resource(resource_name)
        c.execute("DELETE FROM resource_targets WHERE resource_id = ?", (rid,))
        c.execute("DELETE FROM resource_deliveries WHERE resource_id = ?", (rid,))
    conn.commit()
    conn.close()


def add_delivery(discord_id: int, resource_name: str, amount: int):
    rid = get_or_create_resource(resource_name)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO resource_deliveries (discord_id, resource_id, amount, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (discord_id, rid, amount, now_str),
    )
    conn.commit()
    conn.close()


def get_resources_status() -> List[Tuple[str, int, int]]:
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
    return [(str(r[0]), int(r[1]), int(r[2])) for r in rows]


def get_inactive_users(threshold_hours: int = INACTIVE_THRESHOLD_HOURS) -> List[int]:
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

    now = datetime.now()
    result: List[int] = []
    for discord_id, last_ts in rows:
        try:
            last_dt = datetime.strptime(last_ts, "%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            continue
        if now - last_dt >= timedelta(hours=threshold_hours):
            result.append(int(discord_id))
    return result


# ---------- TIMERY ----------

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
    return int(row[0])


def get_all_timers() -> List[Tuple[int, str, int]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, name, duration_minutes FROM timers ORDER BY name")
    rows = c.fetchall()
    conn.close()
    return [(int(r[0]), str(r[1]), int(r[2])) for r in rows]


def delete_timer(name: str) -> bool:
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM timers WHERE name = ?", (name,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return deleted > 0


def upsert_active_timer(user_id: int, timer_name: str, minutes: int, end_at: datetime):
    conn = get_connection()
    c = conn.cursor()
    end_str = end_at.strftime("%Y-%m-%d %H:%M:%S")
    c.execute(
        """
        INSERT INTO active_timers (user_id, timer_name, duration_minutes, end_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, timer_name)
        DO UPDATE SET duration_minutes = excluded.duration_minutes,
                      end_at = excluded.end_at
        """,
        (user_id, timer_name, minutes, end_str),
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
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT user_id, timer_name, duration_minutes, end_at FROM active_timers")
    rows = c.fetchall()
    conn.close()
    return [(int(r[0]), str(r[1]), int(r[2]), str(r[3])) for r in rows]


# ---------- USER STATS (XP/COINS/LEVEL/MESSAGES) ----------


def ensure_user_stats_columns():
    conn = get_connection()
    c = conn.cursor()
    c.execute("PRAGMA table_info(user_stats)")
    columns = {row[1] for row in c.fetchall()}
    if "message_count" not in columns:
        c.execute(
            "ALTER TABLE user_stats ADD COLUMN message_count INTEGER NOT NULL DEFAULT 0"
        )
        conn.commit()
    conn.close()

def get_or_create_user_stats(discord_id: int) -> Tuple[int, int, int, Optional[str], int]:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT coins, exp, level, last_xp_at, message_count FROM user_stats WHERE discord_id = ?",
        (discord_id,),
    )
    row = c.fetchone()
    if row is None:
        c.execute(
            """
            INSERT INTO user_stats (discord_id, coins, exp, level, last_xp_at, message_count)
            VALUES (?, 0, 0, 1, NULL, 0)
            """,
            (discord_id,),
        )
        conn.commit()
        conn.close()
        return 0, 0, 1, None, 0
    conn.close()
    return int(row[0]), int(row[1]), int(row[2]), row[3], int(row[4])


def update_user_stats(
    discord_id: int,
    coins: Optional[int] = None,
    exp: Optional[int] = None,
    level: Optional[int] = None,
    last_xp_at: Optional[Optional[str]] = None,
    message_count: Optional[int] = None,
):
    conn = get_connection()
    c = conn.cursor()
    parts: List[str] = []
    params: List[Any] = []

    if coins is not None:
        parts.append("coins = ?")
        params.append(coins)
    if exp is not None:
        parts.append("exp = ?")
        params.append(exp)
    if level is not None:
        parts.append("level = ?")
        params.append(level)
    if last_xp_at is not None:
        parts.append("last_xp_at = ?")
        params.append(last_xp_at)
    if message_count is not None:
        parts.append("message_count = ?")
        params.append(message_count)

    if not parts:
        conn.close()
        return

    sql = f"UPDATE user_stats SET {', '.join(parts)} WHERE discord_id = ?"
    params.append(discord_id)
    c.execute(sql, tuple(params))
    conn.commit()
    conn.close()


def get_top_users_by_stat(stat: str, limit: int = 10) -> List[Tuple[int, int]]:
    allowed = {"coins", "message_count"}
    if stat not in allowed:
        raise ValueError(f"Nepodporovaný sloupec: {stat}")

    conn = get_connection()
    c = conn.cursor()
    c.execute(
        f"SELECT discord_id, {stat} FROM user_stats ORDER BY {stat} DESC LIMIT ?",
        (limit,),
    )
    rows = c.fetchall()
    conn.close()
    return [(int(r[0]), int(r[1])) for r in rows]


# ---------- SHOP ----------

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


def decrement_shop_item_stock(item_id: int, amount: int = 1) -> Tuple[bool, int]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT stock, is_active FROM shop_items WHERE id = ?", (item_id,))
    row = c.fetchone()
    if row is None:
        conn.close()
        return False, 0

    stock = int(row[0])
    is_active = int(row[1])
    if amount <= 0:
        conn.close()
        return False, stock

    if is_active == 0 or stock <= 0 or stock < amount:
        conn.close()
        return False, max(stock, 0)

    new_stock = stock - amount
    new_active = 1 if new_stock > 0 else 0
    c.execute(
        "UPDATE shop_items SET stock = ?, is_active = ? WHERE id = ?",
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


def create_shop_purchase(
    item_id: int, buyer_id: int, seller_id: int, price_coins: int, quantity: int = 1
) -> int:
    now_str = datetime.utcnow().isoformat()
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO shop_purchases (item_id, buyer_id, seller_id, price_coins, created_at, quantity)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (item_id, buyer_id, seller_id, price_coins, now_str, quantity),
    )
    conn.commit()
    purchase_id = c.lastrowid
    conn.close()
    return int(purchase_id)


def get_pending_shop_purchases_grouped() -> List[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT buyer_id, SUM(quantity) AS cnt
        FROM shop_purchases
        WHERE completed = 0
        GROUP BY buyer_id
        ORDER BY cnt DESC
        """
    )
    rows = c.fetchall()
    conn.close()
    return [{"buyer_id": int(r[0]), "count": int(r[1])} for r in rows]


def complete_shop_purchase(purchase_id: int) -> bool:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "UPDATE shop_purchases SET completed = 1 WHERE id = ? AND completed = 0",
        (purchase_id,),
    )
    conn.commit()
    rowcount = c.rowcount
    conn.close()
    return rowcount > 0


def complete_shop_purchases_for_user(buyer_id: int) -> int:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "UPDATE shop_purchases SET completed = 1 WHERE buyer_id = ? AND completed = 0",
        (buyer_id,),
    )
    conn.commit()
    rowcount = c.rowcount
    conn.close()
    return int(rowcount)


def get_pending_shop_sales_for_seller(seller_id: int) -> List[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT sp.id, sp.item_id, sp.buyer_id, sp.price_coins, sp.created_at, si.title, sp.quantity
        FROM shop_purchases sp
        JOIN shop_items si ON sp.item_id = si.id
        WHERE sp.seller_id = ? AND sp.completed = 0
        ORDER BY sp.created_at ASC
        """,
        (seller_id,),
    )
    rows = c.fetchall()
    conn.close()
    return [
        {
            "id": int(r[0]),
            "item_id": int(r[1]),
            "buyer_id": int(r[2]),
            "price_coins": int(r[3]),
            "created_at": r[4],
            "title": r[5],
            "quantity": int(r[6]),
        }
        for r in rows
    ]


# ---------- CLAN APPLICATIONS ----------

def _row_to_clan_application(row) -> Dict[str, Any]:
    return {
        "id": int(row[0]),
        "guild_id": int(row[1]),
        "channel_id": int(row[2]),
        "user_id": int(row[3]),
        "roblox_nick": row[4],
        # text, žádné int() – může být '24hodin', '2–3', 'cca 1500', ...
        "hours_per_day": row[5],
        "rebirths": row[6],
        "locale": row[7],
        "status": row[8],
        "created_at": row[9],
        "decided_at": row[10],
        "last_message_at": row[11],
        "last_message_by_bot": int(row[12]),
        "last_ping_at": row[13],
        "deleted": int(row[14]),
    }


def create_clan_application(
    guild_id: int, channel_id: int, user_id: int, locale: str
) -> int:
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO clan_applications (
            guild_id, channel_id, user_id,
            roblox_nick, hours_per_day, rebirths,
            locale, status, created_at, decided_at,
            last_message_at, last_message_by_bot, last_ping_at, deleted
        )
        VALUES (?, ?, ?, NULL, NULL, NULL, ?, 'open', ?, NULL, ?, 0, NULL, 0)
        """,
        (guild_id, channel_id, user_id, locale, now_str, now_str),
    )
    conn.commit()
    app_id = c.lastrowid
    conn.close()
    return int(app_id)


def get_open_application_by_user(guild_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT id, guild_id, channel_id, user_id,
               roblox_nick, hours_per_day, rebirths, locale,
               status, created_at, decided_at, last_message_at,
               last_message_by_bot, last_ping_at, deleted
        FROM clan_applications
        WHERE guild_id = ? AND user_id = ? AND status = 'open' AND deleted = 0
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (guild_id, user_id),
    )
    row = c.fetchone()
    conn.close()
    if row is None:
        return None
    return _row_to_clan_application(row)


def get_latest_clan_application_by_user(
    guild_id: int, user_id: int
) -> Optional[Dict[str, Any]]:
    """
    Vrátí nejnovější (nevypnutou) přihlášku uživatele bez ohledu na stav.
    Používá se při přemapování ticketů pro stávající členy.
    """

    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT id, guild_id, channel_id, user_id,
               roblox_nick, hours_per_day, rebirths, locale,
               status, created_at, decided_at, last_message_at,
               last_message_by_bot, last_ping_at, deleted
        FROM clan_applications
        WHERE guild_id = ? AND user_id = ? AND deleted = 0
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (guild_id, user_id),
    )
    row = c.fetchone()
    conn.close()
    if row is None:
        return None
    return _row_to_clan_application(row)


def get_clan_applications_by_user(
    guild_id: int, user_id: int, include_deleted: bool = False
) -> list[Dict[str, Any]]:
    """Vrátí všechny přihlášky uživatele seřazené od nejnovější."""

    conn = get_connection()
    c = conn.cursor()
    query = """
        SELECT id, guild_id, channel_id, user_id,
               roblox_nick, hours_per_day, rebirths, locale,
               status, created_at, decided_at, last_message_at,
               last_message_by_bot, last_ping_at, deleted
        FROM clan_applications
        WHERE guild_id = ? AND user_id = ?
    """
    params: list[Any] = [guild_id, user_id]
    if not include_deleted:
        query += " AND deleted = 0"

    query += " ORDER BY created_at DESC"

    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    return [_row_to_clan_application(row) for row in rows]


def get_clan_application_by_channel(
    guild_id: int, channel_id: int
) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT id, guild_id, channel_id, user_id,
               roblox_nick, hours_per_day, rebirths, locale,
               status, created_at, decided_at, last_message_at,
               last_message_by_bot, last_ping_at, deleted
        FROM clan_applications
        WHERE guild_id = ? AND channel_id = ? AND deleted = 0
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (guild_id, channel_id),
    )
    row = c.fetchone()
    conn.close()
    if row is None:
        return None
    return _row_to_clan_application(row)


def list_open_clan_applications(guild_id: int) -> list[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT id, guild_id, channel_id, user_id,
               roblox_nick, hours_per_day, rebirths, locale,
               status, created_at, decided_at, last_message_at,
               last_message_by_bot, last_ping_at, deleted
        FROM clan_applications
        WHERE guild_id = ? AND status = 'open' AND deleted = 0
        ORDER BY created_at DESC
        """,
        (guild_id,),
    )
    rows = c.fetchall()
    conn.close()
    return [_row_to_clan_application(row) for row in rows]


def get_open_application_by_channel(channel_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT id, guild_id, channel_id, user_id,
               roblox_nick, hours_per_day, rebirths, locale,
               status, created_at, decided_at, last_message_at,
               last_message_by_bot, last_ping_at, deleted
        FROM clan_applications
        WHERE channel_id = ? AND status = 'open' AND deleted = 0
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (channel_id,),
    )
    row = c.fetchone()
    conn.close()
    if row is None:
        return None
    return _row_to_clan_application(row)


def update_clan_application_form(
    app_id: int,
    roblox_nick: str,
    hours_per_day: str,
    rebirths: str,
):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        UPDATE clan_applications
        SET roblox_nick = ?, hours_per_day = ?, rebirths = ?
        WHERE id = ?
        """,
        (roblox_nick, hours_per_day, rebirths, app_id),
    )
    conn.commit()
    conn.close()


def set_clan_application_status(app_id: int, status: str, decided_at: Optional[datetime] = None):
    if decided_at is None:
        decided_at = datetime.utcnow()
    decided_str = decided_at.strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        UPDATE clan_applications
        SET status = ?, decided_at = ?
        WHERE id = ?
        """,
        (status, decided_str, app_id),
    )
    conn.commit()
    conn.close()


def update_clan_application_last_message(
    app_id: int,
    message_at: Optional[datetime] = None,
    by_bot: Optional[bool] = None,
):
    if message_at is None:
        message_at = datetime.utcnow()
    message_str = message_at.strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    c = conn.cursor()
    if by_bot is None:
        c.execute(
            """
            UPDATE clan_applications
            SET last_message_at = ?
            WHERE id = ?
            """,
            (message_str, app_id),
        )
    else:
        c.execute(
            """
            UPDATE clan_applications
            SET last_message_at = ?, last_message_by_bot = ?
            WHERE id = ?
            """,
            (message_str, int(by_bot), app_id),
        )
    conn.commit()
    conn.close()


def update_clan_application_last_ping(app_id: int, pinged_at: Optional[datetime] = None):
    if pinged_at is None:
        pinged_at = datetime.utcnow()
    pinged_str = pinged_at.strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        UPDATE clan_applications
        SET last_ping_at = ?
        WHERE id = ?
        """,
        (pinged_str, app_id),
    )
    conn.commit()
    conn.close()


def get_clan_applications_for_cleanup(
    age_minutes: int = CLAN_TICKET_CLEANUP_MINUTES,
) -> List[Dict[str, Any]]:
    """
    Vrátí přihlášky, které jsou accepted/rejected, nejsou smazané (deleted=0)
    a decided_at je starší než age_minutes.
    """
    cutoff = datetime.utcnow() - timedelta(minutes=age_minutes)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT id, guild_id, channel_id, user_id,
               roblox_nick, hours_per_day, rebirths, locale,
               status, created_at, decided_at, last_message_at,
               last_message_by_bot, last_ping_at, deleted
        FROM clan_applications
        WHERE deleted = 0
          AND status IN ('accepted', 'rejected')
          AND decided_at IS NOT NULL
          AND decided_at <= ?
        """,
        (cutoff_str,),
    )
    rows = c.fetchall()
    conn.close()
    return [_row_to_clan_application(r) for r in rows]


def mark_clan_application_deleted(app_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "UPDATE clan_applications SET deleted = 1 WHERE id = ?",
        (app_id,),
    )
    conn.commit()
    conn.close()


def save_clan_ticket_vacation(
    guild_id: int,
    channel_id: int,
    user_id: int,
    clan_key: str | None,
    prev_category_id: int | None,
    removed_role_ids: list[int],
    vacation_role_id: int,
    moved_at: Optional[datetime] = None,
):
    moved_at = moved_at or datetime.utcnow()
    moved_at_str = moved_at.strftime("%Y-%m-%d %H:%M:%S")
    removed_role_ids_json = json.dumps(removed_role_ids)
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO clan_ticket_vacations (
            channel_id, guild_id, user_id, clan_key, prev_category_id,
            removed_role_ids_json, vacation_role_id, moved_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            guild_id = excluded.guild_id,
            user_id = excluded.user_id,
            clan_key = excluded.clan_key,
            prev_category_id = excluded.prev_category_id,
            removed_role_ids_json = excluded.removed_role_ids_json,
            vacation_role_id = excluded.vacation_role_id,
            moved_at = excluded.moved_at
        """,
        (
            channel_id,
            guild_id,
            user_id,
            clan_key,
            prev_category_id,
            removed_role_ids_json,
            vacation_role_id,
            moved_at_str,
        ),
    )
    conn.commit()
    conn.close()


def get_clan_ticket_vacation(channel_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT channel_id, guild_id, user_id, clan_key, prev_category_id,
               removed_role_ids_json, vacation_role_id, moved_at
        FROM clan_ticket_vacations
        WHERE channel_id = ?
        """,
        (channel_id,),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    removed_role_ids_json = row[5] or "[]"
    try:
        removed_role_ids = json.loads(removed_role_ids_json)
    except json.JSONDecodeError:
        removed_role_ids = []
    return {
        "channel_id": row[0],
        "guild_id": row[1],
        "user_id": row[2],
        "clan_key": row[3],
        "prev_category_id": row[4],
        "removed_role_ids": removed_role_ids,
        "vacation_role_id": row[6],
        "moved_at": row[7],
    }


def delete_clan_ticket_vacation(channel_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM clan_ticket_vacations WHERE channel_id = ?", (channel_id,))
    conn.commit()
    conn.close()


def enqueue_discord_write(operation: str, payload: Dict[str, Any]) -> int:
    conn = get_connection()
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    payload_json = json.dumps(payload, ensure_ascii=False)
    c.execute(
        """
        INSERT INTO discord_write_queue (operation, payload, status, created_at, updated_at)
        VALUES (?, ?, 'pending', ?, ?)
        """,
        (operation, payload_json, now, now),
    )
    conn.commit()
    row_id = c.lastrowid
    conn.close()
    return int(row_id)


def fetch_pending_discord_writes(limit: int = 100) -> List[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT id, operation, payload
        FROM discord_write_queue
        WHERE status = 'pending'
        ORDER BY id ASC
        LIMIT ?
        """,
        (limit,),
    )
    rows = c.fetchall()
    conn.close()
    return [
        {"id": row[0], "operation": row[1], "payload": row[2]} for row in rows
    ]


def mark_discord_write_done(write_id: int):
    conn = get_connection()
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    c.execute(
        """
        UPDATE discord_write_queue
        SET status = 'done', updated_at = ?, last_error = NULL
        WHERE id = ?
        """,
        (now, write_id),
    )
    conn.commit()
    conn.close()


def mark_discord_write_failed(write_id: int, error: str):
    conn = get_connection()
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    c.execute(
        """
        UPDATE discord_write_queue
        SET status = 'failed', updated_at = ?, last_error = ?
        WHERE id = ?
        """,
        (now, error, write_id),
    )
    conn.commit()
    conn.close()


def clear_pending_discord_writes() -> int:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM discord_write_queue WHERE status = 'pending'")
    count = c.fetchone()[0]
    c.execute("DELETE FROM discord_write_queue WHERE status = 'pending'")
    conn.commit()
    conn.close()
    return int(count)


def add_windows_notification(payload: Dict[str, Any]) -> None:
    conn = get_connection()
    c = conn.cursor()
    created_at = datetime.utcnow().isoformat()
    payload_json = json.dumps(payload, ensure_ascii=False)
    c.execute(
        """
        INSERT INTO windows_notifications (payload, created_at)
        VALUES (?, ?)
        """,
        (payload_json, created_at),
    )
    conn.commit()
    conn.close()


def get_windows_notifications(limit: int = 50) -> List[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT id, payload, created_at
        FROM windows_notifications
        ORDER BY id ASC
        LIMIT ?
        """,
        (limit,),
    )
    rows = c.fetchall()
    conn.close()
    notifications: List[Dict[str, Any]] = []
    for row in rows:
        payload_raw = row[1]
        try:
            payload = json.loads(payload_raw) if payload_raw else {}
        except json.JSONDecodeError:
            payload = {"raw_payload": payload_raw}
        notifications.append(
            {"id": int(row[0]), "payload": payload, "created_at": row[2]}
        )
    return notifications


def delete_windows_notifications(notification_ids: List[int]) -> None:
    if not notification_ids:
        return
    conn = get_connection()
    c = conn.cursor()
    placeholders = ",".join("?" for _ in notification_ids)
    c.execute(
        f"DELETE FROM windows_notifications WHERE id IN ({placeholders})",
        tuple(notification_ids),
    )
    conn.commit()
    conn.close()
