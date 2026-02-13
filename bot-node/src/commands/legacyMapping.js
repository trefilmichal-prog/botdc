export const legacyMapping = [
  ['help', 'bot basic help'],
  ['admin kick', 'bot admin kick'],
  ['admin ban', 'bot admin ban'],
  ['admin warn', 'bot admin warn'],
  ['mute', 'bot admin mute'],
  ['setnick', 'bot admin setnick'],
  ['profile', 'bot xp profile'],
  ['give_coins', 'bot xp give-coins'],
  ['setuptimers', 'bot timers setup'],
  ['settimer', 'bot timers set'],
  ['removetimer', 'bot timers remove'],
  ['setupshop', 'bot shop setup'],
  ['addshopitem', 'bot shop add-item'],
  ['shoporders', 'bot shop orders'],
  ['see_sold_shop', 'bot shop sold'],
  ['setup_panel', 'bot wood setup-panel'],
  ['set_need', 'bot wood set-need'],
  ['reset_need', 'bot wood reset-need'],
  ['resources', 'bot wood resources'],
  ['setupgiveaway', 'bot giveaway setup'],
  ['start_giveaway', 'bot giveaway start'],
  ['setup_sp', 'bot setup-panel setup-sp'],
  ['setup_ready_panel', 'bot attendance setup-ready-panel'],
  ['setup_clan_room', 'bot leaderboard setup-clan-room'],
  ['setup_leaderboard', 'bot leaderboard setup-board'],
  ['leaderboard', 'bot leaderboard show'],
  ['setup_clan_stats_room', 'bot clan-stats setup-room'],
  ['clan_stats_dm', 'bot clan-stats dm'],
  ['rebirth_future', 'bot prophecy ask'],
  ['updatebot', 'bot updater run'],
  ['roblox_activity', 'bot roblox activity'],
  ['roblox_tracking', 'bot roblox tracking'],
  ['roblox_leaderboard', 'bot roblox leaderboard'],
  ['cookie', 'bot roblox cookie'],
  ['welcome/*', 'bot welcome ...'],
  ['clan_panel/*', 'bot clan-panel ...'],
  ['log_settings/*', 'bot log-settings ...'],
  ['secret/*, dropstats/*, roles/*', 'bot secret ...']
];

export function renderMappingPreview(limit = 20) {
  return legacyMapping
    .slice(0, limit)
    .map(([legacy, modern]) => `• ${legacy} → ${modern}`)
    .join('\n');
}
