import asyncio
import contextlib
import json
import itertools
import logging
import os
import re
import string
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import discord
from discord.ext import commands

from config import DISCORD_WRITE_MIN_INTERVAL_SECONDS

# Poznámka: Patchování write metod je verzí podmíněné (discord.py 2.x),
# protože některé API metody se v minor verzích mohou lišit nebo chybět.
# Pokud metoda neexistuje, patch se přeskočí a loguje.
# Smoke test (manuální): spusť bot a ověř v logu "Patched ... methods"
# a "DiscordWriteCoordinator cog není načten" se neobjeví, běžné send
# požadavky musí projít přes queue (grep: "Discord write selhal").
from db import (
    clan_member_nick_exists,
    delete_discord_rate_limit_bucket,
    fetch_discord_rate_limit_buckets,
    enqueue_discord_write,
    prune_discord_rate_limit_buckets,
    fetch_pending_discord_writes,
    mark_discord_write_done,
    mark_discord_write_failed,
    mark_discord_write_retry,
    normalize_clan_member_name,
    upsert_discord_rate_limit_bucket,
)

@dataclass
class WriteRequest:
    operation: str
    payload: dict[str, Any]
    persist: bool
    future: asyncio.Future | None
    db_id: int | None
    priority: int
    attempts: int = 0
    next_retry_at: datetime | None = None
    bucket_key: str | None = None


class WritePriority:
    URGENT = 0
    NORMAL = 10

    @classmethod
    def normalize(cls, value: str | int | None) -> int:
        if value is None:
            return cls.NORMAL
        if isinstance(value, int):
            return value
        normalized = str(value).strip().lower()
        if normalized == "urgent":
            return cls.URGENT
        if normalized == "normal":
            return cls.NORMAL
        return cls.NORMAL


def get_writer(bot: commands.Bot) -> "DiscordWriteCoordinatorCog":
    writer = bot.get_cog("DiscordWriteCoordinator")
    if not isinstance(writer, DiscordWriteCoordinatorCog):
        raise RuntimeError("DiscordWriteCoordinator cog není načten.")
    return writer


def _get_client_from_state(obj: Any) -> commands.Bot:
    state = getattr(obj, "_state", None)
    if state is None:
        raise RuntimeError("Nelze získat state z objektu pro Discord writer.")
    client = state._get_client()  # type: ignore[attr-defined]
    if client is None:
        raise RuntimeError("Nelze získat klienta z Discord state.")
    return client


def _sanitize_components_v2(components: Any, max_total: int = 4000, max_per: int = 4000):
    try:
        content_nodes: list[dict[str, Any]] = []

        def sanitize_node(node: Any) -> None:
            if node is None:
                return
            if isinstance(node, (list, tuple)):
                for child in node:
                    sanitize_node(child)
                return
            if isinstance(node, dict):
                if "content" in node:
                    value = node.get("content")
                    if value is None:
                        text = ""
                    elif isinstance(value, str):
                        text = value
                    else:
                        try:
                            text = str(value)
                        except Exception:  # noqa: BLE001
                            text = ""
                    if text.strip() == "":
                        text = "\u200b"
                    if len(text) > max_per:
                        suffix = "… (zkráceno)"
                        if len(suffix) >= max_per:
                            text = text[:max_per]
                        else:
                            text = f"{text[:max_per - len(suffix)]}{suffix}"
                    node["content"] = text
                    content_nodes.append(node)
                nested = node.get("components")
                if nested is not None:
                    sanitize_node(nested)
                return

        sanitize_node(components)

        total_length = sum(len(node.get("content", "")) for node in content_nodes)
        if total_length > max_total:
            remaining = total_length - max_total
            for node in reversed(content_nodes):
                if remaining <= 0:
                    break
                content = node.get("content", "")
                if not isinstance(content, str):
                    try:
                        content = str(content)
                    except Exception:  # noqa: BLE001
                        content = ""
                if content.strip() == "":
                    content = "\u200b"
                current_len = len(content)
                if current_len <= 1:
                    node["content"] = content if content.strip() != "" else "\u200b"
                    continue
                reducible = current_len - 1
                cut = min(reducible, remaining)
                new_len = current_len - cut
                trimmed = content[:new_len]
                if trimmed.strip() == "":
                    trimmed = "\u200b"
                node["content"] = trimmed
                remaining -= cut
    except Exception:  # noqa: BLE001
        return components
    return components


def _find_prefix_and_nick_in_text(value: Any) -> tuple[bool, str | None]:
    if value is None:
        return False, None
    if isinstance(value, str):
        text = value
    else:
        try:
            text = str(value)
        except Exception:  # noqa: BLE001
            return False, None
    for token in re.split(r"\s+", text):
        candidate = token.strip(string.punctuation)
        normalized = normalize_clan_member_name(candidate)
        if normalized and clan_member_nick_exists(normalized):
            return True, normalized
    return False, None


def _find_prefix_and_nick_in_components(components: Any) -> bool:
    found = False

    def search_node(node: Any) -> None:
        nonlocal found
        if found or node is None:
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                search_node(child)
                if found:
                    return
            return
        if isinstance(node, dict):
            if "content" in node:
                matched, _nick = _find_prefix_and_nick_in_text(node.get("content"))
                if matched:
                    found = True
                    return
            nested = node.get("components")
            if nested is not None:
                search_node(nested)
            return

    try:
        search_node(components)
    except Exception:  # noqa: BLE001
        return False
    return found


