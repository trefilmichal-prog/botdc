import discord
from typing import Iterable

class Components(discord.ui.LayoutView):
    def __init__(self, online_members: Iterable[str], offline_members: Iterable[str], unknown_members: Iterable[str] = ()): 
        super().__init__(timeout=None)

        online_list = list(online_members)
        offline_list = list(offline_members)
        unknown_list = list(unknown_members)

        online_section = "Online\n" + "\n".join(online_list) if online_list else "Online\nNikdo není online."
        offline_section = "Offline\n" + "\n".join(offline_list) if offline_list else "Offline\nNikdo není offline."
        unknown_section = "Neznámý\n" + "\n".join(unknown_list) if unknown_list else ""

        content_blocks = [discord.ui.TextDisplay(content=online_section), discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large), discord.ui.TextDisplay(content=offline_section)]
        if unknown_section:
            content_blocks.extend([discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large), discord.ui.TextDisplay(content=unknown_section)])

        container = discord.ui.Container(*content_blocks)
        self.add_item(container)
