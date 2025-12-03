import asyncio
import json
import logging
import socket
import urllib.error
import urllib.request

import discord
from discord import app_commands
from discord.ext import commands

from config import OLLAMA_MODEL, OLLAMA_TIMEOUT, OLLAMA_URL
from i18n import CZECH_LOCALE, get_interaction_locale, get_message_locale, t


class ProphecyCog(commands.Cog, name="RobloxProphecy"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._logger = logging.getLogger(__name__)

    def _strip_mentions(self, content: str, mentions: list[discord.abc.User]) -> str:
        for mention in mentions:
            patterns = (f"<@{mention.id}>", f"<@!{mention.id}>")
            for pattern in patterns:
                content = content.replace(pattern, "")
        return content.strip()

    def _detect_czech_text(self, content: str) -> bool:
        czech_characters = "áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ"
        if any(char in czech_characters for char in content):
            return True

        lowercase = content.lower()
        czech_keywords = ("protože", "že", "jak", "kde", "co", "vtip", "prosím", "můžeš")
        return any(keyword in lowercase for keyword in czech_keywords)

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
            "options": {"temperature": 0.85},
        }

        try:
            raw_response = await asyncio.to_thread(self._post_json, payload)
            data = json.loads(raw_response)
        except (urllib.error.URLError, TimeoutError, socket.timeout) as error:
            self._logger.warning("Ollama request failed: %s", error)
            return None
        except json.JSONDecodeError:
            self._logger.warning("Ollama returned invalid JSON")
            return None

        response_text = data.get("response") if isinstance(data, dict) else None
        if not response_text:
            return None
        return response_text.strip()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if not self.bot.user or self.bot.user not in message.mentions:
            return

        dotaz = self._strip_mentions(message.content, message.mentions)
        locale = get_message_locale(message)
        if locale != CZECH_LOCALE and self._detect_czech_text(dotaz):
            locale = CZECH_LOCALE
        if not dotaz:
            await message.reply(
                t("mention_prompt_missing", locale),
                mention_author=False,
            )
            return

        async with message.channel.typing():
            prompt = t("prophecy_prompt_message", locale, question=dotaz)

            response_text = await self._ask_ollama(prompt)

        if not response_text:
            await message.reply(t("prophecy_unavailable", locale), mention_author=False)
            return

        embed = discord.Embed(
            title=t("prophecy_title", locale),
            description=response_text,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Model: {OLLAMA_MODEL}")

        await message.reply(embed=embed, mention_author=False)

    @app_commands.command(
        name="rebirth_future",
        description="Zeptej se Ollamy na vtipnou odpověď bez věštění.",
    )
    @app_commands.describe(dotaz="Na co se chceš zeptat? (česky nebo anglicky)")
    async def rebirth_future(self, interaction: discord.Interaction, dotaz: str | None = None):
        locale = get_interaction_locale(interaction)
        await interaction.response.defer()

        prompt = t("prophecy_prompt_slash", locale)
        if dotaz:
            prompt += f" Otázka hráče: {dotaz}" if locale.value.startswith("cs") else f" Player question: {dotaz}"
        else:
            prompt += t("prophecy_prompt_general", locale)

        response_text = await self._ask_ollama(prompt)
        if not response_text:
            await interaction.followup.send(
                t("prophecy_unavailable", locale),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=t("prophecy_title", locale),
            description=response_text,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Model: {OLLAMA_MODEL}")

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(ProphecyCog(bot))
