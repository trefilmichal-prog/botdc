import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import discord
from discord.ext import commands

from config import DISCORD_WRITE_MIN_INTERVAL_SECONDS
from db import (
    enqueue_discord_write,
    fetch_pending_discord_writes,
    mark_discord_write_done,
    mark_discord_write_failed,
)


@dataclass
class WriteRequest:
    operation: str
    payload: dict[str, Any]
    persist: bool
    future: asyncio.Future | None
    db_id: int | None
    attempts: int = 0


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


async def _patched_messageable_send(target: discord.abc.Messageable, *args, **kwargs):
    writer = get_writer(_get_client_from_state(target))
    return await writer.send_message(target, *args, **kwargs)


async def _patched_message_edit(message: discord.Message, *args, **kwargs):
    writer = get_writer(_get_client_from_state(message))
    return await writer.edit_message(message, *args, **kwargs)


async def _patched_message_delete(message: discord.Message, *args, **kwargs):
    writer = get_writer(_get_client_from_state(message))
    return await writer.delete_message(message, *args, **kwargs)


async def _patched_channel_edit(channel: discord.abc.GuildChannel, *args, **kwargs):
    writer = get_writer(_get_client_from_state(channel))
    return await writer.edit_channel(channel, *args, **kwargs)


async def _patched_channel_delete(channel: discord.abc.GuildChannel, *args, **kwargs):
    writer = get_writer(_get_client_from_state(channel))
    return await writer.delete_channel(channel, *args, **kwargs)


async def _patched_create_text_channel(guild: discord.Guild, *args, **kwargs):
    writer = get_writer(_get_client_from_state(guild))
    return await writer.create_text_channel(guild, *args, **kwargs)


async def _patched_member_add_roles(member: discord.Member, *roles, **kwargs):
    writer = get_writer(_get_client_from_state(member))
    return await writer.add_roles(member, *roles, **kwargs)


async def _patched_member_remove_roles(member: discord.Member, *roles, **kwargs):
    writer = get_writer(_get_client_from_state(member))
    return await writer.remove_roles(member, *roles, **kwargs)


async def _patched_guild_ban(guild: discord.Guild, user: discord.abc.User, **kwargs):
    writer = get_writer(_get_client_from_state(guild))
    return await writer.ban_member(guild, user, **kwargs)


async def _patched_member_kick(member: discord.Member, **kwargs):
    writer = get_writer(_get_client_from_state(member))
    return await writer.kick_member(member, **kwargs)


async def _patched_member_timeout(member: discord.Member, until, **kwargs):
    writer = get_writer(_get_client_from_state(member))
    return await writer.timeout_member(member, until, **kwargs)


async def _patched_member_edit(member: discord.Member, **kwargs):
    writer = get_writer(_get_client_from_state(member))
    return await writer.edit_member(member, **kwargs)


async def _patched_interaction_send(response: discord.InteractionResponse, *args, **kwargs):
    interaction = response._parent
    writer = get_writer(interaction.client)
    return await writer.send_interaction_response(interaction, *args, **kwargs)


async def _patched_interaction_defer(response: discord.InteractionResponse, *args, **kwargs):
    interaction = response._parent
    writer = get_writer(interaction.client)
    return await writer.defer_interaction(interaction, *args, **kwargs)


async def _patched_interaction_edit(response: discord.InteractionResponse, *args, **kwargs):
    interaction = response._parent
    writer = get_writer(interaction.client)
    return await writer.edit_interaction_response(interaction, *args, **kwargs)


async def _patched_interaction_modal(response: discord.InteractionResponse, *args, **kwargs):
    interaction = response._parent
    writer = get_writer(interaction.client)
    return await writer.send_interaction_modal(interaction, *args, **kwargs)


async def _patched_webhook_send(webhook: discord.Webhook, *args, **kwargs):
    writer = get_writer(_get_client_from_state(webhook))
    return await writer.send_webhook_message(webhook, *args, **kwargs)


