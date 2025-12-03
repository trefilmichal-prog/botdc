import asyncio
import json
import logging
import types
import weakref
import urllib.error
import urllib.request

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    AUTO_TRANSLATE_CHANNEL_ID,
    AUTO_TRANSLATE_TARGET_CHANNEL_ID,
    CLAN_MEMBER_ROLE_EN_ID,
    CLAN_MEMBER_ROLE_ID,
    OLLAMA_MODEL,
    OLLAMA_TIMEOUT,
    OLLAMA_URL,
    REACTION_TRANSLATION_BLOCKED_CHANNEL_IDS,
)


logger = logging.getLogger(__name__)


class TranslationRevealView(discord.ui.View):
    def __init__(self, *, translation: str, source_message: discord.Message, requester_id: int):
        super().__init__(timeout=600)
        self._translation = translation
        self._source_message = source_message
        self._requester_id = requester_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id == self._requester_id:
            return True

        await interaction.response.send_message(
            "Tento překlad není pro vás. Přidejte vlastní reakci, pokud chcete překlad.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="Zobrazit překlad", style=discord.ButtonStyle.primary)
    async def reveal_translation(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            f"Překlad zprávy: {self._source_message.jump_url}\n\n{self._translation}",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class AutoTranslateCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._target_channel: discord.abc.Messageable | None = None
        self._reaction_targets = {"🇨🇿": "Czech", "🇺🇲": "English"}
        self._safe_allowed_mentions = discord.AllowedMentions(
            everyone=False, roles=False, replied_user=False
        )
        self._auto_translate_cooldown = commands.CooldownMapping.from_cooldown(
            5, 30, commands.BucketType.channel
        )
        self._reaction_cooldown = commands.CooldownMapping.from_cooldown(
            3, 20, commands.BucketType.channel
        )

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
            "options": {"temperature": 0},
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

    def _sanitize_output(self, content: str) -> str:
        safe_content = content.replace("@everyone", "@\u200beveryone").replace(
            "@here", "@\u200bhere"
        )
        return safe_content.replace("<@&", "<@\u200b&")

    def _build_prompt(self, language: str, content: str) -> str:
        return (
            f"Translate the following Discord message to {language}. "
            "Preserve the original formatting, emojis, and mentions. "
            "Use a neutral, respectful tone without jokes or additions. "
            "Answer with the translation only.\n\n"
            f"Message: {content}"
        )

    async def _translate_text(self, language: str, content: str) -> str | None:
        prepared_content = self._prepare_content(content)
        prompt = self._build_prompt(language, prepared_content)
        return await self._ask_ollama(prompt)

    async def _respond_with_translation(
        self, interaction: discord.Interaction, language: str, message: discord.Message
    ) -> None:
        if message.author.bot:
            await interaction.response.send_message(
                "Botí zprávy nelze překládat.", ephemeral=True
            )
            return

        if not message.content.strip():
            await interaction.response.send_message(
                "Zpráva je prázdná, není co překládat.", ephemeral=True
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        translation = await self._translate_text(language, message.content)
        if not translation:
            await interaction.followup.send(
                "Překlad se nepodařil, zkuste to prosím znovu.", ephemeral=True
            )
            return

        safe_translation = self._sanitize_output(translation)
        await interaction.followup.send(
            f"Překlad do {language}: {safe_translation}",
            ephemeral=True,
            allowed_mentions=self._safe_allowed_mentions,
        )

    @commands.hybrid_command(name="translate")
    async def translate_command(
        self, ctx: commands.Context, language: str, *, text: str
    ):
        """Translate arbitrary text for anyone without special permissions."""

        prompt = self._build_prompt(language, text)

        async with ctx.typing():
            translation = await self._ask_ollama(prompt)

        if not translation:
            await ctx.reply(
                "Překlad se nepodařil, zkuste to prosím znovu.",
                mention_author=False,
                allowed_mentions=self._safe_allowed_mentions,
            )
            return

        safe_translation = self._sanitize_output(translation)
        await ctx.reply(
            safe_translation,
            mention_author=False,
            allowed_mentions=self._safe_allowed_mentions,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if message.channel.id != AUTO_TRANSLATE_CHANNEL_ID:
            return

        retry_after = self._auto_translate_cooldown.get_bucket(message).update_rate_limit()
        if retry_after:
            logger.info(
                "Překlad ve kanálu %s odložen kvůli limitu, čeká %.1fs",
                message.channel.id,
                retry_after,
            )
            return

        if not message.content.strip():
            return

        prepared_content = self._prepare_content(message.content)
        prompt = self._build_prompt("English", prepared_content)

        async with message.channel.typing():
            translation = await self._ask_ollama(prompt)

        if not translation:
            logger.warning("Translation failed for message %s", message.id)
            return

        safe_translation = self._sanitize_output(translation)
        target_channel = await self._target_messageable()
        if not target_channel:
            logger.warning(
                "Translation ready for message %s but target channel %s unavailable",
                message.id,
                AUTO_TRANSLATE_TARGET_CHANNEL_ID,
            )
            return

        await target_channel.send(
            safe_translation, allowed_mentions=self._safe_allowed_mentions
        )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == getattr(self.bot.user, "id", None):
            return

        target_language = self._reaction_targets.get(str(payload.emoji))
        if not target_language:
            return

        retry_after = self._reaction_cooldown.get_bucket(
            types.SimpleNamespace(channel=types.SimpleNamespace(id=payload.channel_id))
        ).update_rate_limit()
        if retry_after:
            logger.info(
                "Reakční překlad v kanálu %s odložen kvůli limitu, čeká %.1fs",
                payload.channel_id,
                retry_after,
            )
            return

        channel = self.bot.get_channel(payload.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(payload.channel_id)
            except (discord.Forbidden, discord.HTTPException) as error:
                logger.warning("Unable to fetch channel %s: %s", payload.channel_id, error)
                return

        if payload.channel_id in REACTION_TRANSLATION_BLOCKED_CHANNEL_IDS:
            return

        if not isinstance(channel, discord.abc.Messageable):
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as error:
            logger.warning("Unable to fetch message %s: %s", payload.message_id, error)
            return

        if message.author.bot:
            return

        if not message.content.strip():
            return

        prepared_content = self._prepare_content(message.content)
        prompt = self._build_prompt(target_language, prepared_content)

        async with channel.typing():
            translation = await self._ask_ollama(prompt)

        if not translation:
            logger.warning(
                "Reaction translation failed for message %s with emoji %s",
                message.id,
                payload.emoji,
            )
            return

        safe_translation = self._sanitize_output(translation)

        try:
            await message.reply(
                f"Překlad: {safe_translation}",
                mention_author=False,
                allowed_mentions=self._safe_allowed_mentions,
            )
        except discord.HTTPException as error:
            logger.warning(
                "Failed to send reaction translation for message %s: %s",
                message.id,
                error,
            )

async def setup(bot: commands.Bot):
    cog = AutoTranslateCog(bot)
    await bot.add_cog(cog)

    cog_ref = weakref.ref(cog)
    responder_ref = weakref.WeakMethod(cog._respond_with_translation)

    async def _invoke_translation(
        language: str, interaction: discord.Interaction, message: discord.Message
    ) -> None:
        target_cog = cog_ref()

        if not isinstance(target_cog, AutoTranslateCog):
            logger.warning(
                "Translation cog unavailable for %s context menu", language
            )
            await interaction.response.send_message(
                "Překlad není dostupný, zkuste to prosím znovu později.",
                ephemeral=True,
            )
            return

        responder = responder_ref() or getattr(
            target_cog, "_respond_with_translation", None
        )
        if not callable(responder):
            logger.warning(
                "Translation responder missing for %s context menu", language
            )
            await interaction.response.send_message(
                "Překlad není dostupný, zkuste to prosím znovu později.",
                ephemeral=True,
            )
            return

        await responder(interaction, language, message)

    async def translate_to_czech(
        interaction: discord.Interaction, message: discord.Message
    ) -> None:
        await _invoke_translation("Czech", interaction, message)

    async def translate_to_english(
        interaction: discord.Interaction, message: discord.Message
    ) -> None:
        await _invoke_translation("English", interaction, message)

    bot.tree.add_command(
        app_commands.ContextMenu(
            name="Přeložit do češtiny", callback=translate_to_czech
        )
    )
    bot.tree.add_command(
        app_commands.ContextMenu(
            name="Translate to English", callback=translate_to_english
        )
    )
