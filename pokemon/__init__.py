"""Runs once, the first time anything under `pokemon.*` is imported (which
`bot.py`'s startup sequence guarantees happens before any command is
usable) -- the one place this package registers itself into the OTHER
tracks' pluggable extension points, rather than those tracks needing to
know Pokémon exists.

Two registrations happen here:

- `cogs.achievements.ACHIEVEMENT_PRESETS["Pokémon Pack"]`, ported verbatim
  from the monolith's own `Pokémon Pack` bulkimport branch (the
  `/achievement bulkimport` command, contore.py). Each "custom"-type
  trigger's `metric` is kept in sync by
  `pokemon.commands.repository.sync_achievement_progress` -- see that
  function's docstring for exactly when it's called and its one known gap
  (`eggs_hatched`).
- `cogs.export.EXPORT_PROVIDERS["pokemon"]` ->
  `pokemon.commands.repository.pokemon_export`, so `/export` includes
  Pokémon data the same way the monolith's inlined version did.

(`cogs.help.HELP_DOCS`'s `pokemon_basics`/`pokemon_battling`/
`pokemon_breeding`/`pokemon_growth`/`pokemon_economy` topics are NOT
registered from here -- Track A wrote that content directly into
`cogs/help.py` itself rather than through a plugin registry, so there's
nothing for this package to hook in.)
"""
try:
    from cogs.achievements import ACHIEVEMENT_PRESETS as _ACHIEVEMENT_PRESETS
except ImportError:
    _ACHIEVEMENT_PRESETS = None  # partial checkout without cogs/ -- nothing to register into

try:
    from cogs.export import EXPORT_PROVIDERS as _EXPORT_PROVIDERS
except ImportError:
    _EXPORT_PROVIDERS = None

if _EXPORT_PROVIDERS is not None:
    from pokemon.commands.repository import pokemon_export as _pokemon_export
    _EXPORT_PROVIDERS["pokemon"] = _pokemon_export
    del _pokemon_export

del _EXPORT_PROVIDERS