class DiscordWriteCoordinatorCog(commands.Cog, name="DiscordWriteCoordinator"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("botdc.discord_write")
        self._queue: asyncio.Queue[WriteRequest] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._last_write_at: float | None = None
        self._min_interval_seconds = DISCORD_WRITE_MIN_INTERVAL_SECONDS
        self._patched = False
        self._original_messageable_send = discord.abc.Messageable.send
        self._original_message_edit = discord.Message.edit
        self._original_message_delete = discord.Message.delete
        self._original_channel_edit = discord.abc.GuildChannel.edit
        self._original_channel_delete = discord.abc.GuildChannel.delete
        self._original_create_text_channel = discord.Guild.create_text_channel
        self._original_add_roles = discord.Member.add_roles
        self._original_remove_roles = discord.Member.remove_roles
        self._original_ban = discord.Guild.ban
        self._original_kick = discord.Member.kick
        self._original_timeout = discord.Member.timeout
        self._original_member_edit = discord.Member.edit
        self._original_interaction_send = discord.InteractionResponse.send_message
        self._original_interaction_defer = discord.InteractionResponse.defer
        self._original_interaction_edit = discord.InteractionResponse.edit_message
        self._original_interaction_modal = discord.InteractionResponse.send_modal
        self._original_webhook_send = discord.Webhook.send

    async def cog_load(self):
        await self._restore_pending()
        self._patch_methods()
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def cog_unload(self):
        if self._worker_task:
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
        self._restore_methods()

    def _patch_methods(self):
        if self._patched:
            return
        discord.abc.Messageable.send = _patched_messageable_send
        discord.Message.edit = _patched_message_edit
        discord.Message.delete = _patched_message_delete
        discord.abc.GuildChannel.edit = _patched_channel_edit
        discord.abc.GuildChannel.delete = _patched_channel_delete
        discord.Guild.create_text_channel = _patched_create_text_channel
        discord.Member.add_roles = _patched_member_add_roles
        discord.Member.remove_roles = _patched_member_remove_roles
        discord.Guild.ban = _patched_guild_ban
        discord.Member.kick = _patched_member_kick
        discord.Member.timeout = _patched_member_timeout
        discord.Member.edit = _patched_member_edit
        discord.InteractionResponse.send_message = _patched_interaction_send
        discord.InteractionResponse.defer = _patched_interaction_defer
        discord.InteractionResponse.edit_message = _patched_interaction_edit
        discord.InteractionResponse.send_modal = _patched_interaction_modal
        discord.Webhook.send = _patched_webhook_send
        self._patched = True

    def _restore_methods(self):
        if not self._patched:
            return
        discord.abc.Messageable.send = self._original_messageable_send
        discord.Message.edit = self._original_message_edit
        discord.Message.delete = self._original_message_delete
        discord.abc.GuildChannel.edit = self._original_channel_edit
        discord.abc.GuildChannel.delete = self._original_channel_delete
        discord.Guild.create_text_channel = self._original_create_text_channel
        discord.Member.add_roles = self._original_add_roles
        discord.Member.remove_roles = self._original_remove_roles
        discord.Guild.ban = self._original_ban
        discord.Member.kick = self._original_kick
        discord.Member.timeout = self._original_timeout
        discord.Member.edit = self._original_member_edit
        discord.InteractionResponse.send_message = self._original_interaction_send
        discord.InteractionResponse.defer = self._original_interaction_defer
        discord.InteractionResponse.edit_message = self._original_interaction_edit
        discord.InteractionResponse.send_modal = self._original_interaction_modal
        discord.Webhook.send = self._original_webhook_send
        self._patched = False

    async def _restore_pending(self):
        for item in fetch_pending_discord_writes():
            try:
                payload = json.loads(item["payload"])
            except json.JSONDecodeError:
                self.logger.error(
                    "Nelze načíst payload pro discord zápis %s", item["id"]
                )
                continue
            await self._queue.put(
                WriteRequest(
                    operation=item["operation"],
                    payload=payload,
                    persist=True,
                    future=None,
                    db_id=item["id"],
                )
            )

    async def _worker_loop(self):
        while True:
            request = await self._queue.get()
            await self._respect_rate_limit()
            try:
                result = await self._execute_request(request)
            except discord.HTTPException as exc:
                if exc.status == 429:
                    retry_after = getattr(exc, "retry_after", None)
                    delay = retry_after or self._min_interval_seconds
                    self.logger.warning(
                        "Rate limit hit, čekám %.2fs před opakováním.", delay
                    )
                    await asyncio.sleep(delay)
                    request.attempts += 1
                    await self._queue.put(request)
                    continue
                self._mark_failed(request, exc)
                continue
            except Exception as exc:  # noqa: BLE001
                self._mark_failed(request, exc)
                continue

            self._last_write_at = asyncio.get_running_loop().time()
            if request.persist and request.db_id is not None:
                mark_discord_write_done(request.db_id)
            if request.future and not request.future.done():
                request.future.set_result(result)

    async def _respect_rate_limit(self):
        if self._last_write_at is None:
            return
        now = asyncio.get_running_loop().time()
        wait_for = self._min_interval_seconds - (now - self._last_write_at)
        if wait_for > 0:
            await asyncio.sleep(wait_for)

    def _mark_failed(self, request: WriteRequest, exc: Exception):
        self.logger.exception("Discord write selhal: %s", request.operation)
        if request.persist and request.db_id is not None:
            mark_discord_write_failed(request.db_id, repr(exc))
        if request.future and not request.future.done():
            request.future.set_exception(exc)

    async def _execute_request(self, request: WriteRequest):
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
        raise ValueError(f"Neznámá operace: {op}")

    async def _op_send_message(self, payload: dict[str, Any]):
        target = await self._resolve_target(payload)
        if target is None:
            raise RuntimeError("Cíl zprávy nebyl nalezen.")
        return await self._original_messageable_send(target, **payload["kwargs"])

    async def _op_edit_message(self, payload: dict[str, Any]):
        message = await self._resolve_message(payload)
        if message is None:
            raise RuntimeError("Zpráva k úpravě nebyla nalezena.")
        return await self._original_message_edit(message, **payload["kwargs"])

    async def _op_delete_message(self, payload: dict[str, Any]):
        message = await self._resolve_message(payload)
        if message is None:
            raise RuntimeError("Zpráva ke smazání nebyla nalezena.")
        return await self._original_message_delete(message, **payload["kwargs"])

    async def _op_edit_channel(self, payload: dict[str, Any]):
        channel = await self._resolve_channel(payload["channel_id"])
        if channel is None:
            raise RuntimeError("Kanál k úpravě nebyl nalezen.")
        return await self._original_channel_edit(channel, **payload["kwargs"])

    async def _op_delete_channel(self, payload: dict[str, Any]):
        channel = await self._resolve_channel(payload["channel_id"])
        if channel is None:
            raise RuntimeError("Kanál ke smazání nebyl nalezen.")
        return await self._original_channel_delete(channel, **payload["kwargs"])

    async def _op_create_text_channel(self, payload: dict[str, Any]):
        guild = self.bot.get_guild(payload["guild_id"])
        if guild is None:
            raise RuntimeError("Guild nebyla nalezena.")
        return await self._original_create_text_channel(
            guild, payload["name"], **payload["kwargs"]
        )

    async def _op_add_roles(self, payload: dict[str, Any]):
        member = await self._resolve_member(payload)
        if member is None:
            raise RuntimeError("Člen nebyl nalezen pro přidání role.")
        roles = self._resolve_roles(member.guild, payload["role_ids"])
        return await self._original_add_roles(member, *roles, reason=payload.get("reason"))

    async def _op_remove_roles(self, payload: dict[str, Any]):
        member = await self._resolve_member(payload)
        if member is None:
            raise RuntimeError("Člen nebyl nalezen pro odebrání role.")
        roles = self._resolve_roles(member.guild, payload["role_ids"])
        return await self._original_remove_roles(
            member, *roles, reason=payload.get("reason")
        )

    async def _op_ban_member(self, payload: dict[str, Any]):
        guild = self.bot.get_guild(payload["guild_id"])
        if guild is None:
            raise RuntimeError("Guild nebyla nalezena pro ban.")
        user = await self._resolve_user(payload["user_id"])
        if user is None:
            raise RuntimeError("Uživatel nebyl nalezen pro ban.")
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
        return await self._original_kick(member, reason=payload.get("reason"))

    async def _op_timeout_member(self, payload: dict[str, Any]):
        member = await self._resolve_member(payload)
        if member is None:
            raise RuntimeError("Člen nebyl nalezen pro timeout.")
        return await self._original_timeout(
            member, payload["until"], reason=payload.get("reason")
        )

    async def _op_edit_member(self, payload: dict[str, Any]):
        member = await self._resolve_member(payload)
        if member is None:
            raise RuntimeError("Člen nebyl nalezen pro editaci.")
        return await self._original_member_edit(member, **payload["kwargs"])

    async def _op_interaction_response(self, payload: dict[str, Any]):
        interaction = payload["interaction"]
        return await self._original_interaction_send(
            interaction.response, **payload["kwargs"]
        )

    async def _op_interaction_followup(self, payload: dict[str, Any]):
        interaction = payload["interaction"]
        return await self._original_webhook_send(
            interaction.followup, **payload["kwargs"]
        )

    async def _op_interaction_edit(self, payload: dict[str, Any]):
        interaction = payload["interaction"]
        return await self._original_interaction_edit(
            interaction.response, **payload["kwargs"]
        )

    async def _op_interaction_defer(self, payload: dict[str, Any]):
        interaction = payload["interaction"]
        return await self._original_interaction_defer(
            interaction.response, **payload["kwargs"]
        )

    async def _op_interaction_modal(self, payload: dict[str, Any]):
        interaction = payload["interaction"]
        return await self._original_interaction_modal(
            interaction.response, payload["modal"]
        )

    async def _op_webhook_send(self, payload: dict[str, Any]):
        webhook = payload["webhook"]
        return await self._original_webhook_send(webhook, **payload["kwargs"])

    async def send_message(self, target: discord.abc.Messageable, **kwargs):
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
        return await self._enqueue("edit_channel", payload, persist)

    async def delete_channel(self, channel: discord.abc.GuildChannel, **kwargs):
        payload = {"channel_id": channel.id, "kwargs": kwargs}
        persist = self._is_serializable(kwargs)
        return await self._enqueue("delete_channel", payload, persist)

    async def create_text_channel(self, guild: discord.Guild, name: str, **kwargs):
        payload = {"guild_id": guild.id, "name": name, "kwargs": kwargs}
        persist = self._is_serializable(kwargs)
        return await self._enqueue("create_text_channel", payload, persist)

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
        return await self._enqueue("ban_member", payload, persist=True)

    async def kick_member(self, member: discord.Member, reason: str | None = None):
        payload = {
            "guild_id": member.guild.id,
            "member_id": member.id,
            "reason": reason,
        }
        return await self._enqueue("kick_member", payload, persist=True)

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
        return await self._enqueue("timeout_member", payload, persist)

    async def edit_member(self, member: discord.Member, **kwargs):
        payload = {
            "guild_id": member.guild.id,
            "member_id": member.id,
            "kwargs": kwargs,
        }
        persist = self._is_serializable(kwargs)
        return await self._enqueue("edit_member", payload, persist)

    async def send_interaction_response(self, interaction: discord.Interaction, **kwargs):
        payload = {"interaction": interaction, "kwargs": kwargs}
        return await self._enqueue("interaction_response", payload, persist=False)

    async def send_interaction_followup(self, interaction: discord.Interaction, **kwargs):
        payload = {"interaction": interaction, "kwargs": kwargs}
        return await self._enqueue("interaction_followup", payload, persist=False)

    async def edit_interaction_response(self, interaction: discord.Interaction, **kwargs):
        payload = {"interaction": interaction, "kwargs": kwargs}
        return await self._enqueue("interaction_edit", payload, persist=False)

    async def defer_interaction(self, interaction: discord.Interaction, **kwargs):
        payload = {"interaction": interaction, "kwargs": kwargs}
        return await self._enqueue("interaction_defer", payload, persist=False)

    async def send_interaction_modal(
        self, interaction: discord.Interaction, modal: discord.ui.Modal
    ):
        payload = {"interaction": interaction, "modal": modal}
        return await self._enqueue("interaction_modal", payload, persist=False)

    async def send_webhook_message(self, webhook: discord.Webhook, **kwargs):
        payload = {"webhook": webhook, "kwargs": kwargs}
        return await self._enqueue("webhook_send", payload, persist=False)

    async def _enqueue(self, operation: str, payload: dict[str, Any], persist: bool):
        db_id = None
        if persist:
            stored_payload = self._serialize_payload(payload)
            db_id = enqueue_discord_write(operation, stored_payload)
        future = asyncio.get_running_loop().create_future()
        await self._queue.put(
            WriteRequest(
                operation=operation,
                payload=payload,
                persist=persist,
                future=future,
                db_id=db_id,
            )
        )
        return await future

    def _serialize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        serialized: dict[str, Any] = {}
        for key, value in payload.items():
            if key in {"interaction", "modal", "webhook"}:
                continue
            serialized[key] = self._serialize_item(value)
        return serialized

    def _serialize_item(self, item: Any):
        if isinstance(item, discord.Embed):
            return {"__embed__": item.to_dict()}
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
        if isinstance(item, dict) and "__embed__" in item:
            return discord.Embed.from_dict(item["__embed__"])
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

    def _build_send_payload(self, target: discord.abc.Messageable, kwargs: dict[str, Any]):
        payload_kwargs = kwargs.copy()
        persist = True
        if "embed" in payload_kwargs and payload_kwargs["embed"] is not None:
            payload_kwargs["embeds"] = [payload_kwargs.pop("embed")]
        if payload_kwargs.get("view") is not None:
            persist = False
        if payload_kwargs.get("file") is not None or payload_kwargs.get("files") is not None:
            persist = False
        if payload_kwargs.get("allowed_mentions") is not None:
            persist = False
        target_type = "user" if isinstance(target, (discord.User, discord.Member)) else "channel"
        payload = {
            "target_type": target_type,
            "target_id": target.id,
            "kwargs": payload_kwargs,
        }
        if persist:
            persist = self._is_serializable(payload)
        return payload, persist

    def _build_message_payload(self, message: discord.Message, kwargs: dict[str, Any]):
        payload_kwargs = kwargs.copy()
        persist = True
        if "embed" in payload_kwargs and payload_kwargs["embed"] is not None:
            payload_kwargs["embeds"] = [payload_kwargs.pop("embed")]
        if payload_kwargs.get("view") is not None:
            persist = False
        payload = {
            "channel_id": message.channel.id,
            "message_id": message.id,
            "kwargs": payload_kwargs,
        }
        if persist:
            persist = self._is_serializable(payload)
        return payload, persist

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

    def _resolve_roles(self, guild: discord.Guild, role_ids: list[int]):
        roles: list[discord.Role] = []
        for role_id in role_ids:
            role = guild.get_role(role_id)
            if role is not None:
                roles.append(role)
        return roles
