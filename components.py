import discord
from typing import Iterable, Mapping, Sequence


class Components(discord.ui.LayoutView):
    def __init__(
        self,
        online_members: Iterable[str],
        offline_members: Iterable[str],
        unknown_members: Iterable[str] = (),
        leaderboard_entries: Mapping[str, Sequence[str]] | Iterable[str] = (),
    ):
        super().__init__(timeout=None)

        online_list = list(online_members)
        offline_list = list(offline_members)
        unknown_list = list(unknown_members)

        if isinstance(leaderboard_entries, Mapping):
            leaderboard_dict = {
                str(name): list(entries) for name, entries in leaderboard_entries.items()
            }
        else:
            leaderboard_list = list(leaderboard_entries)
            leaderboard_dict = {"Leaderboard": leaderboard_list} if leaderboard_list else {}

        online_section = "Online\n" + "\n".join(online_list) if online_list else "Online\nNikdo není online."
        offline_section = "Offline\n" + "\n".join(offline_list) if offline_list else "Offline\nNikdo není offline."
        content_blocks = [
            discord.ui.TextDisplay(content=online_section),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(content=offline_section),
        ]

        if unknown_list:
            unknown_section = "Neznámý\n" + "\n".join(unknown_list)
            content_blocks.extend(
                [
                    discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
                    discord.ui.TextDisplay(content=unknown_section),
                ]
            )

        self._leaderboard_variants = leaderboard_dict
        self._leaderboard_display: discord.ui.TextDisplay | None = None

        if self._leaderboard_variants:
            default_label = next(iter(self._leaderboard_variants))
            leaderboard_section = self._format_leaderboard_section(default_label)

            self._leaderboard_display = discord.ui.TextDisplay(content=leaderboard_section)
            content_blocks.extend(
                [
                    discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
                    self._leaderboard_display,
                ]
            )

            if len(self._leaderboard_variants) > 1:
                select = discord.ui.Select(
                    placeholder="Vyber žebříček",
                    options=[
                        discord.SelectOption(label=name, value=name)
                        for name in self._leaderboard_variants
                    ],
                )

                async def handle_select(interaction: discord.Interaction):
                    chosen = select.values[0]
                    await self._update_leaderboard(interaction, chosen)

                select.callback = handle_select
                self.add_item(discord.ui.ActionRow(select))

        container = discord.ui.Container(*content_blocks)
        self.add_item(container)

    def _format_leaderboard_section(self, name: str) -> str:
        entries = self._leaderboard_variants.get(name, []) if self._leaderboard_variants else []
        if entries:
            return name + "\n" + "\n".join(entries)
        return name + "\nŽádná data."

    async def _update_leaderboard(self, interaction: discord.Interaction, name: str):
        if self._leaderboard_display is None:
            return
        self._leaderboard_display.content = self._format_leaderboard_section(name)
        await interaction.response.edit_message(view=self)
