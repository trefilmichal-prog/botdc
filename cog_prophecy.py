import asyncio
import json
import logging
import random
import socket
from urllib.parse import urlsplit, urlunsplit
from datetime import datetime
import urllib.error
import urllib.request

import discord
from discord import app_commands
from discord.ext import commands

from config import OLLAMA_MODEL, OLLAMA_TIMEOUT, OLLAMA_URL, validate_ollama_model
from db import get_guild_personality, log_prophecy, set_guild_personality
from i18n import CZECH_LOCALE, get_interaction_locale, get_message_locale, t


PERSONALITY_MIN_LENGTH = 20
PERSONALITY_MAX_LENGTH = 2000
# 5% random prophecy trigger channels
PROPHECY_RANDOM_CHANNEL_IDS = {1457820636557742232, 1440041477664411731}
PROPHECY_RANDOM_CHANCE = 0.40


class PersonalityEditModal(discord.ui.Modal):
    def __init__(self, guild_id: int, locale: discord.Locale, current_personality: str | None = None):
        super().__init__(title=t("prophecy_personality_modal_title", locale))
        self.guild_id = guild_id
        self.locale = locale
        self.personality_text = discord.ui.TextInput(
            label=t("prophecy_personality_modal_label", locale),
            style=discord.TextStyle.paragraph,
            required=True,
            min_length=PERSONALITY_MIN_LENGTH,
            max_length=PERSONALITY_MAX_LENGTH,
            placeholder=t("prophecy_personality_modal_placeholder", locale),
        )
        self.add_item(self.personality_text)
        if current_personality:
            self.personality_text.default = current_personality[:PERSONALITY_MAX_LENGTH]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw_value = str(self.personality_text.value or "")
        personality = raw_value.strip()

        if len(personality) < PERSONALITY_MIN_LENGTH:
            error_view = discord.ui.LayoutView(timeout=None)
            error_view.add_item(
                discord.ui.Container(
                    discord.ui.TextDisplay(content="## ❌ Neplatná délka textu"),
                    discord.ui.TextDisplay(
                        content=(
                            t(
                                "prophecy_personality_too_short",
                                self.locale,
                                min_length=PERSONALITY_MIN_LENGTH,
                            )
                        )
                    ),
                )
            )
            await interaction.response.send_message(view=error_view, ephemeral=True)
            return

        if len(personality) > PERSONALITY_MAX_LENGTH:
            error_view = discord.ui.LayoutView(timeout=None)
            error_view.add_item(
                discord.ui.Container(
                    discord.ui.TextDisplay(content="## ❌ Neplatná délka textu"),
                    discord.ui.TextDisplay(
                        content=(
                            t(
                                "prophecy_personality_too_long",
                                self.locale,
                                max_length=PERSONALITY_MAX_LENGTH,
                            )
                        )
                    ),
                )
            )
            await interaction.response.send_message(view=error_view, ephemeral=True)
            return

        try:
            set_guild_personality(self.guild_id, personality)
        except Exception as error:
            failure_view = discord.ui.LayoutView(timeout=None)
            failure_view.add_item(
                discord.ui.Container(
                    discord.ui.TextDisplay(content=t("prophecy_personality_save_failed_title", self.locale)),
                    discord.ui.TextDisplay(content=t("prophecy_personality_save_failed_body", self.locale, error=error)),
                )
            )
            await interaction.response.send_message(view=failure_view, ephemeral=True)
            return

        success_view = discord.ui.LayoutView(timeout=None)
        success_view.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(content=t("prophecy_personality_saved_title", self.locale)),
                discord.ui.TextDisplay(content=t("prophecy_personality_saved_body", self.locale)),
                discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
                discord.ui.TextDisplay(
                    content=t(
                        "prophecy_personality_saved_length",
                        self.locale,
                        length=len(personality),
                    )
                ),
            )
        )
        await interaction.response.send_message(view=success_view, ephemeral=True)


