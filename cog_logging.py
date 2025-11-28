import asyncio
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
        self.log_task = self.bot.loop.create_task(self._process_log_queue())

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
        embed = discord.Embed(
            title="Zpráva odstraněna",
            description="\n".join(description_lines),
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Obsah", value=content[:1024], inline=False)

        attachments = [attachment.url for attachment in message.attachments]
        if attachments:
            embed.add_field(
                name="Přílohy",
                value="\n".join(attachments)[:1024],
                inline=False,
            )

        await channel.send(embed=embed)

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

        embed = discord.Embed(
            title="Zpráva upravena",
            description="\n".join(description_lines),
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Původní text",
            value=(before.content or "*(Žádný text)*")[:1024],
            inline=False,
        )
        embed.add_field(
            name="Nový text",
            value=(after.content or "*(Žádný text)*")[:1024],
            inline=False,
        )

        await channel.send(embed=embed)

    async def _process_log_queue(self):
        try:
            while True:
                log_entry = await self.log_queue.get()
                await self._send_log_embed(log_entry)
        except asyncio.CancelledError:
            pass

    async def _send_log_embed(self, message: str):
        channel = await self._get_log_channel()
        if channel is None:
            return

        embed = discord.Embed(
            title="Log bota",
            description=message[:4096],
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow(),
        )
        await channel.send(embed=embed)

    def cog_unload(self):
        root_logger = logging.getLogger()
        if self._handler in root_logger.handlers:
            root_logger.removeHandler(self._handler)
        self.log_task.cancel()


def setup(bot: commands.Bot):
    bot.add_cog(LoggingCog(bot))


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
        if loop.is_closed():
            return

        loop.call_soon_threadsafe(self.cog.log_queue.put_nowait, message)