if _ACHIEVEMENT_PRESETS is not None:
    from pokemon.dex.items_repo import POKEBALL_EMOJI as _BALL

    _ACHIEVEMENT_PRESETS["Pokémon Pack"] = [
        {"achievement_id": "first_catch", "name": "First Catch", "description": "*Catch your first Pokémon*",
         "icon": _BALL["Poké Ball"], "points": 10, "rarity": "COMMON", "type": "automated",
         "trigger": {"type": "custom", "metric": "pokemon_caught", "count": 1}},
        {"achievement_id": "pokemon_collector", "name": "Pokémon Collector", "description": "*Catch 50 Pokémon*",
         "icon": _BALL["Ultra Ball"], "points": 20, "rarity": "UNCOMMON", "type": "automated",
         "trigger": {"type": "custom", "metric": "pokemon_caught", "count": 50}},
        {"achievement_id": "pokemon_hoarder", "name": "Pokémon Hoarder", "description": "*Catch 250 Pokémon*",
         "icon": _BALL["Master Ball"], "points": 40, "rarity": "RARE", "type": "automated",
         "trigger": {"type": "custom", "metric": "pokemon_caught", "count": 250}},
        {"achievement_id": "catch_em_all_ish", "name": "Catch 'Em All(ish)", "description": "*Catch 1,000 Pokémon*",
         "icon": "\U0001F3C6", "points": 100, "rarity": "LEGENDARY", "type": "automated",
         "trigger": {"type": "custom", "metric": "pokemon_caught", "count": 1000}},
        {"achievement_id": "shiny_hunter", "name": "Shiny Hunter", "description": "*Catch a shiny Pokémon*",
         "icon": "\u2728", "points": 50, "rarity": "EPIC", "type": "automated",
         "trigger": {"type": "custom", "metric": "shinies_caught", "count": 1}},
        {"achievement_id": "shiny_charm", "name": "Shiny Charm", "description": "*Catch 10 shiny Pokémon*",
         "icon": "\U0001F31F", "points": 100, "rarity": "LEGENDARY", "type": "automated",
         "trigger": {"type": "custom", "metric": "shinies_caught", "count": 10}},
        {"achievement_id": "my_radiant_collection", "name": "My Radiant Collection",
         "description": "*Catch 50 shiny Pokémon*", "icon": "\U0001F4AB", "points": 150, "rarity": "MYTHIC",
         "type": "automated", "trigger": {"type": "custom", "metric": "shinies_caught", "count": 50}},
        {"achievement_id": "legendary_encounter", "name": "Legendary Encounter",
         "description": "*Catch a Legendary Pokémon*", "icon": "\U0001F531", "points": 50, "rarity": "EPIC",
         "type": "automated", "trigger": {"type": "custom", "metric": "legendaries_caught", "count": 1}},
        {"achievement_id": "legend_collector", "name": "Legend Collector",
         "description": "*Catch 10 Legendary Pokémon*", "icon": "\U0001F451", "points": 120,
         "rarity": "LEGENDARY", "type": "automated",
         "trigger": {"type": "custom", "metric": "legendaries_caught", "count": 10}},
        {"achievement_id": "mythical_discovery", "name": "Mythical Discovery",
         "description": "*Catch a Mythical Pokémon*", "icon": "\U0001FA7D", "points": 60, "rarity": "EPIC",
         "type": "automated", "trigger": {"type": "custom", "metric": "mythicals_caught", "count": 1}},
        {"achievement_id": "budding_researcher", "name": "Budding Researcher",
         "description": "*Catch 30 different Pokémon species*", "icon": "\U0001F4D6", "points": 15,
         "rarity": "COMMON", "type": "automated",
         "trigger": {"type": "custom", "metric": "pokedex_seen_count", "count": 30}},
        {"achievement_id": "halfway_there", "name": "Halfway There",
         "description": "*Catch 360 different Pokémon species*", "icon": "\U0001F4DA", "points": 50,
         "rarity": "EPIC", "type": "automated",
         "trigger": {"type": "custom", "metric": "pokedex_seen_count", "count": 360}},
        {"achievement_id": "pokedex_complete", "name": "Pokédex Complete",
         "description": "*Catch 721 different Pokémon species*", "icon": "\U0001F393", "points": 150,
         "rarity": "MYTHIC", "type": "automated",
         "trigger": {"type": "custom", "metric": "pokedex_seen_count", "count": 721}},
        {"achievement_id": "its_hatching", "name": "It's Hatching!", "description": "*Hatch a Pokémon egg*",
         "icon": "\U0001F95A", "points": 15, "rarity": "COMMON", "type": "automated",
         "trigger": {"type": "custom", "metric": "eggs_hatched", "count": 1}},
        {"achievement_id": "egg_incubator", "name": "Egg Incubator", "description": "*Hatch 25 Pokémon eggs*",
         "icon": "\U0001FA77", "points": 20, "rarity": "UNCOMMON", "type": "automated",
         "trigger": {"type": "custom", "metric": "eggs_hatched", "count": 25}},
        {"achievement_id": "breeding_expert", "name": "Breeding Expert", "description": "*Hatch 100 Pokémon eggs*",
         "icon": "\U0001F49E", "points": 30, "rarity": "RARE", "type": "automated",
         "trigger": {"type": "custom", "metric": "eggs_hatched", "count": 100}},
        {"achievement_id": "first_victory", "name": "First Victory",
         "description": "*Win a duel against another Pokémon trainer*", "icon": "\U0001F94A", "points": 15,
         "rarity": "COMMON", "type": "automated", "trigger": {"type": "custom", "metric": "battles_won", "count": 1}},
        {"achievement_id": "battle_veteran", "name": "Battle Veteran",
         "description": "*Win a duel against 25 other Pokémon trainers*", "icon": "\u2694\ufe0f", "points": 25,
         "rarity": "RARE", "type": "automated",
         "trigger": {"type": "custom", "metric": "battles_won", "count": 25}},
        {"achievement_id": "elite_trainer", "name": "Elite Trainer",
         "description": "*Win a duel against 100 other Pokémon trainers*", "icon": "\U0001F3C5", "points": 50,
         "rarity": "EPIC", "type": "automated",
         "trigger": {"type": "custom", "metric": "battles_won", "count": 100}},
    ]

    del _BALL

del _ACHIEVEMENT_PRESETS