class ProphecyCog(commands.Cog, name="RobloxProphecy"):
    prophecy_group = app_commands.Group(name="prophecy", description="Nastavení proroctví")
    personality_group = app_commands.Group(
        name="personality",
        description="Správa osobnosti proroctví",
        parent=prophecy_group,
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._logger = logging.getLogger(__name__)
        validate_ollama_model(self.__class__.__name__)

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
        for ollama_url in self._candidate_ollama_urls():
            try:
                request = urllib.request.Request(
                    ollama_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=OLLAMA_TIMEOUT) as response:
                    return response.read().decode("utf-8")
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    continue
                raise

        raise urllib.error.HTTPError(
            self._candidate_ollama_urls()[-1],
            404,
            "No valid Ollama endpoint found",
            hdrs=None,
            fp=None,
        )

    def _candidate_ollama_urls(self) -> list[str]:
        parsed = urlsplit(OLLAMA_URL)
        path = parsed.path or ""
        if not path or path == "/":
            base = urlunsplit((parsed.scheme, parsed.netloc, "", parsed.query, parsed.fragment)).rstrip("/")
            return [f"{base}/api/generate", f"{base}/api/chat"]

        if path.endswith("/api/generate"):
            return [OLLAMA_URL, OLLAMA_URL.replace("/api/generate", "/api/chat")]

        if path.endswith("/api/chat"):
            return [OLLAMA_URL, OLLAMA_URL.replace("/api/chat", "/api/generate")]

        return [OLLAMA_URL]


    def _question_suffix(self, locale, question: str) -> str:
        return (
            f" Otázka hráče: {question}"
            if locale.value.startswith("cs")
            else f" Player question: {question}"
        )

    def _load_guild_personality(self, guild_id: int | None) -> str | None:
        if guild_id is None:
            return None
        try:
            return get_guild_personality(guild_id)
        except Exception as error:
            self._logger.warning(
                "Failed to load guild personality for guild_id=%s: %s", guild_id, error
            )
            return None

    def _build_prompt(self, locale, question: str, guild_id: int | None, *, is_message: bool) -> str:
        guild_prompt = self._load_guild_personality(guild_id)
        if guild_prompt:
            return f"{guild_prompt}{self._question_suffix(locale, question)}"

        if is_message:
            return t("prophecy_prompt_message", locale, question=question)

        return f"{t('prophecy_prompt_slash', locale)}{self._question_suffix(locale, question)}"

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

        mention_trigger = bool(self.bot.user and self.bot.user in message.mentions)
        random_trigger = (
            message.channel.id in PROPHECY_RANDOM_CHANNEL_IDS
            and random.random() < PROPHECY_RANDOM_CHANCE
        )
        if not mention_trigger and not random_trigger:
            return

        if mention_trigger:
            dotaz = self._strip_mentions(message.content, message.mentions)
        else:
            dotaz = message.content.strip()
            if not dotaz:
                return

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
            prompt = self._build_prompt(
                locale,
                dotaz,
                message.guild.id if message.guild else None,
                is_message=True,
            )

            response_text = await self._ask_ollama(prompt)

        if not response_text:
            await message.channel.send(
                t("prophecy_unavailable", locale),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        response_view = self._build_prophecy_view(locale, dotaz, response_text)
        sent_message = await message.channel.send(
            view=response_view,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self._log_prophecy(sent_message, dotaz, response_text, author_id=message.author.id)

    @personality_group.command(name="edit", description="Upraví osobnost proroctví pro tento server")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def prophecy_personality_edit(self, interaction: discord.Interaction) -> None:
        locale = get_interaction_locale(interaction)
        if not interaction.guild_id:
            fallback_view = discord.ui.LayoutView(timeout=None)
            fallback_view.add_item(
                discord.ui.Container(
                    discord.ui.TextDisplay(content=t("prophecy_personality_guild_only", locale)),
                )
            )
            await interaction.response.send_message(view=fallback_view, ephemeral=True)
            return

        current_personality = self._load_guild_personality(interaction.guild_id)
        await interaction.response.send_modal(
            PersonalityEditModal(
                guild_id=interaction.guild_id,
                locale=locale,
                current_personality=current_personality,
            )
        )

    @app_commands.command(
        name="rebirth_future",
        description="Zeptej se Ollamy na vtipnou odpověď bez věštění.",
    )
    @app_commands.describe(dotaz="Na co se chceš zeptat? (česky nebo anglicky)")
    async def rebirth_future(self, interaction: discord.Interaction, dotaz: str | None = None):
        locale = get_interaction_locale(interaction)
        await interaction.response.defer(ephemeral=True)

        if dotaz:
            prompt = self._build_prompt(locale, dotaz, interaction.guild_id, is_message=False)
        else:
            prompt = f"{t('prophecy_prompt_slash', locale)}{t('prophecy_prompt_general', locale)}"

        response_text = await self._ask_ollama(prompt)
        if not response_text:
            await interaction.edit_original_response(
                t("prophecy_unavailable", locale),
            )
            return

        response_view = self._build_prophecy_view(locale, dotaz or "-", response_text)
        await interaction.edit_original_response(
            view=response_view,
        )
        try:
            sent_message = await interaction.original_response()
        except discord.HTTPException:
            return
        self._log_prophecy(
            sent_message, dotaz or "-", response_text, author_id=interaction.user.id
        )
