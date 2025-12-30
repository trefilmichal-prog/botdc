import asyncio
import json
import logging
import socket
from datetime import datetime
import urllib.error
import urllib.request

import discord
from discord import app_commands
from discord.ext import commands

from config import OLLAMA_MODEL, OLLAMA_TIMEOUT, OLLAMA_URL
from db import log_prophecy
from i18n import CZECH_LOCALE, get_interaction_locale, get_message_locale, t


class ProphecyCog(commands.Cog, name="RobloxProphecy"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._logger = logging.getLogger(__name__)

    def _build_prophecy_view(self, locale, question: str, answer: str):
        locale_code = getattr(locale, "value", str(locale))
        question_label = "Otázka" if locale_code.startswith("cs") else "Question"
        answer_label = "Odpověď" if locale_code.startswith("cs") else "Answer"

        view = discord.ui.LayoutView(timeout=None)
        container = discord.ui.Container(
            discord.ui.TextDisplay(content=f"## {t('prophecy_title', locale)}"),
            discord.ui.TextDisplay(content=f"{question_label}: {question}"),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(content=f"{answer_label}: {answer}"),
            discord.ui.TextDisplay(content=f"Model: {OLLAMA_MODEL}"),
        )
        view.add_item(container)
        return view

    def _log_prophecy(
        self,
        message: discord.Message,
        question: str,
        answer: str,
        author_id: int | None = None,
    ):
        try:
            log_prophecy(
                message_id=message.id,
                channel_id=message.channel.id,
                author_id=author_id if author_id is not None else message.author.id,
                question=question,
                answer=answer,
                model=OLLAMA_MODEL,
                created_at=datetime.utcnow(),
            )
        except Exception as error:
            self._logger.warning("Failed to persist prophecy log: %s", error)

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
            await message.channel.send(
                t("mention_prompt_missing", locale),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        async with message.channel.typing():
            prompt = t("prophecy_prompt_message", locale, question=dotaz)

            response_text = await self._ask_ollama(prompt)

        if not response_text:
            await message.channel.send(
                t("prophecy_unavailable", locale),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        response_view = self._build_prophecy_view(locale, dotaz, response_text)
        locale_code = getattr(locale, "value", str(locale))
        question_label = "Otázka" if locale_code.startswith("cs") else "Question"
        answer_label = "Odpověď" if locale_code.startswith("cs") else "Answer"
        response_body = (
            f"{t('prophecy_title', locale)}\n"
            f"{question_label}: {dotaz}\n\n"
            f"{answer_label}: {response_text}\n\n"
            f"Model: {OLLAMA_MODEL}"
        )

        sent_message = await message.channel.send(
            response_body,
            view=response_view,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self._log_prophecy(sent_message, dotaz, response_text, author_id=message.author.id)

    @app_commands.command(
        name="rebirth_future",
        description="Zeptej se Ollamy na vtipnou odpověď bez věštění.",
    )
    @app_commands.describe(dotaz="Na co se chceš zeptat? (česky nebo anglicky)")
    async def rebirth_future(self, interaction: discord.Interaction, dotaz: str | None = None):
        locale = get_interaction_locale(interaction)
        await interaction.response.defer(ephemeral=True)

        prompt = t("prophecy_prompt_slash", locale)
        if dotaz:
            prompt += f" Otázka hráče: {dotaz}" if locale.value.startswith("cs") else f" Player question: {dotaz}"
        else:
            prompt += t("prophecy_prompt_general", locale)

        response_text = await self._ask_ollama(prompt)
        if not response_text:
            await interaction.edit_original_response(
                t("prophecy_unavailable", locale),
            )
            return

        response_view = self._build_prophecy_view(locale, dotaz or "-", response_text)
        locale_code = getattr(locale, "value", str(locale))
        question_label = "Otázka" if locale_code.startswith("cs") else "Question"
        answer_label = "Odpověď" if locale_code.startswith("cs") else "Answer"
        response_body = (
            f"{t('prophecy_title', locale)}\n"
            f"{question_label}: {dotaz or '-'}\n\n"
            f"{answer_label}: {response_text}\n\n"
            f"Model: {OLLAMA_MODEL}"
        )

        await interaction.edit_original_response(
            response_body,
            view=response_view,
        )
        try:
            sent_message = await interaction.original_response()
        except discord.HTTPException:
            return
        self._log_prophecy(
            sent_message, dotaz or "-", response_text, author_id=interaction.user.id
        )
