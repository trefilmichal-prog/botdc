import asyncio
import contextlib
import logging

import discord
from discord.ext import commands

LOG_CHANNEL_ID = 1440046748088402064


class LoggingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger("botdc")
        self.logger.setLevel(logging.INFO)

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
        if self._handler not in root_logger.handlers:
            root_logger.addHandler(self._handler)

    async def cog_load(self):
        # Start log processing once the cog is fully loaded and a loop is running.
        loop = asyncio.get_running_loop()
        self.log_task = loop.create_task(self._process_log_queue())

    async def _get_log_channel(self) -> discord.TextChannel | None:
        channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if channel is not None:
            return channel  # type: ignore[return-value]

        try:
            fetched = await self.bot.fetch_channel(LOG_CHANNEL_ID)
        except (discord.Forbidden, discord.HTTPException):
            return None

        if isinstance(fetched, discord.TextChannel):
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
        )

        channel = await self._get_log_channel()
        if channel is None:
            return

        description_lines = [
            f"Autor: {message.author.mention} ({message.author.id})",
            f"Kanál: {message.channel.mention}",
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
        await channel.send(content="", view=view)

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
        )

        description_lines = [
            f"Autor: {before.author.mention} ({before.author.id})",
            f"Kanál: {before.channel.mention}",
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
        await channel.send(content="", view=view)

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

        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(content="## Log bota"),
                discord.ui.TextDisplay(content=message[:4096]),
            )
        )
        await channel.send(content="", view=view)

    async def cog_unload(self):
        root_logger = logging.getLogger()
        if self._handler in root_logger.handlers:
            root_logger.removeHandler(self._handler)
        if self.log_task is not None:
            self.log_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.log_task


class _ChannelLogHandler(logging.Handler):
    def __init__(self, cog: LoggingCog):
        super().__init__()
        self.cog = cog

    def emit(self, record: logging.LogRecord):
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return

        loop = self.cog.bot.loop
        if loop is None or loop.is_closed():
            return

        loop.call_soon_threadsafe(self.cog.log_queue.put_nowait, message)
