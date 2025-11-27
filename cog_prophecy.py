import asyncio
import json
import urllib.error
import urllib.request

import discord
from discord import app_commands
from discord.ext import commands

from config import OLLAMA_MODEL, OLLAMA_URL


class ProphecyCog(commands.Cog, name="RobloxProphecy"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _strip_mentions(self, content: str, mentions: list[discord.abc.User]) -> str:
        for mention in mentions:
            patterns = (f"<@{mention.id}>", f"<@!{mention.id}>")
            for pattern in patterns:
                content = content.replace(pattern, "")
        return content.strip()

    def _post_json(self, payload: dict[str, object]) -> str:
        request = urllib.request.Request(
            OLLAMA_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
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
        except (urllib.error.URLError, TimeoutError) as error:
            print(f"Ollama request failed: {error}")
            return None
        except json.JSONDecodeError:
            print("Ollama returned invalid JSON")
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
        if not dotaz:
            await message.reply(
                "Ahoj! Příště mi rovnou napiš otázku, ať ti můžu věštit budoucnost. 😊",
                mention_author=False,
            )
            return

        async with message.channel.typing():
            prompt = (
                "Jsi veselý český věštec pro hráče Roblox hry Rebirth Champions Ultimate."
                " Odpovídej vždy česky, ve 1–2 věty maximálně, s lehkým humorem a konkrétním tipem na další postup."
                " Vyhýbej se vulgaritám a udrž tón přátelský pro komunitu Discordu."
                f" Otázka hráče: {dotaz}"
            )

            response_text = await self._ask_ollama(prompt)

        if not response_text:
            await message.reply(
                "Nemohu se momentálně spojit s Ollamou. Zkus to prosím za chvíli.",
                mention_author=False,
            )
            return

        embed = discord.Embed(
            title="🔮 Roblox věštba",
            description=response_text,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Model: {OLLAMA_MODEL}")

        await message.reply(embed=embed, mention_author=False)

    @app_commands.command(
        name="rebirth_future",
        description="Zeptej se Ollamy na vtipnou věštbu pro Rebirth Champions Ultimate.",
    )
    @app_commands.describe(dotaz="Co tě zajímá o budoucnosti v Rebirth Champions Ultimate?")
    async def rebirth_future(self, interaction: discord.Interaction, dotaz: str | None = None):
        await interaction.response.defer()

        prompt = (
            "Jsi veselý český věštec pro hráče Roblox hry Rebirth Champions Ultimate."
            " Odpovídej vždy česky, ve 2–3 větách, s lehkým humorem a konkrétním tipem na další postup."
            " Vyhýbej se vulgaritám a udrž tón přátelský pro komunitu Discordu."
        )
        if dotaz:
            prompt += f" Otázka hráče: {dotaz}"
        else:
            prompt += " Dej obecnou předpověď pro nejbližší run."

        response_text = await self._ask_ollama(prompt)
        if not response_text:
            await interaction.followup.send(
                "Nemohu se momentálně spojit s Ollamou. Zkus to prosím za chvíli.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🔮 Roblox věštba",
            description=response_text,
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Model: {OLLAMA_MODEL}")

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(ProphecyCog(bot))
