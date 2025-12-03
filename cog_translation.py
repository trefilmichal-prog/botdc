import asyncio
import json
import logging
import urllib.error
import urllib.request

import discord
from discord.ext import commands

from config import (
    AUTO_TRANSLATE_CHANNEL_ID,
    AUTO_TRANSLATE_TARGET_CHANNEL_ID,
    CLAN_MEMBER_ROLE_EN_ID,
    CLAN_MEMBER_ROLE_ID,
    OLLAMA_MODEL,
    OLLAMA_TIMEOUT,
    OLLAMA_URL,
)


logger = logging.getLogger(__name__)


class AutoTranslateCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._target_channel: discord.abc.Messageable | None = None

    def _post_json(self, payload: dict[str, object]) -> str:
        request = urllib.request.Request(
            OLLAMA_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=OLLAMA_TIMEOUT) as response:
            return response.read().decode("utf-8")

    async def _ask_ollama(self, prompt: str) -> str | None:
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3},
        }

        try:
            raw_response = await asyncio.to_thread(self._post_json, payload)
            data = json.loads(raw_response)
        except (urllib.error.URLError, TimeoutError) as error:
            logger.warning("Ollama request failed: %s", error)
            return None
        except json.JSONDecodeError:
            logger.warning("Ollama returned invalid JSON")
            return None

        response_text = data.get("response") if isinstance(data, dict) else None
        if not response_text:
            return None
        return response_text.strip()

    async def _target_messageable(self) -> discord.abc.Messageable | None:
        if self._target_channel:
            return self._target_channel

        channel = self.bot.get_channel(AUTO_TRANSLATE_TARGET_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(AUTO_TRANSLATE_TARGET_CHANNEL_ID)
            except (discord.Forbidden, discord.HTTPException) as error:
                logger.warning("Unable to fetch target channel %s: %s", AUTO_TRANSLATE_TARGET_CHANNEL_ID, error)
                return None

        self._target_channel = channel
        return channel

    def _prepare_content(self, content: str) -> str:
        return content.replace(
            f"<@&{CLAN_MEMBER_ROLE_ID}>", f"<@&{CLAN_MEMBER_ROLE_EN_ID}>"
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if message.channel.id != AUTO_TRANSLATE_CHANNEL_ID:
            return

        if not message.content.strip():
            return

        prepared_content = self._prepare_content(message.content)
        prompt = (
            "Translate the following Discord message to English. "
            "Preserve the original formatting, emojis, and mentions. "
            "Answer with the translation only.\n\n"
            f"Message: {prepared_content}"
        )

        async with message.channel.typing():
            translation = await self._ask_ollama(prompt)

        if not translation:
            logger.warning("Translation failed for message %s", message.id)
            return

        target_channel = await self._target_messageable()
        if not target_channel:
            logger.warning(
                "Translation ready for message %s but target channel %s unavailable",
                message.id,
                AUTO_TRANSLATE_TARGET_CHANNEL_ID,
            )
            return

        await target_channel.send(translation)


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoTranslateCog(bot))
