# bot-node

Minimální Node.js varianta Discord bota, která běží paralelně vedle existujícího Python procesu.

## Struktura

- `src/index.js` – bootstrap klienta, command router, registrace slash commandů.
- `src/commands/` – moduly slash commandů (aktuálně sjednocené pod `/bot <group> <subcommand>`).
- `src/storage/` – per-guild persistence vrstva.
- `data/guild-state.json` – persisted stav načítaný po restartu.

## Lokální spuštění

```bash
cd bot-node
npm install
DISCORD_TOKEN=... DISCORD_CLIENT_ID=... DISCORD_GUILD_ID=... npm start
```

Poznámky:
- `DISCORD_GUILD_ID` je volitelné. Pokud je nastavené, commandy se registrují guild-scoped (rychlejší propagace).
- Bez `DISCORD_GUILD_ID` se commandy registrují globálně.

## Jak běží vedle Python procesu

- Python bot zůstává „control plane“ pro restart/update workflow (`updatebot` logika může zůstat v Pythonu).
- Node proces je „command plane“ pro nově strukturované slash commandy (`/bot <group> <subcommand>`).
- Oba procesy mohou běžet současně, pokud mají oddělený command namespace.

## Command surface a mapping

Python command surface je zachován přes explicitní mapování v `src/commands/legacyMapping.js`.

- Legacy commandy z Pythonu jsou mapovány na nové group/subcommand cesty.
- Nové příkazy přidávej primárně jako subcommandy do existujících group (`commandTree` v `botCommand.js`).

## Persistence po restartu

- Při startu se načítá `data/guild-state.json`.
- Data jsou izolovaná per guild (`guildId`).
- Ukázkové per-guild set/show flow je dostupné přes:
  - `/bot state set key:<...> value:<...>`
  - `/bot state show`
