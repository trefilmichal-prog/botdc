import asyncio
import contextlib
import logging

import discord
from discord import app_commands
from discord.ext import commands

from db import get_log_channel_id, set_log_channel_id

LOG_CHANNEL_ID = 1440046748088402064


class LoggingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("botdc")
        self.logger.setLevel(logging.INFO)
        self.log_channel_id = get_log_channel_id() or LOG_CHANNEL_ID

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
            description="Nastavení logovacího kanálu.",
            default_permissions=discord.Permissions(administrator=True),
        )
        self.log_settings_group.command(
            name="set_channel",
            description="Nastaví kanál, kam se budou posílat logy bota.",
        )(self.set_log_channel)
        self.log_settings_group.command(
            name="set_channel_id",
            description="Nastaví ID kanálu (včetně vláken) pro logy bota.",
        )(self.set_log_channel_id)
        self.log_settings_group.command(
            name="show",
            description="Zobrazí aktuálně nastavený logovací kanál.",
        )(self.show_log_channel)
        self.__cog_app_commands__ = []

    async def cog_load(self):
        # Start log processing once the cog is fully loaded and a loop is running.
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

    async def _get_log_channel(self) -> discord.TextChannel | discord.Thread | None:
        channel = self.bot.get_channel(self.log_channel_id)
        if channel is not None:
            if isinstance(channel, (discord.TextChannel, discord.Thread)):
                return channel
            return None

        try:
            fetched = await self.bot.fetch_channel(self.log_channel_id)
        except (discord.Forbidden, discord.HTTPException):
            return None

        if isinstance(fetched, (discord.TextChannel, discord.Thread)):
            return fetched
        return None

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

        channel = await self._get_log_channel()
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

        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.Container(*(discord.ui.TextDisplay(content=line) for line in lines))
        )
        await channel.send(
            content="",
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.guild is None or before.author.bot:
            return

        if before.content == after.content:
            return

        channel = await self._get_log_channel()
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
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.Container(*(discord.ui.TextDisplay(content=line) for line in lines))
        )
        await channel.send(
            content="",
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _process_log_queue(self):
        try:
            await self.bot.wait_until_ready()
            while True:
                log_entry = await self.log_queue.get()
                try:
                    await self._send_log_embed(log_entry)
                except Exception:
                    self.logger.exception("Nepodařilo se odeslat log do kanálu")
        except asyncio.CancelledError:
            pass

    async def _send_log_embed(self, message: str):
        channel = await self._get_log_channel()
        if channel is None:
            return

        safe_message = self._safe_textdisplay_content(message)
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(content="## Log bota"),
                discord.ui.TextDisplay(content=safe_message),
            )
        )
        await channel.send(
            content="",
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    def _build_view(self, lines: list[str]) -> discord.ui.LayoutView:
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.Container(*(discord.ui.TextDisplay(content=line) for line in lines))
        )
        return view

    def _safe_textdisplay_content(self, value: object) -> str:
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

    def _update_log_channel_id(self, channel_id: int) -> None:
        self.log_channel_id = channel_id
        set_log_channel_id(channel_id)

    async def set_log_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        self._update_log_channel_id(channel.id)
        view = self._build_view(
            [
                "## Logovací kanál nastaven",
                f"Nový kanál: {channel.mention} ({channel.id})",
            ]
        )
        await interaction.response.send_message(content="", view=view, ephemeral=True)

    async def set_log_channel_id(
        self, interaction: discord.Interaction, channel_id: int
    ):
        channel = interaction.guild.get_channel(channel_id) if interaction.guild else None
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.Forbidden, discord.HTTPException):
                channel = None

        if channel is None or not isinstance(channel, (discord.TextChannel, discord.Thread)):
            view = self._build_view(
                [
                    "## Nelze nastavit logovací kanál",
                    "Zadané ID neodpovídá textovému kanálu ani vláknu.",
                ]
            )
            await interaction.response.send_message(
                content="", view=view, ephemeral=True
            )
            return

        self._update_log_channel_id(channel.id)
        view = self._build_view(
            [
                "## Logovací kanál nastaven",
                f"Nový kanál: {channel.mention} ({channel.id})",
            ]
        )
        await interaction.response.send_message(content="", view=view, ephemeral=True)

    async def show_log_channel(self, interaction: discord.Interaction):
        channel = await self._get_log_channel()
        if channel is None:
            lines = [
                "## Logovací kanál nenalezen",
                f"Aktuálně uložené ID: {self.log_channel_id}",
            ]
        else:
            lines = [
                "## Aktuální logovací kanál",
                f"Kanál: {channel.mention} ({channel.id})",
            ]
        view = self._build_view(lines)
        await interaction.response.send_message(content="", view=view, ephemeral=True)


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
