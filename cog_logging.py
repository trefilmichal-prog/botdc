import asyncio
import contextlib
import logging

import discord
from discord import app_commands
from discord.ext import commands

from db import (
    get_audit_log_channel_id,
    get_error_log_channel_id,
    get_log_channel_id,
    set_audit_log_channel_id,
    set_error_log_channel_id,
)

LOG_CHANNEL_ID = 1440046748088402064
MAX_TEXTDISPLAY_PAYLOAD_LENGTH = 4000
TRUNCATION_SUFFIX = "… (zkráceno)"


class LoggingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("botdc")
        self.logger.setLevel(logging.INFO)

        legacy_log_channel_id = get_log_channel_id()
        stored_error_log_channel_id = get_error_log_channel_id()
        stored_audit_log_channel_id = get_audit_log_channel_id()

        if stored_error_log_channel_id is None:
            stored_error_log_channel_id = legacy_log_channel_id
        if stored_audit_log_channel_id is None:
            stored_audit_log_channel_id = legacy_log_channel_id

        self.error_log_channel_id = (
            LOG_CHANNEL_ID
            if stored_error_log_channel_id is None
            else stored_error_log_channel_id
        )
        self.audit_log_channel_id = (
            LOG_CHANNEL_ID
            if stored_audit_log_channel_id is None
            else stored_audit_log_channel_id
        )

        self.log_queue: asyncio.Queue[str] = asyncio.Queue()
        self.log_task: asyncio.Task[None] | None = None

        self._handler = _ChannelLogHandler(self)
        self._handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        self._handler.setLevel(logging.INFO)

        root_logger = logging.getLogger()
        for existing_handler in list(root_logger.handlers):
            if isinstance(existing_handler, _ChannelLogHandler):
                root_logger.removeHandler(existing_handler)
        if self._handler not in root_logger.handlers:
            root_logger.addHandler(self._handler)

        self.log_settings_group = app_commands.Group(
            name="log_settings",
            description="Nastavení logování bota.",
            default_permissions=discord.Permissions(administrator=True),
        )

        self.error_group = app_commands.Group(
            name="errors",
            description="Nastavení logování chyb a systémových událostí.",
        )
        self.error_group.command(
            name="set_channel",
            description="Nastaví kanál pro logy chyb bota.",
        )(self.errors_set_channel)
        self.error_group.command(
            name="set_channel_id",
            description="Nastaví ID kanálu (včetně vláken) pro logy chyb bota.",
        )(self.errors_set_channel_id)
        self.error_group.command(
            name="show",
            description="Zobrazí aktuální kanál pro logy chyb.",
        )(self.errors_show)
        self.error_group.command(
            name="disable",
            description="Vypne posílání logů chyb do Discord kanálu.",
        )(self.errors_disable)

        self.audit_group = app_commands.Group(
            name="audit",
            description="Nastavení audit logů (úprava/smazání zpráv).",
        )
        self.audit_group.command(
            name="set_channel",
            description="Nastaví kanál pro audit logy zpráv.",
        )(self.audit_set_channel)
        self.audit_group.command(
            name="set_channel_id",
            description="Nastaví ID kanálu (včetně vláken) pro audit logy zpráv.",
        )(self.audit_set_channel_id)
        self.audit_group.command(
            name="show",
            description="Zobrazí aktuální audit log kanál.",
        )(self.audit_show)
        self.audit_group.command(
            name="disable",
            description="Vypne posílání audit logů do Discord kanálu.",
        )(self.audit_disable)

        self.log_settings_group.add_command(self.error_group)
        self.log_settings_group.add_command(self.audit_group)
        self.log_settings_group.command(
            name="show",
            description="Zobrazí souhrn nastavení logování.",
        )(self.show_log_channels)
        self.log_settings_group.command(
            name="disable",
            description="Vypne chybové i audit logy do Discord kanálu.",
        )(self.disable_all_log_channels)
        self.__cog_app_commands__ = []

    async def cog_load(self):
        loop = asyncio.get_running_loop()
        self.log_task = loop.create_task(self._process_log_queue())
        existing_group = self.bot.tree.get_command(
            "log_settings", type=discord.AppCommandType.chat_input
        )
        if existing_group:
            self.bot.tree.remove_command(
                "log_settings", type=discord.AppCommandType.chat_input
            )
        try:
            self.bot.tree.add_command(self.log_settings_group)
        except app_commands.CommandAlreadyRegistered:
            pass

    async def cog_unload(self):
        root_logger = logging.getLogger()
        if self._handler in root_logger.handlers:
            root_logger.removeHandler(self._handler)
        if self.log_task is not None:
            self.log_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.log_task
        existing_group = self.bot.tree.get_command(
            "log_settings", type=discord.AppCommandType.chat_input
        )
        if existing_group:
            self.bot.tree.remove_command(
                "log_settings", type=discord.AppCommandType.chat_input
            )

    async def _resolve_log_channel(
        self, channel_id: int
    ) -> discord.TextChannel | discord.Thread | None:
        if channel_id <= 0:
            return None

        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            if isinstance(channel, (discord.TextChannel, discord.Thread)):
                return channel
            return None

        try:
            fetched = await self.bot.fetch_channel(channel_id)
        except (discord.Forbidden, discord.HTTPException):
            return None

        if isinstance(fetched, (discord.TextChannel, discord.Thread)):
            return fetched
        return None

    async def _get_error_log_channel(self) -> discord.TextChannel | discord.Thread | None:
        return await self._resolve_log_channel(self.error_log_channel_id)

    async def _get_audit_log_channel(self) -> discord.TextChannel | discord.Thread | None:
        return await self._resolve_log_channel(self.audit_log_channel_id)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return

        self.logger.info(
            "Zpráva odstraněna: autor=%s kanál=%s obsah=%s",
            message.author,
            getattr(message.channel, "name", message.channel),
            (message.content or "*(Žádný text)*").replace("\n", " "),
            extra={"skip_channel": True},
        )

        channel = await self._get_audit_log_channel()
        if channel is None:
            return

        description_lines = [
            f"Autor: {message.author} ({message.author.id})",
            f"Kanál: #{message.channel} ({message.channel.id})",
        ]

        content = message.content or "*(Žádný text)*"
        lines = [
            "## Zpráva odstraněna",
            "\n".join(description_lines),
            f"**Obsah:** {content[:1024]}",
        ]

        attachments = [attachment.url for attachment in message.attachments]
        if attachments:
            lines.append("**Přílohy:**\n" + "\n".join(attachments)[:1024])

        view = self._build_view(lines)
        await channel.send(
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.guild is None or before.author.bot:
            return

        if before.content == after.content:
            return

        channel = await self._get_audit_log_channel()
        if channel is None:
            return

        self.logger.info(
            "Zpráva upravena: autor=%s kanál=%s původní=%s nový=%s",
            before.author,
            getattr(before.channel, "name", before.channel),
            (before.content or "*(Žádný text)*").replace("\n", " "),
            (after.content or "*(Žádný text)*").replace("\n", " "),
            extra={"skip_channel": True},
        )

        description_lines = [
            f"Autor: {before.author} ({before.author.id})",
            f"Kanál: #{before.channel} ({before.channel.id})",
            f"Zpráva: [Odkaz]({after.jump_url})",
        ]

        lines = [
            "## Zpráva upravena",
            "\n".join(description_lines),
            f"**Původní text:** {(before.content or '*(Žádný text)*')[:1024]}",
            f"**Nový text:** {(after.content or '*(Žádný text)*')[:1024]}",
        ]
        view = self._build_view(lines)
        await channel.send(
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _process_log_queue(self):
        try:
            await self.bot.wait_until_ready()
            while True:
                log_entry = await self.log_queue.get()
                try:
                    await self._send_log_entry(log_entry)
                except Exception:
                    self.logger.exception("Nepodařilo se odeslat log do kanálu")
        except asyncio.CancelledError:
            pass

    async def _send_log_entry(self, message: str):
        channel = await self._get_error_log_channel()
        if channel is None:
            return

        payload_lines = self._fit_textdisplay_payload(["## Log bota", message])
        view = self._build_view(payload_lines)
        await channel.send(
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    def _build_view(self, lines: list[str]) -> discord.ui.LayoutView:
        safe_lines = self._fit_textdisplay_payload(lines)
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.Container(
                *(discord.ui.TextDisplay(content=line) for line in safe_lines)
            )
        )
        return view

    def _fit_textdisplay_payload(self, values: list[object]) -> list[str]:
        safe_values = [self._safe_textdisplay_content(value) for value in values]
        adjusted_values: list[str] = []
        used_chars = 0

        for value in safe_values:
            remaining = MAX_TEXTDISPLAY_PAYLOAD_LENGTH - used_chars
            if remaining <= 0:
                break

            if len(value) <= remaining:
                adjusted_values.append(value)
                used_chars += len(value)
                continue

            adjusted_values.append(self._safe_textdisplay_content(value, limit=remaining))
            break

        if not adjusted_values:
            return ["\u200b"]
        return adjusted_values

    def _safe_textdisplay_content(
        self, value: object, limit: int = MAX_TEXTDISPLAY_PAYLOAD_LENGTH
    ) -> str:
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
        if len(text) > limit:
            if len(TRUNCATION_SUFFIX) >= limit:
                return text[:limit]
            return f"{text[:limit - len(TRUNCATION_SUFFIX)]}{TRUNCATION_SUFFIX}"
        return text

    async def _resolve_channel_input(
        self, interaction: discord.Interaction, channel_id: int
    ) -> discord.TextChannel | discord.Thread | None:
        channel = interaction.guild.get_channel(channel_id) if interaction.guild else None
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.Forbidden, discord.HTTPException):
                return None

        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return None
        return channel

    async def errors_set_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        self.error_log_channel_id = channel.id
        set_error_log_channel_id(channel.id)
        view = self._build_view(
            [
                "## Chybový log kanál nastaven",
                f"Nový kanál: {channel.mention} ({channel.id})",
            ]
        )
        await interaction.response.send_message(view=view, ephemeral=True)

    async def errors_set_channel_id(
        self, interaction: discord.Interaction, channel_id: int
    ):
        channel = await self._resolve_channel_input(interaction, channel_id)
        if channel is None:
            view = self._build_view(
                [
                    "## Nelze nastavit chybový log kanál",
                    "Zadané ID neodpovídá textovému kanálu ani vláknu.",
                ]
            )
            await interaction.response.send_message(view=view, ephemeral=True)
            return

        self.error_log_channel_id = channel.id
        set_error_log_channel_id(channel.id)
        view = self._build_view(
            [
                "## Chybový log kanál nastaven",
                f"Nový kanál: {channel.mention} ({channel.id})",
            ]
        )
        await interaction.response.send_message(view=view, ephemeral=True)

    async def errors_disable(self, interaction: discord.Interaction):
        self.error_log_channel_id = 0
        set_error_log_channel_id(0)
        view = self._build_view(
            [
                "## Chybové logování vypnuto",
                "Chyby bota se do Discord kanálu neposílají.",
            ]
        )
        await interaction.response.send_message(view=view, ephemeral=True)

    async def errors_show(self, interaction: discord.Interaction):
        channel = await self._get_error_log_channel()
        if self.error_log_channel_id <= 0:
            lines = [
                "## Chybové logování je vypnuto",
                "Logy chyb se odesílají jen do interní fronty handleru.",
            ]
        elif channel is None:
            lines = [
                "## Chybový log kanál nenalezen",
                f"Aktuálně uložené ID: {self.error_log_channel_id}",
            ]
        else:
            lines = [
                "## Chybový log kanál",
                f"Kanál: {channel.mention} ({channel.id})",
            ]
        view = self._build_view(lines)
        await interaction.response.send_message(view=view, ephemeral=True)

    async def audit_set_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        self.audit_log_channel_id = channel.id
        set_audit_log_channel_id(channel.id)
        view = self._build_view(
            [
                "## Audit log kanál nastaven",
                f"Nový kanál: {channel.mention} ({channel.id})",
            ]
        )
        await interaction.response.send_message(view=view, ephemeral=True)

    async def audit_set_channel_id(
        self, interaction: discord.Interaction, channel_id: int
    ):
        channel = await self._resolve_channel_input(interaction, channel_id)
        if channel is None:
            view = self._build_view(
                [
                    "## Nelze nastavit audit log kanál",
                    "Zadané ID neodpovídá textovému kanálu ani vláknu.",
                ]
            )
            await interaction.response.send_message(view=view, ephemeral=True)
            return

        self.audit_log_channel_id = channel.id
        set_audit_log_channel_id(channel.id)
        view = self._build_view(
            [
                "## Audit log kanál nastaven",
                f"Nový kanál: {channel.mention} ({channel.id})",
            ]
        )
        await interaction.response.send_message(view=view, ephemeral=True)

    async def audit_disable(self, interaction: discord.Interaction):
        self.audit_log_channel_id = 0
        set_audit_log_channel_id(0)
        view = self._build_view(
            [
                "## Audit logování vypnuto",
                "Logy úprav a mazání zpráv se do Discord kanálu neposílají.",
            ]
        )
        await interaction.response.send_message(view=view, ephemeral=True)

    async def audit_show(self, interaction: discord.Interaction):
        channel = await self._get_audit_log_channel()
        if self.audit_log_channel_id <= 0:
            lines = [
                "## Audit logování je vypnuto",
                "Události úprav a mazání zpráv se do Discord kanálu neposílají.",
            ]
        elif channel is None:
            lines = [
                "## Audit log kanál nenalezen",
                f"Aktuálně uložené ID: {self.audit_log_channel_id}",
            ]
        else:
            lines = [
                "## Audit log kanál",
                f"Kanál: {channel.mention} ({channel.id})",
            ]
        view = self._build_view(lines)
        await interaction.response.send_message(view=view, ephemeral=True)

    async def disable_all_log_channels(self, interaction: discord.Interaction):
        self.error_log_channel_id = 0
        self.audit_log_channel_id = 0
        set_error_log_channel_id(0)
        set_audit_log_channel_id(0)
        view = self._build_view(
            [
                "## Všechna Discord logování vypnuta",
                "Bylo vypnuto chybové i audit logování do Discord kanálů.",
            ]
        )
        await interaction.response.send_message(view=view, ephemeral=True)

    async def show_log_channels(self, interaction: discord.Interaction):
        error_channel = await self._get_error_log_channel()
        audit_channel = await self._get_audit_log_channel()

        if self.error_log_channel_id <= 0:
            error_line = "Chybové logy: vypnuto"
        elif error_channel is None:
            error_line = (
                f"Chybové logy: nenalezeno (ID: {self.error_log_channel_id})"
            )
        else:
            error_line = f"Chybové logy: {error_channel.mention} ({error_channel.id})"

        if self.audit_log_channel_id <= 0:
            audit_line = "Audit logy: vypnuto"
        elif audit_channel is None:
            audit_line = (
                f"Audit logy: nenalezeno (ID: {self.audit_log_channel_id})"
            )
        else:
            audit_line = f"Audit logy: {audit_channel.mention} ({audit_channel.id})"

        view = self._build_view([
            "## Nastavení logování",
            error_line,
            audit_line,
        ])
        await interaction.response.send_message(view=view, ephemeral=True)


class _ChannelLogHandler(logging.Handler):
    def __init__(self, cog: LoggingCog):
        super().__init__()
        self.cog = cog

    def emit(self, record: logging.LogRecord):
        if getattr(record, "skip_channel", False):
            return
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return

        loop = self.cog.bot.loop
        if loop is None or loop.is_closed():
            return

        loop.call_soon_threadsafe(self.cog.log_queue.put_nowait, message)
