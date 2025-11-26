import re
from io import BytesIO
from typing import Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, UnidentifiedImageError
import pytesseract

from db import get_clan_stats_channel, set_clan_stats_channel


class ClanStatsOcrCog(commands.Cog, name="ClanStatsOcr"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.stats_channel_id: Optional[int] = get_clan_stats_channel()

    @app_commands.command(
        name="setup_clan_stats_room",
        description="Nastaví kanál, kam se budou posílat výsledky OCR ze screenu clanu.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_clan_stats_room(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        self.stats_channel_id = channel.id
        set_clan_stats_channel(channel.id)
        await interaction.response.send_message(
            f"Kanál pro clan statistiky nastaven na {channel.mention}.",
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not isinstance(message.channel, discord.DMChannel):
            return
        if not message.attachments:
            return

        channel = await self._resolve_stats_channel()
        if channel is None:
            await message.channel.send(
                "Není nastaven žádný kanál pro zapisování clan statistik. "
                "Použij na serveru `/setup_clan_stats_room`.",
            )
            return

        attachment = self._pick_image_attachment(message.attachments)
        if attachment is None:
            return

        ocr_result = await self._perform_ocr(attachment)
        if ocr_result is None:
            await message.channel.send(
                "Nepodařilo se načíst obrázek. Zkus prosím znovu se stejným screenu.",
            )
            return

        stats, raw_text = ocr_result
        if not stats:
            await message.channel.send(
                "OCR nenašlo žádné hodnoty. Ujisti se, že je screenshot stejný jako vzor.",
            )
            return

        embed = self._build_stats_embed(message.author, stats, raw_text)
        await channel.send(
            content=f"📊 Nové clan statistiky od <@{message.author.id}>",
            embed=embed,
        )
        await message.channel.send("Statistiky byly zpracovány a odeslány do roomky.")

    async def _resolve_stats_channel(self) -> Optional[discord.TextChannel]:
        if self.stats_channel_id is None:
            return None

        channel = self.bot.get_channel(self.stats_channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel

        try:
            fetched = await self.bot.fetch_channel(self.stats_channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

        if isinstance(fetched, discord.TextChannel):
            return fetched
        return None

    @staticmethod
    def _pick_image_attachment(attachments: List[discord.Attachment]) -> Optional[discord.Attachment]:
        for attachment in attachments:
            if attachment.content_type and attachment.content_type.startswith("image"):
                return attachment
            if attachment.filename.lower().endswith((".png", ".jpg", ".jpeg")):
                return attachment
        return None

    async def _perform_ocr(
        self, attachment: discord.Attachment
    ) -> Optional[tuple[Dict[str, str], str]]:
        try:
            data = await attachment.read()
            image = Image.open(BytesIO(data))
            raw_text = pytesseract.image_to_string(image)
        except (discord.HTTPException, UnidentifiedImageError, OSError):
            return None

        stats = self._extract_stats(raw_text)
        return stats, raw_text

    def _extract_stats(self, text: str) -> Dict[str, str]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        stat_map = {
            "season_rebirths": ["season", "rebirth"],
            "weekly_rebirths": ["weekly", "rebirth"],
            "total_rebirths": ["total", "rebirth"],
            "weekly_hatching_points": ["hatching", "point"],
            "eggs_opened": ["eggs", "opened"],
        }

        values: Dict[str, str] = {}
        for key, keywords in stat_map.items():
            value = self._find_value_for_keywords(lines, keywords)
            if value:
                values[key] = value
        return values

    @staticmethod
    def _find_value_for_keywords(lines: List[str], keywords: List[str]) -> Optional[str]:
        for line in lines:
            lower_line = line.lower()
            if all(keyword in lower_line for keyword in keywords):
                match = re.search(r"([0-9][0-9.,]*\s*[A-Za-z]{0,3})", line)
                if match:
                    return match.group(1).replace(" ", "")
        return None

    def _build_stats_embed(
        self, author: discord.User, stats: Dict[str, str], raw_text: str
    ) -> discord.Embed:
        label_map = {
            "season_rebirths": "(Season 5) Rebirths",
            "weekly_rebirths": "Weekly Rebirths",
            "total_rebirths": "Total Rebirths",
            "weekly_hatching_points": "Weekly Hatching Points",
            "eggs_opened": "Eggs Opened",
        }

        embed = discord.Embed(
            title="Clan statistiky – OCR",
            description=f"Požadoval {author.mention}",
            color=0x2ECC71,
        )

        for key in label_map:
            embed.add_field(
                name=label_map[key],
                value=stats.get(key, "Nedetekováno"),
                inline=False,
            )

        shortened_raw = raw_text.strip()
        if len(shortened_raw) > 500:
            shortened_raw = shortened_raw[:500] + "…"
        if shortened_raw:
            embed.add_field(
                name="Raw OCR text", value=f"```{shortened_raw}```", inline=False
            )

        return embed


async def setup(bot: commands.Bot):
    await bot.add_cog(ClanStatsOcrCog(bot))