async def _patched_messageable_send(target: discord.abc.Messageable, *args, **kwargs):
    try:
        writer = get_writer(_get_client_from_state(target))
        return await writer.send_message(target, *args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("botdc.discord_write").warning(
            "Fallback na originální send (Messageable) pro %s.", type(target).__name__, exc_info=exc
        )
        original = getattr(type(target), "__discord_write_original_send__", None)
        if original is None:
            raise
        return await original(target, *args, **kwargs)


async def _patched_message_edit(message: discord.Message, *args, **kwargs):
    try:
        writer = get_writer(_get_client_from_state(message))
        return await writer.edit_message(message, *args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("botdc.discord_write").warning(
            "Fallback na originální edit pro Message.", exc_info=exc
        )
        original = getattr(type(message), "__discord_write_original_edit__", None)
        if original is None:
            raise
        return await original(message, *args, **kwargs)


async def _patched_message_delete(message: discord.Message, *args, **kwargs):
    try:
        writer = get_writer(_get_client_from_state(message))
        return await writer.delete_message(message, *args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("botdc.discord_write").warning(
            "Fallback na originální delete pro Message.", exc_info=exc
        )
        original = getattr(type(message), "__discord_write_original_delete__", None)
        if original is None:
            raise
        return await original(message, *args, **kwargs)


async def _patched_channel_edit(channel: discord.abc.GuildChannel, *args, **kwargs):
    try:
        writer = get_writer(_get_client_from_state(channel))
        return await writer.edit_channel(channel, *args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("botdc.discord_write").warning(
            "Fallback na originální edit pro kanál %s.", type(channel).__name__, exc_info=exc
        )
        original = getattr(type(channel), "__discord_write_original_edit__", None)
        if original is None:
            raise
        return await original(channel, *args, **kwargs)


async def _patched_channel_delete(channel: discord.abc.GuildChannel, *args, **kwargs):
    try:
        writer = get_writer(_get_client_from_state(channel))
        return await writer.delete_channel(channel, *args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("botdc.discord_write").warning(
            "Fallback na originální delete pro kanál %s.", type(channel).__name__, exc_info=exc
        )
        original = getattr(type(channel), "__discord_write_original_delete__", None)
        if original is None:
            raise
        return await original(channel, *args, **kwargs)


async def _patched_channel_set_permissions(
    channel: discord.abc.GuildChannel, target: discord.abc.Snowflake, **kwargs
):
    try:
        writer = get_writer(_get_client_from_state(channel))
        return await writer.set_permissions(channel, target, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("botdc.discord_write").warning(
            "Fallback na originální set_permissions pro kanál %s.",
            type(channel).__name__,
            exc_info=exc,
        )
        original = getattr(type(channel), "__discord_write_original_set_permissions__", None)
        if original is None:
            raise
        return await original(channel, target, **kwargs)


async def _patched_create_text_channel(guild: discord.Guild, *args, **kwargs):
    try:
        writer = get_writer(_get_client_from_state(guild))
        return await writer.create_text_channel(guild, *args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("botdc.discord_write").warning(
            "Fallback na originální create_text_channel pro Guild.", exc_info=exc
        )
        original = getattr(
            discord.Guild, "__discord_write_original_create_text_channel__", None
        )
        if original is None:
            raise
        return await original(guild, *args, **kwargs)


async def _patched_member_add_roles(member: discord.Member, *roles, **kwargs):
    try:
        writer = get_writer(_get_client_from_state(member))
        return await writer.add_roles(member, *roles, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("botdc.discord_write").warning(
            "Fallback na originální add_roles pro Member.", exc_info=exc
        )
        original = getattr(discord.Member, "__discord_write_original_add_roles__", None)
        if original is None:
            raise
        return await original(member, *roles, **kwargs)


async def _patched_member_remove_roles(member: discord.Member, *roles, **kwargs):
    try:
        writer = get_writer(_get_client_from_state(member))
        return await writer.remove_roles(member, *roles, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("botdc.discord_write").warning(
            "Fallback na originální remove_roles pro Member.", exc_info=exc
        )
        original = getattr(discord.Member, "__discord_write_original_remove_roles__", None)
        if original is None:
            raise
        return await original(member, *roles, **kwargs)


async def _patched_guild_ban(guild: discord.Guild, user: discord.abc.User, **kwargs):
    try:
        writer = get_writer(_get_client_from_state(guild))
        return await writer.ban_member(guild, user, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("botdc.discord_write").warning(
            "Fallback na originální ban pro Guild.", exc_info=exc
        )
        original = getattr(discord.Guild, "__discord_write_original_ban__", None)
        if original is None:
            raise
        return await original(guild, user, **kwargs)


async def _patched_member_kick(member: discord.Member, **kwargs):
    try:
        writer = get_writer(_get_client_from_state(member))
        return await writer.kick_member(member, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("botdc.discord_write").warning(
            "Fallback na originální kick pro Member.", exc_info=exc
        )
        original = getattr(discord.Member, "__discord_write_original_kick__", None)
        if original is None:
            raise
        return await original(member, **kwargs)


async def _patched_member_timeout(member: discord.Member, until, **kwargs):
    try:
        writer = get_writer(_get_client_from_state(member))
        return await writer.timeout_member(member, until, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("botdc.discord_write").warning(
            "Fallback na originální timeout pro Member.", exc_info=exc
        )
        original = getattr(discord.Member, "__discord_write_original_timeout__", None)
        if original is None:
            raise
        return await original(member, until, **kwargs)


async def _patched_member_edit(member: discord.Member, **kwargs):
    try:
        writer = get_writer(_get_client_from_state(member))
        return await writer.edit_member(member, **kwargs)
    except discord.Forbidden as exc:
        if getattr(exc, "code", None) == 50013:
            logging.getLogger("botdc.discord_write").warning(
                "Nelze editovat člena (Missing Permissions).",
                exc_info=exc,
            )
            return None
        raise
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("botdc.discord_write").warning(
            "Fallback na originální edit pro Member.", exc_info=exc
        )
        original = getattr(discord.Member, "__discord_write_original_edit__", None)
        if original is None:
            raise
        return await original(member, **kwargs)


async def _patched_interaction_send(response: discord.InteractionResponse, *args, **kwargs):
    interaction = getattr(response, "_parent", None)
    try:
        if interaction is None or interaction.client is None:
            raise RuntimeError("Interakce nemá klienta.")
        writer = get_writer(interaction.client)
        return await writer.send_interaction_response(interaction, *args, **kwargs)
    except discord.NotFound as exc:
        if getattr(exc, "code", None) == 10062:
            logging.getLogger("botdc.discord_write").info(
                "Interaction vypršela (Unknown interaction) při send_message."
            )
            return None
        raise
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("botdc.discord_write").warning(
            "Fallback na originální send_message pro InteractionResponse.", exc_info=exc
        )
        original = getattr(
            discord.InteractionResponse, "__discord_write_original_send_message__", None
        )
        if original is None:
            raise
        return await original(response, *args, **kwargs)


async def _patched_interaction_defer(response: discord.InteractionResponse, *args, **kwargs):
    interaction = getattr(response, "_parent", None)
    try:
        if interaction is None or interaction.client is None:
            raise RuntimeError("Interakce nemá klienta.")
        writer = get_writer(interaction.client)
        return await writer.defer_interaction(interaction, *args, **kwargs)
    except discord.NotFound as exc:
        if getattr(exc, "code", None) == 10062:
            logging.getLogger("botdc.discord_write").info(
                "Interaction vypršela (Unknown interaction) při defer."
            )
            return None
        raise
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("botdc.discord_write").warning(
            "Fallback na originální defer pro InteractionResponse.", exc_info=exc
        )
        original = getattr(discord.InteractionResponse, "__discord_write_original_defer__", None)
        if original is None:
            raise
        return await original(response, *args, **kwargs)


async def _patched_interaction_edit(response: discord.InteractionResponse, *args, **kwargs):
    interaction = getattr(response, "_parent", None)
    try:
        if interaction is None or interaction.client is None:
            raise RuntimeError("Interakce nemá klienta.")
        writer = get_writer(interaction.client)
        return await writer.edit_interaction_response(interaction, *args, **kwargs)
    except discord.NotFound as exc:
        if getattr(exc, "code", None) == 10062:
            logging.getLogger("botdc.discord_write").info(
                "Interaction vypršela (Unknown interaction) při edit_message."
            )
            return None
        raise
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("botdc.discord_write").warning(
            "Fallback na originální edit_message pro InteractionResponse.", exc_info=exc
        )
        original = getattr(
            discord.InteractionResponse, "__discord_write_original_edit_message__", None
        )
        if original is None:
            raise
        return await original(response, *args, **kwargs)


async def _patched_interaction_modal(response: discord.InteractionResponse, *args, **kwargs):
    interaction = getattr(response, "_parent", None)
    try:
        if interaction is None or interaction.client is None:
            raise RuntimeError("Interakce nemá klienta.")
        writer = get_writer(interaction.client)
        return await writer.send_interaction_modal(interaction, *args, **kwargs)
    except discord.NotFound as exc:
        if getattr(exc, "code", None) == 10062:
            logging.getLogger("botdc.discord_write").info(
                "Interaction vypršela (Unknown interaction) při send_modal."
            )
            return None
        raise
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("botdc.discord_write").warning(
            "Fallback na originální send_modal pro InteractionResponse.", exc_info=exc
        )
        original = getattr(
            discord.InteractionResponse, "__discord_write_original_send_modal__", None
        )
        if original is None:
            raise
        return await original(response, *args, **kwargs)


async def _patched_webhook_send(webhook: discord.Webhook, *args, **kwargs):
    state = getattr(webhook, "_state", None)
    try:
        if state is None:
            raise RuntimeError("Webhook nemá state.")
        writer = get_writer(_get_client_from_state(webhook))
        return await writer.send_webhook_message(webhook, *args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("botdc.discord_write").warning(
            "Fallback na originální send pro webhook %s.",
            type(webhook).__name__,
            exc_info=exc,
        )
        original = getattr(type(webhook), "__discord_write_original_send__", None)
        if original is None:
            raise
        return await original(webhook, *args, **kwargs)


class DiscordWriteCoordinatorCog(commands.Cog, name="DiscordWriteCoordinator"):
    """Centralizuje běžné write operace přes queue.

    Patche se týkají send/edit/delete/channel/member/guild a interaction/webhook cest.
    Interní HTTP volání discord.py mimo tyto veřejné metody nejsou patchována.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("botdc.discord_write")
        self._patch_enabled = (
            os.getenv("DISCORD_WRITE_PATCH_ENABLED", "true").strip().lower() != "false"
        )
        self._queue: asyncio.PriorityQueue[tuple[int, int, WriteRequest]] = (
            asyncio.PriorityQueue()
        )
        self._queue_counter = itertools.count()
        self._worker_task: asyncio.Task | None = None
        self._scheduled_tasks: set[asyncio.Task] = set()
        self._last_write_at: float | None = None
        self._blocked_until: float | None = None
        self._rate_limit_buckets: dict[str, float] = {}
        self._max_backoff_seconds = 30.0
        # DISCORD_WRITE_MIN_INTERVAL_SECONDS (např. 0.1–1.0 s) určuje minimální rozestup zápisů.
        self._min_interval_seconds = DISCORD_WRITE_MIN_INTERVAL_SECONDS
        self._patched = False
        self._messageable_send_originals: dict[type, Callable[..., Any]] = {}
        self._channel_edit_originals: dict[type, Callable[..., Any]] = {}
        self._channel_delete_originals: dict[type, Callable[..., Any]] = {}
        self._channel_set_permissions_originals: dict[type, Callable[..., Any]] = {}
        self._original_message_edit: Optional[Callable[..., Any]] = None
        self._original_message_delete: Optional[Callable[..., Any]] = None
        self._original_create_text_channel: Optional[Callable[..., Any]] = None
        self._original_add_roles: Optional[Callable[..., Any]] = None
        self._original_remove_roles: Optional[Callable[..., Any]] = None
        self._original_ban: Optional[Callable[..., Any]] = None
        self._original_kick: Optional[Callable[..., Any]] = None
        self._original_timeout: Optional[Callable[..., Any]] = None
        self._original_member_edit: Optional[Callable[..., Any]] = None
        self._original_interaction_send: Optional[Callable[..., Any]] = None
        self._original_interaction_defer: Optional[Callable[..., Any]] = None
        self._original_interaction_edit: Optional[Callable[..., Any]] = None
        self._original_interaction_modal: Optional[Callable[..., Any]] = None
        self._original_webhook_send: Optional[Callable[..., Any]] = None
        self._original_followup_send: Optional[Callable[..., Any]] = None

    async def cog_load(self):
        try:
            await self._restore_pending()
        except Exception:  # noqa: BLE001
            self.logger.exception("Obnova pending Discord write fronty selhala.")
        try:
            self._restore_rate_limit_buckets()
        except Exception:  # noqa: BLE001
            self.logger.exception("Obnova rate limit bucketů selhala.")
        try:
            self._patch_methods()
        except Exception:  # noqa: BLE001
            self.logger.exception("Patchování Discord write metod selhalo.")
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def cog_unload(self):
        if self._worker_task:
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
        for task in list(self._scheduled_tasks):
            task.cancel()
        if self._scheduled_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*self._scheduled_tasks, return_exceptions=True)
        self._scheduled_tasks.clear()
        self._restore_methods()

    def _patch_methods(self):
        """Globální, verzí podmíněné patchování Discord write metod."""
        if self._patched:
            self.logger.warning("Discord write patching již bylo aplikováno.")
            return
        if not self._patch_enabled:
            self.logger.warning(
                "Discord write patching je vypnuto (DISCORD_WRITE_PATCH_ENABLED=false)."
            )
        apply_patches = self._patch_enabled
        patched_targets: list[str] = []
        self.logger.warning(
            "Discord write patching je globální; pokud běží více botů, sdílí patche."
        )

        messageable_send = getattr(discord.abc.Messageable, "send", None)
        if messageable_send is not None:
            self._messageable_send_originals[discord.abc.Messageable] = messageable_send
            if apply_patches:
                setattr(
                    discord.abc.Messageable,
                    "__discord_write_original_send__",
                    messageable_send,
                )
                discord.abc.Messageable.send = _patched_messageable_send
                patched_targets.append("Messageable.send")
        else:
            fallback_classes = [
                discord.TextChannel,
                discord.Thread,
                discord.DMChannel,
                discord.GroupChannel,
                discord.User,
                discord.Member,
            ]
            partial_messageable = getattr(discord, "PartialMessageable", None)
            if partial_messageable is not None:
                fallback_classes.append(partial_messageable)
            for cls in fallback_classes:
                original = getattr(cls, "send", None)
                if original is None:
                    continue
                self._messageable_send_originals[cls] = original
                if apply_patches:
                    setattr(cls, "__discord_write_original_send__", original)
                    cls.send = _patched_messageable_send
                    patched_targets.append(f"{cls.__name__}.send")

        self._original_message_edit = getattr(discord.Message, "edit", None)
        if self._original_message_edit is not None:
            if apply_patches:
                setattr(
                    discord.Message, "__discord_write_original_edit__", self._original_message_edit
                )
                discord.Message.edit = _patched_message_edit
                patched_targets.append("Message.edit")
        self._original_message_delete = getattr(discord.Message, "delete", None)
        if self._original_message_delete is not None:
            if apply_patches:
                setattr(
                    discord.Message,
                    "__discord_write_original_delete__",
                    self._original_message_delete,
                )
                discord.Message.delete = _patched_message_delete
                patched_targets.append("Message.delete")

        # Třídy s edit/delete v discord.py 2.x (Text/Thread/Voice/Stage/Category/Forum).
        channel_classes = [
            discord.TextChannel,
            discord.Thread,
            discord.VoiceChannel,
            discord.StageChannel,
            discord.CategoryChannel,
            discord.ForumChannel,
        ]
        if hasattr(discord.abc.GuildChannel, "edit"):
            channel_classes.append(discord.abc.GuildChannel)
        for cls in channel_classes:
            original_edit = getattr(cls, "edit", None)
            if original_edit is not None and cls not in self._channel_edit_originals:
                self._channel_edit_originals[cls] = original_edit
                if apply_patches:
                    setattr(cls, "__discord_write_original_edit__", original_edit)
                    cls.edit = _patched_channel_edit
                    patched_targets.append(f"{cls.__name__}.edit")
            original_delete = getattr(cls, "delete", None)
            if original_delete is not None and cls not in self._channel_delete_originals:
                self._channel_delete_originals[cls] = original_delete
                if apply_patches:
                    setattr(cls, "__discord_write_original_delete__", original_delete)
                    cls.delete = _patched_channel_delete
                    patched_targets.append(f"{cls.__name__}.delete")
            original_set_permissions = getattr(cls, "set_permissions", None)
            if (
                original_set_permissions is not None
                and cls not in self._channel_set_permissions_originals
            ):
                self._channel_set_permissions_originals[cls] = original_set_permissions
                if apply_patches:
                    setattr(
                        cls,
                        "__discord_write_original_set_permissions__",
                        original_set_permissions,
                    )
                    cls.set_permissions = _patched_channel_set_permissions
                    patched_targets.append(f"{cls.__name__}.set_permissions")

        self._original_create_text_channel = getattr(discord.Guild, "create_text_channel", None)
        if self._original_create_text_channel is not None:
            if apply_patches:
                setattr(
                    discord.Guild,
                    "__discord_write_original_create_text_channel__",
                    self._original_create_text_channel,
                )
                discord.Guild.create_text_channel = _patched_create_text_channel
                patched_targets.append("Guild.create_text_channel")

        self._original_add_roles = getattr(discord.Member, "add_roles", None)
        if self._original_add_roles is not None:
            if apply_patches:
                setattr(
                    discord.Member,
                    "__discord_write_original_add_roles__",
                    self._original_add_roles,
                )
                discord.Member.add_roles = _patched_member_add_roles
                patched_targets.append("Member.add_roles")
        self._original_remove_roles = getattr(discord.Member, "remove_roles", None)
        if self._original_remove_roles is not None:
            if apply_patches:
                setattr(
                    discord.Member,
                    "__discord_write_original_remove_roles__",
                    self._original_remove_roles,
                )
                discord.Member.remove_roles = _patched_member_remove_roles
                patched_targets.append("Member.remove_roles")

        self._original_ban = getattr(discord.Guild, "ban", None)
        if self._original_ban is not None:
            if apply_patches:
                setattr(discord.Guild, "__discord_write_original_ban__", self._original_ban)
                discord.Guild.ban = _patched_guild_ban
                patched_targets.append("Guild.ban")
        self._original_kick = getattr(discord.Member, "kick", None)
        if self._original_kick is not None:
            if apply_patches:
                setattr(discord.Member, "__discord_write_original_kick__", self._original_kick)
                discord.Member.kick = _patched_member_kick
                patched_targets.append("Member.kick")
        self._original_timeout = getattr(discord.Member, "timeout", None)
        if self._original_timeout is not None:
            if apply_patches:
                setattr(
                    discord.Member, "__discord_write_original_timeout__", self._original_timeout
                )
                discord.Member.timeout = _patched_member_timeout
                patched_targets.append("Member.timeout")
        self._original_member_edit = getattr(discord.Member, "edit", None)
        if self._original_member_edit is not None:
            if apply_patches:
                setattr(discord.Member, "__discord_write_original_edit__", self._original_member_edit)
                discord.Member.edit = _patched_member_edit
                patched_targets.append("Member.edit")

        self._original_interaction_send = getattr(discord.InteractionResponse, "send_message", None)
        if self._original_interaction_send is not None:
            if apply_patches:
                setattr(
                    discord.InteractionResponse,
                    "__discord_write_original_send_message__",
                    self._original_interaction_send,
                )
                discord.InteractionResponse.send_message = _patched_interaction_send
                patched_targets.append("InteractionResponse.send_message")
        self._original_interaction_defer = getattr(discord.InteractionResponse, "defer", None)
        if self._original_interaction_defer is not None:
            if apply_patches:
                setattr(
                    discord.InteractionResponse,
                    "__discord_write_original_defer__",
                    self._original_interaction_defer,
                )
                discord.InteractionResponse.defer = _patched_interaction_defer
                patched_targets.append("InteractionResponse.defer")
        self._original_interaction_edit = getattr(discord.InteractionResponse, "edit_message", None)
        if self._original_interaction_edit is not None:
            if apply_patches:
                setattr(
                    discord.InteractionResponse,
                    "__discord_write_original_edit_message__",
                    self._original_interaction_edit,
                )
                discord.InteractionResponse.edit_message = _patched_interaction_edit
                patched_targets.append("InteractionResponse.edit_message")
        self._original_interaction_modal = getattr(discord.InteractionResponse, "send_modal", None)
        if self._original_interaction_modal is not None:
            if apply_patches:
                setattr(
                    discord.InteractionResponse,
                    "__discord_write_original_send_modal__",
                    self._original_interaction_modal,
                )
                discord.InteractionResponse.send_modal = _patched_interaction_modal
                patched_targets.append("InteractionResponse.send_modal")

        self._original_webhook_send = getattr(discord.Webhook, "send", None)
        if self._original_webhook_send is not None:
            if apply_patches:
                setattr(
                    discord.Webhook, "__discord_write_original_send__", self._original_webhook_send
                )
                discord.Webhook.send = _patched_webhook_send
                patched_targets.append("Webhook.send")
        else:
            followup_cls = getattr(discord, "InteractionFollowup", None)
            if followup_cls is not None:
                self._original_followup_send = getattr(followup_cls, "send", None)
                if self._original_followup_send is not None:
                    if apply_patches:
                        setattr(
                            followup_cls,
                            "__discord_write_original_send__",
                            self._original_followup_send,
                        )
                        followup_cls.send = _patched_webhook_send
                        patched_targets.append("InteractionFollowup.send")

        if apply_patches:
            self._patched = True
            self.logger.info(
                "Patched %d methods for Discord write coordinator: %s",
                len(patched_targets),
                ", ".join(patched_targets),
            )

    def _restore_methods(self):
        """Vrací originální metody po globálním patchování."""
        if not self._patched:
            return
        for cls, original in self._messageable_send_originals.items():
            if getattr(cls, "send", None) is _patched_messageable_send:
                cls.send = original
        self._messageable_send_originals.clear()

        if self._original_message_edit and discord.Message.edit is _patched_message_edit:
            discord.Message.edit = self._original_message_edit
        if self._original_message_delete and discord.Message.delete is _patched_message_delete:
            discord.Message.delete = self._original_message_delete

        for cls, original in self._channel_edit_originals.items():
            if getattr(cls, "edit", None) is _patched_channel_edit:
                cls.edit = original
        for cls, original in self._channel_delete_originals.items():
            if getattr(cls, "delete", None) is _patched_channel_delete:
                cls.delete = original
        for cls, original in self._channel_set_permissions_originals.items():
            if getattr(cls, "set_permissions", None) is _patched_channel_set_permissions:
                cls.set_permissions = original
        self._channel_edit_originals.clear()
        self._channel_delete_originals.clear()
        self._channel_set_permissions_originals.clear()

        if (
            self._original_create_text_channel
            and discord.Guild.create_text_channel is _patched_create_text_channel
        ):
            discord.Guild.create_text_channel = self._original_create_text_channel
        if self._original_add_roles and discord.Member.add_roles is _patched_member_add_roles:
            discord.Member.add_roles = self._original_add_roles
        if (
            self._original_remove_roles
            and discord.Member.remove_roles is _patched_member_remove_roles
        ):
            discord.Member.remove_roles = self._original_remove_roles
        if self._original_ban and discord.Guild.ban is _patched_guild_ban:
            discord.Guild.ban = self._original_ban
        if self._original_kick and discord.Member.kick is _patched_member_kick:
            discord.Member.kick = self._original_kick
        if self._original_timeout and discord.Member.timeout is _patched_member_timeout:
            discord.Member.timeout = self._original_timeout
        if self._original_member_edit and discord.Member.edit is _patched_member_edit:
            discord.Member.edit = self._original_member_edit

        if (
            self._original_interaction_send
            and discord.InteractionResponse.send_message is _patched_interaction_send
        ):
            discord.InteractionResponse.send_message = self._original_interaction_send
        if (
            self._original_interaction_defer
            and discord.InteractionResponse.defer is _patched_interaction_defer
        ):
            discord.InteractionResponse.defer = self._original_interaction_defer
        if (
            self._original_interaction_edit
            and discord.InteractionResponse.edit_message is _patched_interaction_edit
        ):
            discord.InteractionResponse.edit_message = self._original_interaction_edit
        if (
            self._original_interaction_modal
            and discord.InteractionResponse.send_modal is _patched_interaction_modal
        ):
            discord.InteractionResponse.send_modal = self._original_interaction_modal

        if self._original_webhook_send and discord.Webhook.send is _patched_webhook_send:
            discord.Webhook.send = self._original_webhook_send
        followup_cls = getattr(discord, "InteractionFollowup", None)
        if (
            followup_cls is not None
            and self._original_followup_send
            and getattr(followup_cls, "send", None) is _patched_webhook_send
        ):
            followup_cls.send = self._original_followup_send

        self._patched = False

    async def _restore_pending(self):
        count = 0
        for item in fetch_pending_discord_writes():
            try:
                payload = json.loads(item["payload"])
            except json.JSONDecodeError:
                self.logger.error(
                    "Nelze načíst payload pro discord zápis %s", item["id"]
                )
                continue
            request = WriteRequest(
                operation=item["operation"],
                payload=payload,
                persist=True,
                future=None,
                db_id=item["id"],
                priority=WritePriority.normalize(item.get("priority")),
                attempts=int(item.get("attempts") or 0),
                next_retry_at=self._parse_next_retry_at(item.get("next_retry_at")),
            )
            if await self._maybe_schedule_future_request(request):
                count += 1
                continue
            await self._queue.put((request.priority, next(self._queue_counter), request))
            count += 1
        self.logger.info("Obnoveno %d pending Discord write záznamů.", count)

    async def _worker_loop(self):
        while True:
            _priority, _order, request = await self._queue.get()
            if await self._maybe_schedule_future_request(request):
                continue
            bucket_key = self._get_rate_limit_bucket_key(request)
            await self._respect_rate_limit(bucket_key)
            try:
                result = await self._execute_request(request)
            except discord.HTTPException as exc:
                if exc.status == 429:
                    retry_after = getattr(exc, "retry_after", None)
                    delay = self._compute_backoff_delay(request, retry_after)
                    now = time.time()
                    blocked_until = now + delay
                    if self._blocked_until is None or blocked_until > self._blocked_until:
                        self._blocked_until = blocked_until
                    if bucket_key is not None:
                        self._set_rate_limit_bucket(bucket_key, blocked_until)
                    next_retry_at = datetime.utcnow() + timedelta(seconds=delay)
                    self.logger.warning(
                        "Rate limit hit, čekám %.2fs před opakováním.", delay
                    )
                    request.attempts += 1
                    request.next_retry_at = next_retry_at
                    if request.persist and request.db_id is not None:
                        mark_discord_write_retry(
                            request.db_id,
                            request.attempts,
                            next_retry_at.isoformat(),
                        )
                    await self._schedule_request(request, delay)
                    continue
                self._mark_failed(request, exc)
                continue
            except Exception as exc:  # noqa: BLE001
                self._mark_failed(request, exc)
                continue

            self._last_write_at = time.time()
            if request.persist and request.db_id is not None:
                mark_discord_write_done(request.db_id)
            if request.future and not request.future.done():
                request.future.set_result(result)

    async def _respect_rate_limit(self, bucket_key: str | None = None):
        now = time.time()
        wait_for = 0.0
        if self._last_write_at is not None:
            wait_for = max(
                wait_for, self._min_interval_seconds - (now - self._last_write_at)
            )
        if self._blocked_until is not None and self._blocked_until > now:
            wait_for = max(wait_for, self._blocked_until - now)
        if bucket_key is not None:
            bucket_until = self._rate_limit_buckets.get(bucket_key)
            if bucket_until is not None:
                if bucket_until <= now:
                    self._rate_limit_buckets.pop(bucket_key, None)
                    delete_discord_rate_limit_bucket(bucket_key)
                else:
                    wait_for = max(wait_for, bucket_until - now)
        if wait_for > 0:
            await asyncio.sleep(wait_for)

    def _restore_rate_limit_buckets(self) -> None:
        now = time.time()
        pruned = prune_discord_rate_limit_buckets(now)
        if pruned:
            self.logger.info("Pročištěno %d expirovaných rate limit bucketů.", pruned)
        self._rate_limit_buckets = fetch_discord_rate_limit_buckets(now)
        if self._rate_limit_buckets:
            self.logger.info(
                "Obnoveno %d rate limit bucketů.",
                len(self._rate_limit_buckets),
            )

    def _set_rate_limit_bucket(self, bucket_key: str, blocked_until: float) -> None:
        self._rate_limit_buckets[bucket_key] = blocked_until
        upsert_discord_rate_limit_bucket(bucket_key, blocked_until)

    def _parse_next_retry_at(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            self.logger.warning("Neplatné next_retry_at: %s", value)
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    async def _maybe_schedule_future_request(self, request: WriteRequest) -> bool:
        if request.next_retry_at is None:
            return False
        delay = (request.next_retry_at - datetime.utcnow()).total_seconds()
        if delay <= 0:
            request.next_retry_at = None
            return False
        await self._schedule_request(request, delay)
        return True

    async def _schedule_request(self, request: WriteRequest, delay: float) -> None:
        async def _delayed_put():
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return
            request.next_retry_at = None
            await self._queue.put((request.priority, next(self._queue_counter), request))

        task = asyncio.create_task(_delayed_put())
        self._scheduled_tasks.add(task)
        task.add_done_callback(lambda done_task: self._scheduled_tasks.discard(done_task))

    def _compute_backoff_delay(self, request: WriteRequest, retry_after: float | None) -> float:
        base_delay = float(retry_after) if retry_after is not None else self._min_interval_seconds
        backoff = base_delay * (2 ** min(request.attempts, 5))
        backoff = min(backoff, self._max_backoff_seconds)
        jitter = random.uniform(0, min(1.0, backoff * 0.25))
        return max(self._min_interval_seconds, backoff + jitter)

    def _mark_failed(self, request: WriteRequest, exc: Exception):
        self.logger.exception("Discord write selhal: %s", request.operation)
        if request.persist and request.db_id is not None:
            mark_discord_write_failed(request.db_id, repr(exc))
        if request.future and not request.future.done():
            request.future.set_exception(exc)

    async def _execute_request(self, request: WriteRequest):
        self._get_rate_limit_bucket_key(request)
        op = request.operation
        payload = self._deserialize_payload(request.payload)
        if op == "send_message":
            return await self._op_send_message(payload)
        if op == "edit_message":
            return await self._op_edit_message(payload)
        if op == "delete_message":
            return await self._op_delete_message(payload)
        if op == "edit_channel":
            return await self._op_edit_channel(payload)
        if op == "delete_channel":
            return await self._op_delete_channel(payload)
        if op == "create_text_channel":
            return await self._op_create_text_channel(payload)
        if op == "add_roles":
            return await self._op_add_roles(payload)
        if op == "remove_roles":
            return await self._op_remove_roles(payload)
        if op == "ban_member":
            return await self._op_ban_member(payload)
        if op == "kick_member":
            return await self._op_kick_member(payload)
        if op == "timeout_member":
            return await self._op_timeout_member(payload)
        if op == "edit_member":
            return await self._op_edit_member(payload)
        if op == "interaction_response":
            return await self._op_interaction_response(payload)
        if op == "interaction_followup":
            return await self._op_interaction_followup(payload)
        if op == "interaction_edit":
            return await self._op_interaction_edit(payload)
        if op == "interaction_defer":
            return await self._op_interaction_defer(payload)
        if op == "interaction_modal":
            return await self._op_interaction_modal(payload)
        if op == "webhook_send":
            return await self._op_webhook_send(payload)
        if op == "set_permissions":
            return await self._op_set_permissions(payload)
        raise ValueError(f"Neznámá operace: {op}")

    def _get_rate_limit_bucket_key(self, request: WriteRequest) -> str | None:
        if request.bucket_key is not None:
            return request.bucket_key
        payload = request.payload or {}
        channel_id = payload.get("channel_id")
        if channel_id is None and payload.get("target_type") == "channel":
            channel_id = payload.get("target_id")
        if channel_id is None:
            interaction = payload.get("interaction")
            channel_id = getattr(interaction, "channel_id", None)
        message_id = payload.get("message_id")
        webhook_id = payload.get("webhook_id")
        if webhook_id is None:
            webhook = payload.get("webhook")
            webhook_id = getattr(webhook, "id", None)
        identifiers = [
            ("channel_id", channel_id),
            ("webhook_id", webhook_id),
            ("message_id", message_id),
        ]
        key_parts = [request.operation]
        for name, value in identifiers:
            if value is not None:
                key_parts.append(f"{name}:{value}")
        request.bucket_key = "|".join(key_parts)
        return request.bucket_key

    async def _op_send_message(self, payload: dict[str, Any]):
        try:
            kwargs = payload.get("kwargs") if isinstance(payload, dict) else None
            if isinstance(kwargs, dict) and "components" in kwargs:
                kwargs["components"] = _sanitize_components_v2(kwargs["components"])
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("botdc.discord_write").warning(
                "Sanitizace Components V2 selhala, pokračuji bez úprav.", exc_info=exc
            )
        target = await self._resolve_target(payload)
        if target is None:
            raise RuntimeError("Cíl zprávy nebyl nalezen.")
        original = self._get_messageable_original(target)
        if original is None:
            raise RuntimeError("Send není dostupný pro daný target.")
        if "kwargs" in payload:
            self._sanitize_view_kwargs(payload["kwargs"])
            self._sanitize_components_v2_kwargs(payload["kwargs"])
        return await original(target, **payload["kwargs"])

    async def _op_edit_message(self, payload: dict[str, Any]):
        message = await self._resolve_message(payload)
        if message is None:
            raise RuntimeError("Zpráva k úpravě nebyla nalezena.")
        original = getattr(type(message), "__discord_write_original_edit__", None)
        if original is None:
            original = getattr(type(message), "edit", None)
        if original is None:
            raise RuntimeError("Message.edit není dostupné.")
        if "kwargs" in payload:
            self._sanitize_view_kwargs(payload["kwargs"])
            self._sanitize_components_v2_kwargs(payload["kwargs"])
        return await original(message, **payload["kwargs"])

    async def _op_delete_message(self, payload: dict[str, Any]):
        message = await self._resolve_message(payload)
        if message is None:
            raise RuntimeError("Zpráva ke smazání nebyla nalezena.")
        if self._original_message_delete is None:
            raise RuntimeError("Message.delete není dostupné.")
        return await self._original_message_delete(message, **payload["kwargs"])

    async def _op_edit_channel(self, payload: dict[str, Any]):
        channel = await self._resolve_channel(payload["channel_id"])
        if channel is None:
            raise RuntimeError("Kanál k úpravě nebyl nalezen.")
        original = self._get_channel_original(channel, self._channel_edit_originals, "edit")
        if original is None:
            self.logger.warning("Edit kanálu není podporován pro %s.", type(channel).__name__)
            raise RuntimeError("Kanál k úpravě nemá podporovaný edit.")
        return await original(channel, **payload["kwargs"])

    async def _op_set_permissions(self, payload: dict[str, Any]):
        channel = await self._resolve_channel(payload["channel_id"])
        if channel is None or not isinstance(channel, discord.abc.GuildChannel):
            raise RuntimeError("Kanál pro set_permissions nebyl nalezen.")
        target = await self._resolve_permission_target(channel, payload)
        if target is None:
            raise RuntimeError("Cíl pro set_permissions nebyl nalezen.")
        original = self._get_channel_original(
            channel, self._channel_set_permissions_originals, "set_permissions"
        )
        if original is None:
            self.logger.warning(
                "set_permissions není podporováno pro %s.", type(channel).__name__
            )
            raise RuntimeError("Kanál nemá podporovaný set_permissions.")
        overwrite = None
        if payload.get("overwrite_allow") is not None and payload.get("overwrite_deny") is not None:
            allow = discord.Permissions(payload["overwrite_allow"])
            deny = discord.Permissions(payload["overwrite_deny"])
            overwrite = discord.PermissionOverwrite.from_pair(allow, deny)
        return await original(channel, target, overwrite=overwrite, reason=payload.get("reason"))

    async def _op_delete_channel(self, payload: dict[str, Any]):
        channel = await self._resolve_channel(payload["channel_id"])
        if channel is None:
            raise RuntimeError("Kanál ke smazání nebyl nalezen.")
        original = self._get_channel_original(channel, self._channel_delete_originals, "delete")
        if original is None:
            self.logger.warning("Delete kanálu není podporován pro %s.", type(channel).__name__)
            raise RuntimeError("Kanál ke smazání nemá podporovaný delete.")
        return await original(channel, **payload["kwargs"])

    async def _op_create_text_channel(self, payload: dict[str, Any]):
        guild = self.bot.get_guild(payload["guild_id"])
        if guild is None:
            raise RuntimeError("Guild nebyla nalezena.")
        if self._original_create_text_channel is None:
            raise RuntimeError("Guild.create_text_channel není dostupné.")
        return await self._original_create_text_channel(guild, payload["name"], **payload["kwargs"])

    async def _op_add_roles(self, payload: dict[str, Any]):
        member = await self._resolve_member(payload)
        if member is None:
            raise RuntimeError("Člen nebyl nalezen pro přidání role.")
        roles = self._resolve_roles(member.guild, payload["role_ids"])
        if self._original_add_roles is None:
            raise RuntimeError("Member.add_roles není dostupné.")
        return await self._original_add_roles(member, *roles, reason=payload.get("reason"))

    async def _op_remove_roles(self, payload: dict[str, Any]):
        member = await self._resolve_member(payload)
        if member is None:
            raise RuntimeError("Člen nebyl nalezen pro odebrání role.")
        roles = self._resolve_roles(member.guild, payload["role_ids"])
        if self._original_remove_roles is None:
            raise RuntimeError("Member.remove_roles není dostupné.")
        return await self._original_remove_roles(member, *roles, reason=payload.get("reason"))

    async def _op_ban_member(self, payload: dict[str, Any]):
        guild = self.bot.get_guild(payload["guild_id"])
        if guild is None:
            raise RuntimeError("Guild nebyla nalezena pro ban.")
        user = await self._resolve_user(payload["user_id"])
        if user is None:
            raise RuntimeError("Uživatel nebyl nalezen pro ban.")
        if self._original_ban is None:
            raise RuntimeError("Guild.ban není dostupné.")
        return await self._original_ban(
            guild,
            user,
            reason=payload.get("reason"),
            delete_message_days=payload.get("delete_message_days", 0),
        )

    async def _op_kick_member(self, payload: dict[str, Any]):
        member = await self._resolve_member(payload)
        if member is None:
            raise RuntimeError("Člen nebyl nalezen pro kick.")
        if self._original_kick is None:
            raise RuntimeError("Member.kick není dostupné.")
        return await self._original_kick(member, reason=payload.get("reason"))

    async def _op_timeout_member(self, payload: dict[str, Any]):
        member = await self._resolve_member(payload)
        if member is None:
            raise RuntimeError("Člen nebyl nalezen pro timeout.")
        if self._original_timeout is None:
            raise RuntimeError("Member.timeout není dostupné.")
        return await self._original_timeout(member, payload["until"], reason=payload.get("reason"))

    async def _op_edit_member(self, payload: dict[str, Any]):
        member = await self._resolve_member(payload)
        if member is None:
            raise RuntimeError("Člen nebyl nalezen pro editaci.")
        if self._original_member_edit is None:
            raise RuntimeError("Member.edit není dostupné.")
        return await self._original_member_edit(member, **payload["kwargs"])

    async def _op_interaction_response(self, payload: dict[str, Any]):
        interaction = payload["interaction"]
        if self._original_interaction_send is None:
            raise RuntimeError("InteractionResponse.send_message není dostupné.")
        if "kwargs" in payload:
            self._sanitize_view_kwargs(payload["kwargs"])
            self._sanitize_components_v2_kwargs(payload["kwargs"])
        try:
            return await self._original_interaction_send(
                interaction.response,
                *payload.get("args", ()),
                **payload["kwargs"],
            )
        except discord.NotFound as exc:
            if getattr(exc, "code", None) == 10062:
                self.logger.info(
                    "Interaction vypršela (Unknown interaction) při send_message."
                )
                return None
            raise

    async def _op_interaction_followup(self, payload: dict[str, Any]):
        interaction = payload["interaction"]
        if "kwargs" in payload:
            self._sanitize_view_kwargs(payload["kwargs"])
            self._sanitize_components_v2_kwargs(payload["kwargs"])
        if self._original_webhook_send is not None:
            return await self._original_webhook_send(interaction.followup, **payload["kwargs"])
        if self._original_followup_send is not None:
            return await self._original_followup_send(interaction.followup, **payload["kwargs"])
        raise RuntimeError("Followup send není dostupné v této verzi discord.py.")

    async def _op_interaction_edit(self, payload: dict[str, Any]):
        interaction = payload["interaction"]
        if self._original_interaction_edit is None:
            raise RuntimeError("InteractionResponse.edit_message není dostupné.")
        if "kwargs" in payload:
            self._sanitize_view_kwargs(payload["kwargs"])
            self._sanitize_components_v2_kwargs(payload["kwargs"])
        return await self._original_interaction_edit(
            interaction.response,
            *payload.get("args", ()),
            **payload["kwargs"],
        )

    async def _op_interaction_defer(self, payload: dict[str, Any]):
        interaction = payload["interaction"]
        if self._original_interaction_defer is None:
            raise RuntimeError("InteractionResponse.defer není dostupné.")
        try:
            return await self._original_interaction_defer(
                interaction.response,
                *payload.get("args", ()),
                **payload["kwargs"],
            )
        except discord.NotFound as exc:
            if getattr(exc, "code", None) == 10062:
                self.logger.info(
                    "Interaction vypršela (Unknown interaction) při defer."
                )
                return None
            raise

    async def _op_interaction_modal(self, payload: dict[str, Any]):
        interaction = payload["interaction"]
        if self._original_interaction_modal is None:
            raise RuntimeError("InteractionResponse.send_modal není dostupné.")
        try:
            return await self._original_interaction_modal(
                interaction.response, payload["modal"]
            )
        except discord.NotFound as exc:
            if getattr(exc, "code", None) == 10062:
                self.logger.info(
                    "Interaction vypršela (Unknown interaction) při send_modal."
                )
                return None
            raise

    async def _op_webhook_send(self, payload: dict[str, Any]):
        webhook = payload["webhook"]
        if self._original_webhook_send is None:
            raise RuntimeError("Webhook.send není dostupné v této verzi discord.py.")
        if "kwargs" in payload:
            self._sanitize_view_kwargs(payload["kwargs"])
            self._sanitize_components_v2_kwargs(payload["kwargs"])
        return await self._original_webhook_send(
            webhook, *payload.get("args", ()), **payload["kwargs"]
        )

    async def send_message(self, target: discord.abc.Messageable, *args, **kwargs):
        if args:
            if len(args) > 1:
                raise TypeError("send_message očekává nejvýše jeden poziční argument.")
            if "content" in kwargs and kwargs["content"] is not None:
                raise TypeError("send_message obdrželo duplicitní content.")
            kwargs["content"] = args[0]
        payload, persist = self._build_send_payload(target, kwargs)
        return await self._enqueue("send_message", payload, persist)

    async def edit_message(self, message: discord.Message, **kwargs):
        payload, persist = self._build_message_payload(message, kwargs)
        return await self._enqueue("edit_message", payload, persist)

    async def delete_message(self, message: discord.Message, **kwargs):
        payload = {
            "channel_id": message.channel.id,
            "message_id": message.id,
            "kwargs": kwargs,
        }
        return await self._enqueue("delete_message", payload, persist=True)

    async def edit_channel(self, channel: discord.abc.GuildChannel, **kwargs):
        payload = {"channel_id": channel.id, "kwargs": kwargs}
        persist = self._is_serializable(kwargs)
        return await self._enqueue("edit_channel", payload, persist, priority=kwargs.pop("priority", None))

    async def delete_channel(self, channel: discord.abc.GuildChannel, **kwargs):
        payload = {"channel_id": channel.id, "kwargs": kwargs}
        persist = self._is_serializable(kwargs)
        return await self._enqueue("delete_channel", payload, persist, priority=kwargs.pop("priority", None))

    async def set_permissions(
        self,
        channel: discord.abc.GuildChannel,
        target: discord.abc.Snowflake,
        *,
        overwrite: discord.PermissionOverwrite | None,
        reason: str | None = None,
        priority: str | int | None = None,
    ):
        payload = self._build_set_permissions_payload(
            channel, target, overwrite=overwrite, reason=reason
        )
        persist = self._is_serializable(payload)
        return await self._enqueue("set_permissions", payload, persist, priority=priority)

    async def create_text_channel(self, guild: discord.Guild, name: str, **kwargs):
        payload = {"guild_id": guild.id, "name": name, "kwargs": kwargs}
        persist = self._is_serializable(kwargs)
        return await self._enqueue(
            "create_text_channel", payload, persist, priority=kwargs.pop("priority", None)
        )

    async def add_roles(self, member: discord.Member, *roles: discord.Role, reason: str | None = None):
        payload = {
            "guild_id": member.guild.id,
            "member_id": member.id,
            "role_ids": [role.id for role in roles],
            "reason": reason,
        }
        return await self._enqueue("add_roles", payload, persist=True)

    async def remove_roles(
        self, member: discord.Member, *roles: discord.Role, reason: str | None = None
    ):
        payload = {
            "guild_id": member.guild.id,
            "member_id": member.id,
            "role_ids": [role.id for role in roles],
            "reason": reason,
        }
        return await self._enqueue("remove_roles", payload, persist=True)

    async def ban_member(
        self,
        guild: discord.Guild,
        user: discord.abc.User,
        reason: str | None = None,
        delete_message_days: int = 0,
    ):
        payload = {
            "guild_id": guild.id,
            "user_id": user.id,
            "reason": reason,
            "delete_message_days": delete_message_days,
        }
        return await self._enqueue(
            "ban_member", payload, persist=True, priority=WritePriority.URGENT
        )

    async def kick_member(self, member: discord.Member, reason: str | None = None):
        payload = {
            "guild_id": member.guild.id,
            "member_id": member.id,
            "reason": reason,
        }
        return await self._enqueue(
            "kick_member", payload, persist=True, priority=WritePriority.URGENT
        )

    async def timeout_member(
        self, member: discord.Member, until, reason: str | None = None
    ):
        payload = {
            "guild_id": member.guild.id,
            "member_id": member.id,
            "until": until,
            "reason": reason,
        }
        persist = self._is_serializable({"until": until})
        return await self._enqueue(
            "timeout_member", payload, persist, priority=WritePriority.URGENT
        )

    async def edit_member(self, member: discord.Member, **kwargs):
        payload = {
            "guild_id": member.guild.id,
            "member_id": member.id,
            "kwargs": kwargs,
        }
        persist = self._is_serializable(kwargs)
        return await self._enqueue("edit_member", payload, persist)

    async def send_interaction_response(
        self, interaction: discord.Interaction, *args, **kwargs
    ):
        self._sanitize_view_kwargs(kwargs)
        payload = {"interaction": interaction, "args": args, "kwargs": kwargs}
        return await self._enqueue("interaction_response", payload, persist=False)

    async def send_interaction_followup(self, interaction: discord.Interaction, **kwargs):
        self._sanitize_view_kwargs(kwargs)
        payload = {"interaction": interaction, "kwargs": kwargs}
        return await self._enqueue("interaction_followup", payload, persist=False)

    async def edit_interaction_response(
        self, interaction: discord.Interaction, *args, **kwargs
    ):
        self._sanitize_view_kwargs(kwargs)
        payload = {"interaction": interaction, "args": args, "kwargs": kwargs}
        return await self._enqueue("interaction_edit", payload, persist=False)

    async def defer_interaction(self, interaction: discord.Interaction, *args, **kwargs):
        payload = {"interaction": interaction, "args": args, "kwargs": kwargs}
        return await self._enqueue("interaction_defer", payload, persist=False)

    async def send_interaction_modal(
        self, interaction: discord.Interaction, modal: discord.ui.Modal
    ):
        payload = {"interaction": interaction, "modal": modal}
        return await self._enqueue("interaction_modal", payload, persist=False)

    async def send_webhook_message(self, webhook: discord.Webhook, *args, **kwargs):
        self._sanitize_view_kwargs(kwargs)
        payload = {"webhook": webhook, "args": args, "kwargs": kwargs}
        return await self._enqueue("webhook_send", payload, persist=False)

    async def _enqueue(
        self,
        operation: str,
        payload: dict[str, Any],
        persist: bool,
        priority: str | int | None = None,
    ):
        normalized_priority = WritePriority.normalize(priority)
        db_id = None
        if persist:
            stored_payload = self._serialize_payload(payload)
            db_id = enqueue_discord_write(operation, stored_payload, normalized_priority)
        future = asyncio.get_running_loop().create_future()
        request = WriteRequest(
            operation=operation,
            payload=payload,
            persist=persist,
            future=future,
            db_id=db_id,
            priority=normalized_priority,
        )
        await self._queue.put((normalized_priority, next(self._queue_counter), request))
        return await future

    def _serialize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        serialized: dict[str, Any] = {}
        for key, value in payload.items():
            if key in {"interaction", "modal", "webhook"}:
                continue
            serialized[key] = self._serialize_item(value)
        return serialized

    def _serialize_item(self, item: Any):
        if isinstance(item, datetime):
            return {"__datetime__": item.isoformat()}
        if isinstance(item, timedelta):
            return {"__timedelta__": item.total_seconds()}
        if isinstance(item, list):
            return [self._serialize_item(value) for value in item]
        if isinstance(item, dict):
            return {key: self._serialize_item(value) for key, value in item.items()}
        return item

    def _deserialize_item(self, item: Any):
        if isinstance(item, dict) and "__datetime__" in item:
            return datetime.fromisoformat(item["__datetime__"])
        if isinstance(item, dict) and "__timedelta__" in item:
            return timedelta(seconds=float(item["__timedelta__"]))
        if isinstance(item, list):
            return [self._deserialize_item(value) for value in item]
        if isinstance(item, dict):
            return {key: self._deserialize_item(value) for key, value in item.items()}
        return item

    def _deserialize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        deserialized: dict[str, Any] = {}
        for key, value in payload.items():
            deserialized[key] = self._deserialize_item(value)
        return deserialized

    def _is_serializable(self, payload: dict[str, Any]) -> bool:
        try:
            json.dumps(self._serialize_payload(payload))
        except TypeError:
            return False
        return True

    def _sanitize_view_kwargs(self, kwargs: dict[str, Any]) -> None:
        view = kwargs.get("view")
        if view is None:
            if kwargs.get("components") is not None:
                self._sanitize_component_item(kwargs["components"])
            return
        if isinstance(view, discord.ui.LayoutView):
            kwargs.pop("embed", None)
            kwargs.pop("embeds", None)
        children = getattr(view, "children", None)
        if children is not None:
            self._sanitize_layout_items(children)
        if kwargs.get("components") is not None:
            self._sanitize_component_item(kwargs["components"])

    def _sanitize_layout_items(self, items: list[Any]) -> None:
        for item in items:
            self._sanitize_component_item(item)

    def _sanitize_text_component_value(self, value: Any) -> str:
        if value is None:
            return " "
        text = str(value)
        if text == "":
            return " "
        if len(text) > 4000:
            return f"{text[:3997]}..."
        return text

    def _sanitize_component_item(self, item: Any) -> None:
        if item is None:
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                self._sanitize_component_item(child)
            return
        if isinstance(item, dict):
            for field in ("content", "label", "value"):
                if field in item:
                    item[field] = self._sanitize_text_component_value(item[field])
            nested = item.get("components") or item.get("children") or item.get("items")
            if nested:
                self._sanitize_component_item(nested)
            return
        for field in ("content", "label", "value"):
            if hasattr(item, field):
                value = getattr(item, field, None)
                setattr(item, field, self._sanitize_text_component_value(value))
        children = (
            getattr(item, "children", None)
            or getattr(item, "components", None)
            or getattr(item, "items", None)
        )
        if children:
            self._sanitize_component_item(children)

    def _sanitize_components_v2_kwargs(self, kwargs: dict[str, Any]) -> None:
        components = kwargs.get("components")
        if components is None:
            return
        kwargs.pop("embed", None)
        kwargs.pop("embeds", None)
        try:
            self._sanitize_components_v2(components)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(
                "Sanitizace Components V2 selhala, pokračuji bez úprav.", exc_info=exc
            )

    def _sanitize_components_v2(self, components: Any) -> None:
        def sanitize_node(node: Any) -> None:
            if node is None:
                return
            if isinstance(node, (list, tuple)):
                for child in node:
                    sanitize_node(child)
                return
            if isinstance(node, dict):
                if "content" in node:
                    node["content"] = self.safe_textdisplay_content(node.get("content"))
                nested = node.get("components")
                if nested is not None:
                    sanitize_node(nested)
                return

        sanitize_node(components)

    def safe_textdisplay_content(self, value: Any) -> str:
        if value is None:
            text = ""
        elif isinstance(value, str):
            text = value
        else:
            try:
                text = str(value)
            except Exception:  # noqa: BLE001
                text = ""
        if text.strip() == "":
            return "\u200b"
        if len(text) > 4000:
            suffix = "… (zkráceno)"
            limit = 4000
            if len(suffix) >= limit:
                return text[:limit]
            return f"{text[:limit - len(suffix)]}{suffix}"
        return text

    def _build_send_payload(self, target: discord.abc.Messageable, kwargs: dict[str, Any]):
        payload_kwargs = kwargs.copy()
        if "embed" in payload_kwargs and payload_kwargs["embed"] is not None:
            payload_kwargs["embeds"] = [payload_kwargs.pop("embed")]
        self._sanitize_view_kwargs(payload_kwargs)
        self._sanitize_components_v2_kwargs(payload_kwargs)
        persist = True
        if payload_kwargs.get("view") is not None:
            persist = False
        if payload_kwargs.get("file") is not None or payload_kwargs.get("files") is not None:
            persist = False
        if payload_kwargs.get("allowed_mentions") is not None:
            persist = False
        has_verified_clan_prefix = False
        if payload_kwargs.get("components") is not None:
            has_verified_clan_prefix = _find_prefix_and_nick_in_components(
                payload_kwargs["components"]
            )
        target_type = "user" if isinstance(target, (discord.User, discord.Member)) else "channel"
        payload = {
            "target_type": target_type,
            "target_id": target.id,
            "kwargs": payload_kwargs,
            "has_verified_clan_prefix": has_verified_clan_prefix,
        }
        if persist:
            persist = self._is_serializable(payload)
        return payload, persist

    def _build_message_payload(self, message: discord.Message, kwargs: dict[str, Any]):
        payload_kwargs = kwargs.copy()
        if "embed" in payload_kwargs and payload_kwargs["embed"] is not None:
            payload_kwargs["embeds"] = [payload_kwargs.pop("embed")]
        self._sanitize_view_kwargs(payload_kwargs)
        self._sanitize_components_v2_kwargs(payload_kwargs)
        persist = True
        if payload_kwargs.get("view") is not None:
            persist = False
        has_verified_clan_prefix = False
        if payload_kwargs.get("components") is not None:
            has_verified_clan_prefix = _find_prefix_and_nick_in_components(
                payload_kwargs["components"]
            )
        payload = {
            "channel_id": message.channel.id,
            "message_id": message.id,
            "kwargs": payload_kwargs,
            "has_verified_clan_prefix": has_verified_clan_prefix,
        }
        if persist:
            persist = self._is_serializable(payload)
        return payload, persist

    def _build_set_permissions_payload(
        self,
        channel: discord.abc.GuildChannel,
        target: discord.abc.Snowflake,
        *,
        overwrite: discord.PermissionOverwrite | None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        target_type = "role"
        if isinstance(target, discord.Member) or isinstance(target, discord.User):
            target_type = "member"
        if isinstance(target, discord.Role):
            target_type = "role"
        payload: dict[str, Any] = {
            "channel_id": channel.id,
            "target_id": target.id,
            "target_type": target_type,
            "reason": reason,
            "overwrite_allow": None,
            "overwrite_deny": None,
        }
        if overwrite is not None:
            allow, deny = overwrite.pair()
            payload["overwrite_allow"] = allow.value
            payload["overwrite_deny"] = deny.value
        return payload

    async def _resolve_target(self, payload: dict[str, Any]):
        if payload["target_type"] == "user":
            return await self._resolve_user(payload["target_id"])
        return await self._resolve_channel(payload["target_id"])

    async def _resolve_channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            return await self.bot.fetch_channel(channel_id)
        except (discord.Forbidden, discord.HTTPException):
            return None

    async def _resolve_user(self, user_id: int):
        user = self.bot.get_user(user_id)
        if user is not None:
            return user
        try:
            return await self.bot.fetch_user(user_id)
        except (discord.NotFound, discord.HTTPException):
            return None

    async def _resolve_message(self, payload: dict[str, Any]):
        channel = await self._resolve_channel(payload["channel_id"])
        if channel is None:
            return None
        if isinstance(channel, discord.TextChannel) or isinstance(channel, discord.Thread):
            message = channel.get_partial_message(payload["message_id"])
            return message
        return None

    async def _resolve_member(self, payload: dict[str, Any]):
        guild = self.bot.get_guild(payload["guild_id"])
        if guild is None:
            return None
        member = guild.get_member(payload["member_id"])
        if member is not None:
            return member
        try:
            return await guild.fetch_member(payload["member_id"])
        except (discord.NotFound, discord.HTTPException):
            return None

    async def _resolve_permission_target(
        self, channel: discord.abc.GuildChannel, payload: dict[str, Any]
    ):
        target_type = payload.get("target_type")
        target_id = payload.get("target_id")
        if not target_type or target_id is None:
            return None
        if target_type == "role":
            return channel.guild.get_role(target_id)
        if target_type == "member":
            member = channel.guild.get_member(target_id)
            if member is not None:
                return member
            try:
                return await channel.guild.fetch_member(target_id)
            except (discord.NotFound, discord.HTTPException):
                return None
        return None

    def _resolve_roles(self, guild: discord.Guild, role_ids: list[int]):
        roles: list[discord.Role] = []
        for role_id in role_ids:
            role = guild.get_role(role_id)
            if role is not None:
                roles.append(role)
        return roles

    def _get_channel_original(
        self,
        channel: discord.abc.GuildChannel,
        originals: dict[type, Callable[..., Any]],
        op_name: str,
    ):
        for cls, original in originals.items():
            if isinstance(channel, cls):
                return original
        self.logger.warning("Nenalezen originální %s pro kanál %s.", op_name, type(channel).__name__)
        return None

    def _get_messageable_original(self, target: discord.abc.Messageable):
        for cls, original in self._messageable_send_originals.items():
            if isinstance(target, cls):
                return original
        self.logger.warning("Nenalezen originální send pro %s.", type(target).__name__)
        return None
