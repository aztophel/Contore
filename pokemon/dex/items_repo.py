"""The item catalog: display name, emoji, price, description, and (where
applicable) mechanical hooks like fling power for every non-TM item Contore
sells or lets a Pokémon hold.

This is design/balance data the bot's authors chose (prices, which emoji
asset represents which item) rather than something PokeAPI or Showdown can
hand us "for free", so — unlike `pokedex.json`/`moves_data.json` — it lives
here as plain Python literals rather than a builder-generated JSON file.
`pokedex_builder.py` only contributes the `fling` mechanical data merged in
at the bottom of this module from `data/items_meta.json`.

Every catalog table below carries a `"gen"` key: the generation an item was
first introduced in. `available_in(ruleset)` / `ItemCatalog.for_ruleset()`
use it so a server capped at, say, Generation 3 won't see Heal Balls (Gen 4)
in its shop. This mirrors `pokemon.generations.ruleset.GenerationRuleset`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from discord import PartialEmoji

from pokemon.generations.ruleset import GenerationRuleset

ItemTable = dict[str, dict[str, Any]]

# ---------------------------------------------------------------------------
# Poké Balls
# ---------------------------------------------------------------------------
POKEBALL_EMOJI: dict[str, str] = {
    "Poké Ball":    "<:poke_ball:1519185387879272488>",    "Great Ball":  "<:great_ball:1519185393306566727>",  "Ultra Ball":   "<:ultra_ball:1519185399476650036>",
    "Master Ball":  "<:master_ball:1519185404685975643>",  "Safari Ball": "<:safari_ball:1519185411052671238>", "Level Ball":   "<:level_ball:1519185433039474798>",
    "Lure Ball":    "<:lure_ball:1519185444481532004>",    "Moon Ball":   "<:moon_ball:1519185449263042570>",   "Friend Ball":  "<:friend_ball:1519185421802803235>",
    "Love Ball":    "<:love_ball:1519185437464465500>",    "Heavy Ball":  "<:heavy_ball:1519185427389481100>",  "Fast Ball":    "<:fast_ball:1519185416689815552>",
    "Premier Ball": "<:premier_ball:1519185489549070380>", "Repeat Ball": "<:repeat_ball:1519185473384222730>", "Timer Ball":   "<:timer_ball:1519185479461765161>",
    "Nest Ball":    "<:nest_ball:1519185466367414334>",    "Net Ball":    "<:net_ball:1519185455290253403>",    "Dive Ball":    "<:dive_ball:1519185461317472426>",
    "Luxury Ball":  "<:luxury_ball:1519185484763631717>",  "Heal Ball":   "<:heal_ball:1519185501637185666>",   "Quick Ball":   "<:quick_ball:1519185508607987732>",
    "Dusk Ball":    "<:dusk_ball:1519185495786127402>",    "Dream Ball":  "<:dream_ball:1519185513876029550>",  "Cherish Ball": "<:cherish_ball:1519185969310339092>",
}

POKEBALL_DATA: ItemTable = {
    # name:  price(₽), base_mult, cond-key,  gen
    "Poké Ball":    {"price": 200,   "base_mult": 1.0,   "cond": None,         "gen": 1, "emoji": POKEBALL_EMOJI["Poké Ball"],    "desc": "A standard Poké Ball."},
    "Great Ball":   {"price": 600,   "base_mult": 1.5,   "cond": None,         "gen": 1, "emoji": POKEBALL_EMOJI["Great Ball"],   "desc": "Better catch rate than a Poké Ball."},
    "Ultra Ball":   {"price": 1200,  "base_mult": 2.0,   "cond": None,         "gen": 1, "emoji": POKEBALL_EMOJI["Ultra Ball"],   "desc": "Even better catch rate."},
    "Master Ball":  {"price": None,  "base_mult": 255.0, "cond": None,         "gen": 1, "emoji": POKEBALL_EMOJI["Master Ball"],  "desc": "Always catches. The peak of Poké Ball technology."},
    "Safari Ball":  {"price": None,  "base_mult": 1.5,   "cond": None,         "gen": 1, "emoji": POKEBALL_EMOJI["Safari Ball"],  "desc": "Special Safari Zone ball."},
    "Fast Ball":    {"price": 1000,  "base_mult": 4.0,   "cond": "fast",       "gen": 2, "emoji": POKEBALL_EMOJI["Fast Ball"],    "desc": "4× vs Pokémon with ≥100 base Speed."},
    "Friend Ball":  {"price": 1000,  "base_mult": 1.0,   "cond": None,         "gen": 2, "emoji": POKEBALL_EMOJI["Friend Ball"],  "desc": "Sets caught Pokémon's friendship to 200."},
    "Heavy Ball":   {"price": 1000,  "base_mult": 1.0,   "cond": "heavy",      "gen": 2, "emoji": POKEBALL_EMOJI["Heavy Ball"],   "desc": "Increases catch modifier by +10 (≥100 kg), +20 (≥200 kg), +30 (≥300 kg), −20 if under 10 kg."},
    "Level Ball":   {"price": 1000,  "base_mult": 1.0,   "cond": "level",      "gen": 2, "emoji": POKEBALL_EMOJI["Level Ball"],   "desc": "Up to 8× if your Pokémon outlevels the wild one."},
    "Love Ball":    {"price": 1000,  "base_mult": 8.0,   "cond": "same_sp",    "gen": 2, "emoji": POKEBALL_EMOJI["Love Ball"],    "desc": "8× vs the same species and opposite gender as your active Pokémon."},
    "Lure Ball":    {"price": 1000,  "base_mult": 3.0,   "cond": "water",      "gen": 2, "emoji": POKEBALL_EMOJI["Lure Ball"],    "desc": "3× vs Water-type Pokémon."},
    "Moon Ball":    {"price": 1000,  "base_mult": 4.0,   "cond": "moon_evo",   "gen": 2, "emoji": POKEBALL_EMOJI["Moon Ball"],    "desc": "4× vs Pokémon that evolve with a Moon Stone."},
    "Net Ball":     {"price": 1000,  "base_mult": 3.5,   "cond": "water_bug",  "gen": 3, "emoji": POKEBALL_EMOJI["Net Ball"],     "desc": "3.5× vs Water or Bug types."},
    "Dive Ball":    {"price": 1000,  "base_mult": 1.0,   "cond": None,         "gen": 3, "emoji": POKEBALL_EMOJI["Dive Ball"],    "desc": "Decorative ball."},
    "Nest Ball":    {"price": 1000,  "base_mult": 1.0,   "cond": "weak",       "gen": 3, "emoji": POKEBALL_EMOJI["Nest Ball"],    "desc": "Better rates vs low-level Pokémon (max 4×)."},
    "Repeat Ball":  {"price": 1000,  "base_mult": 3.5,   "cond": "caught",     "gen": 3, "emoji": POKEBALL_EMOJI["Repeat Ball"],  "desc": "3.5× if you've already caught that species."},
    "Timer Ball":   {"price": 1000,  "base_mult": 1.0,   "cond": "turns",      "gen": 3, "emoji": POKEBALL_EMOJI["Timer Ball"],   "desc": "Grows stronger each turn, up to 4× by turn 11."},
    "Luxury Ball":  {"price": 3000,  "base_mult": 1.0,   "cond": None,         "gen": 3, "emoji": POKEBALL_EMOJI["Luxury Ball"],  "desc": "Doubles friendship gained after capture."},
    "Premier Ball": {"price": None,  "base_mult": 1.0,   "cond": None,         "gen": 3, "emoji": POKEBALL_EMOJI["Premier Ball"], "desc": "Bonus ball. Same as Poké Ball."},
    "Dusk Ball":    {"price": 1000,  "base_mult": 3.5,   "cond": "night",      "gen": 4, "emoji": POKEBALL_EMOJI["Dusk Ball"],    "desc": "3.5× at night (8 PM–6 AM) or in caves."},
    "Heal Ball":    {"price": 300,   "base_mult": 1.0,   "cond": None,         "gen": 4, "emoji": POKEBALL_EMOJI["Heal Ball"],    "desc": "Fully heals and cures status on capture."},
    "Quick Ball":   {"price": 1000,  "base_mult": 5.0,   "cond": "first_turn", "gen": 4, "emoji": POKEBALL_EMOJI["Quick Ball"],   "desc": "5× on the first turn of battle."},
    "Dream Ball":   {"price": None,  "base_mult": 255.0, "cond": "asleep",     "gen": 5, "emoji": POKEBALL_EMOJI["Dream Ball"],   "desc": "Guaranteed catch vs sleeping Pokémon."},
    "Cherish Ball": {"price": None,  "base_mult": 1.0,   "cond": None,         "gen": 5, "emoji": POKEBALL_EMOJI["Cherish Ball"], "desc": "Event-exclusive ball."},
}
BUYABLE_BALLS = [n for n, d in POKEBALL_DATA.items() if d["price"] is not None]
UNINHERITABLE_BALLS = frozenset({"Master Ball", "Cherish Ball"})

# ---------------------------------------------------------------------------
# Battle items (usable in and out of battle)
# ---------------------------------------------------------------------------
BATTLE_ITEMS: ItemTable = {
    "Potion":          {"price": 300,   "emoji": PartialEmoji(name="potion",        id=1519185975195074730), "type": "heal",    "hp": 20,   "gen": 1, "desc": "Restores 20 HP."},
    "Super Potion":    {"price": 700,   "emoji": PartialEmoji(name="super_potion",  id=1519185978957234196), "type": "heal",    "hp": 50,   "gen": 1, "desc": "Restores 50 HP."},
    "Hyper Potion":    {"price": 1500,  "emoji": PartialEmoji(name="hyper_potion",  id=1519185982732112033), "type": "heal",    "hp": 200,  "gen": 1, "desc": "Restores 200 HP."},
    "Max Potion":      {"price": 3000,  "emoji": PartialEmoji(name="max_potion",    id=1519185986410778685), "type": "heal",    "hp": 9999, "gen": 1, "desc": "Fully restores HP."},
    "Full Restore":    {"price": 5000,  "emoji": PartialEmoji(name="full_restore",  id=1519185989895979019), "type": "full",    "hp": 9999, "gen": 1, "desc": "Fully restores HP and cures status."},
    "Revive":          {"price": 1500,  "emoji": PartialEmoji(name="revive",        id=1519185994769891471), "type": "revive",              "gen": 1, "desc": "Revives a fainted Pokémon to half HP."},
    "Max Revive":      {"price": 4000,  "emoji": PartialEmoji(name="max_revive",    id=1519185999878426664), "type": "max_revive",          "gen": 2, "desc": "Revives a fainted Pokémon to full HP."},
    "Antidote":        {"price": 100,   "emoji": PartialEmoji(name="antidote",      id=1519186004198690866), "type": "cure",   "cures": "poison",    "gen": 1, "desc": "Cures poison."},
    "Burn Heal":       {"price": 250,   "emoji": PartialEmoji(name="burn_heal",     id=1519186008216961106), "type": "cure",   "cures": "burn",      "gen": 1, "desc": "Cures burn."},
    "Ice Heal":        {"price": 250,   "emoji": PartialEmoji(name="ice_heal",      id=1519186012734095444), "type": "cure",   "cures": "freeze",    "gen": 1, "desc": "Cures freeze."},
    "Awakening":       {"price": 250,   "emoji": PartialEmoji(name="awakening",     id=1519186016735592590), "type": "cure",   "cures": "sleep",     "gen": 1, "desc": "Cures sleep."},
    "Paralyze Heal":   {"price": 200,   "emoji": PartialEmoji(name="paralyze_heal", id=1519186020929638451), "type": "cure",   "cures": "paralysis", "gen": 1, "desc": "Cures paralysis."},
    "Full Heal":       {"price": 600,   "emoji": PartialEmoji(name="full_heal",     id=1519186024889188403), "type": "cure",   "cures": "all",       "gen": 1, "desc": "Cures any status condition."},
    "Ether":           {"price": 1200,  "emoji": PartialEmoji(name="ether",         id=1519186028823318610), "type": "pp",     "pp": 10,   "gen": 1, "desc": "Restores 10 PP to one move."},
    "Max Ether":       {"price": 2000,  "emoji": PartialEmoji(name="max_ether",     id=1519186034196349068), "type": "pp",     "pp": 9999, "gen": 1, "desc": "Fully restores PP of one move."},
    "Elixir":          {"price": 3000,  "emoji": PartialEmoji(name="elixir",        id=1519186037425963061), "type": "pp_all", "pp": 10,   "gen": 1, "desc": "Restores 10 PP to all moves."},
    "Max Elixir":      {"price": 5000,  "emoji": PartialEmoji(name="max_elixir",    id=1519186043046199437), "type": "pp_all", "pp": 9999, "gen": 2, "desc": "Fully restores PP of all moves."},
    "X Attack":        {"price": 1000,  "emoji": PartialEmoji(name="x_attack",      id=1519186048834469948), "type": "stat",   "stat": "atk", "stages": 1, "gen": 1, "desc": "+1 Attack stage this battle."},
    "X Defense":       {"price": 1000,  "emoji": PartialEmoji(name="x_defense",     id=1519186055893618828), "type": "stat",   "stat": "def", "stages": 1, "gen": 1, "desc": "+1 Defense stage this battle."},
    "X Sp. Atk":       {"price": 1000,  "emoji": PartialEmoji(name="x_sp_atk",      id=1519186061815844975), "type": "stat",   "stat": "spa", "stages": 1, "gen": 1, "desc": "+1 Sp. Atk stage this battle."},
    "X Sp. Def":       {"price": 1000,  "emoji": PartialEmoji(name="x_sp_def",      id=1519186067335544876), "type": "stat",   "stat": "spd", "stages": 1, "gen": 3, "desc": "+1 Sp. Def stage this battle."},
    "X Speed":         {"price": 1000,  "emoji": PartialEmoji(name="x_speed",       id=1519186074247626872), "type": "stat",   "stat": "spe", "stages": 1, "gen": 1, "desc": "+1 Speed stage this battle."},
    "X Accuracy":      {"price": 1000,  "emoji": PartialEmoji(name="x_accuracy",    id=1519186080627429506), "type": "stat",   "stat": "acc", "stages": 1, "gen": 1, "desc": "+1 Accuracy stage this battle."},
    "Mega Ring":       {"price": 10000, "emoji": PartialEmoji(name="mega_ring",     id=1519186092681592923), "type": "gear",                  "gen": 6, "desc": "Required to Mega Evolve in battle."},
}

# ---------------------------------------------------------------------------
# Held items
# ---------------------------------------------------------------------------
HELD_ITEMS: ItemTable = {
    "Leftovers":       {"emoji": PartialEmoji(name="leftovers",       id=1519186100889845810), "price": 8000,  "gen": 2, "desc": "Restores 1/16 max HP at the end of each turn."},
    "Choice Band":     {"emoji": PartialEmoji(name="choice_band",     id=1519186110507651085), "price": 12000, "gen": 3, "desc": "1.5× Attack, but locks you into the first move used."},
    "Choice Specs":    {"emoji": PartialEmoji(name="choice_specs",    id=1519186120347222137), "price": 12000, "gen": 4, "desc": "1.5× Sp. Atk, but locks you into the first move used."},
    "Choice Scarf":    {"emoji": PartialEmoji(name="choice_scarf",    id=1519186128454815864), "price": 12000, "gen": 4, "desc": "1.5× Speed, but locks you into the first move used."},
    "Life Orb":        {"emoji": PartialEmoji(name="life_orb",        id=1519186136445091861), "price": 12000, "gen": 4, "desc": "1.3× damage dealt, lose 1/10 max HP after attacking."},
    "Rocky Helmet":    {"emoji": PartialEmoji(name="rocky_helmet",    id=1519186143608963204), "price": 8000,  "gen": 5, "desc": "Contact attackers lose 1/6 of their max HP."},
    "Focus Sash":      {"emoji": PartialEmoji(name="focus_sash",      id=1519186151976730745), "price": 6000,  "gen": 4, "desc": "Survive a KO from full HP with 1 HP, once."},
    "Focus Band":      {"emoji": PartialEmoji(name="focus_band",      id=1522722476352602122), "price": 4000,  "gen": 2, "desc": "10% chance to survive a KO with 1 HP."},
    "Sitrus Berry":    {"emoji": PartialEmoji(name="sitrus_berry",    id=1519186159647985695), "price": 2000,  "gen": 3, "desc": "Restores 1/4 max HP once below half, once."},
    "Black Sludge":    {"emoji": PartialEmoji(name="black_sludge",    id=1519186167453716520), "price": 8000,  "gen": 4, "desc": "1/16 max HP heal for Poison types, 1/8 damage otherwise."},
    "Weakness Policy": {"emoji": PartialEmoji(name="weakness_policy", id=1519186176110493716), "price": 6000,  "gen": 5, "desc": "+2 Atk/Sp.Atk after taking a super-effective hit, once."},
    "Expert Belt":     {"emoji": PartialEmoji(name="expert_belt",     id=1519186185581363300), "price": 8000,  "gen": 4, "desc": "1.2× damage on super-effective hits."},
    "Air Balloon":     {"emoji": PartialEmoji(name="air_balloon",     id=1519526517988917380), "price": 1000,  "gen": 5, "desc": "Holder is immune to Ground moves and unaffected by terrain. Pops when hit."},
    "Eviolite":        {"emoji": PartialEmoji(name="eviolite",        id=1519186193827368991), "price": 6000,  "gen": 4, "desc": "1.5× Def/Sp.Def for Pokémon that can still evolve."},
    "Everstone":       {"emoji": PartialEmoji(name="everstone",       id=1519526528596054207), "price": 3000,  "gen": 1, "desc": "Suppresses evolution and passes the holder's Nature to any offspring."},
    "Destiny Knot":    {"emoji": PartialEmoji(name="destiny_knot",    id=1519526538582818939), "price": 5000,  "gen": 4, "desc": "When held by a breeding parent, 5 IVs are passed instead of 3."},
    "Guise Charm":     {"emoji": "🪬",                                                         "price": 1000,  "gen": 6, "desc": "Give to a Pokémon before it evolves to keep its current appearance. Consumed on use."},
    "Exp. Share":      {"emoji": PartialEmoji(name="exp_share",       id=1522722460712042517), "price": None,  "gen": 1, "desc": "Holder earns a portion of Exp. even when it didn't participate in battle."},
    "Blue Orb":        {"emoji": PartialEmoji(name="blue_orb",        id=1519186201641226532), "price": 30000, "gen": 6, "desc": "Triggers Kyogre's Primal Reversion and Primordial Sea when held in battle."},
    "Red Orb":         {"emoji": PartialEmoji(name="red_orb",         id=1519186210432614460), "price": 30000, "gen": 6, "desc": "Triggers Groudon's Primal Reversion and Desolate Land when held in battle."},
    "Griseous Orb":    {"emoji": PartialEmoji(name="griseous_orb",    id=1519186218787799051), "price": 15000, "gen": 4, "desc": "Shifts Giratina into Origin Forme when held."},
    "Flame Orb":       {"emoji": PartialEmoji(name="flame_orb",       id=1522722349324042310), "price": 10000, "gen": 4, "desc": "Inflicts burn on the holder at end of each turn."},
    "Toxic Orb":       {"emoji": PartialEmoji(name="toxic_orb",       id=1522722359205953638), "price": 10000, "gen": 4, "desc": "Badly poisons the holder at end of each turn."},
    "White Herb":      {"emoji": PartialEmoji(name="white_herb",      id=1522722368953254000), "price": 2000,  "gen": 3, "desc": "Restores any lowered stats once, then consumed."},
    "Power Herb":      {"emoji": PartialEmoji(name="power_herb",      id=1522722378034184364), "price": 2000,  "gen": 4, "desc": "Allows skipping the charge turn of two-turn moves once."},
    "Mental Herb":     {"emoji": PartialEmoji(name="mental_herb",     id=1522722440663400591), "price": 2000,  "gen": 3, "desc": "Cures Taunt, Encore, Attract, Disable, Torment, or Heal Block once."},
    "Red Card":        {"emoji": PartialEmoji(name="red_card",        id=1522722387626561714), "price": 2000,  "gen": 4, "desc": "Forces the attacker to switch out when the holder is struck."},
    "Eject Button":    {"emoji": PartialEmoji(name="eject_button",    id=1522722396006650038), "price": 2000,  "gen": 4, "desc": "Holder switches out immediately when hit by a move."},
    "Ring Target":     {"emoji": PartialEmoji(name="ring_target",     id=1522722405032788179), "price": 2000,  "gen": 5, "desc": "Holder loses all type immunities."},
    "Assault Vest":    {"emoji": PartialEmoji(name="assault_vest",    id=1522722413220200469), "price": 12000, "gen": 6, "desc": "1.5× Sp.Def; holder cannot select status moves."},
    "Scope Lens":      {"emoji": PartialEmoji(name="scope_lens",      id=1522722422858584064), "price": 8000,  "gen": 2, "desc": "Raises the critical-hit ratio by 1 stage."},
    "Razor Claw":      {"emoji": PartialEmoji(name="razor_claw",      id=1522722432337576046), "price": 5000,  "gen": 4, "desc": "Raises the critical-hit ratio by 1 stage."},
    "Bright Powder":   {"emoji": PartialEmoji(name="bright_powder",   id=1522722448154562630), "price": 3000,  "gen": 2, "desc": "Lowers the accuracy of opposing moves by ~10%."},
    "Charcoal":        {"emoji": PartialEmoji(name="charcoal",        id=1525763940095627354), "price": 3000,  "gen": 2, "desc": "Powers up Fire-type moves by 20%."},
    "Mystic Water":    {"emoji": PartialEmoji(name="mystic_water",    id=1525763947532128326), "price": 3000,  "gen": 2, "desc": "Powers up Water-type moves by 20%."},
    "Miracle Seed":    {"emoji": PartialEmoji(name="miracle_seed",    id=1525763955258294325), "price": 3000,  "gen": 2, "desc": "Powers up Grass-type moves by 20%."},
    "Magnet":          {"emoji": PartialEmoji(name="magnet",          id=1525763963067957258), "price": 3000,  "gen": 2, "desc": "Powers up Electric-type moves by 20%."},
    "Never-Melt Ice":  {"emoji": PartialEmoji(name="never_melt_ice",  id=1525763970227765318), "price": 3000,  "gen": 2, "desc": "Powers up Ice-type moves by 20%."},
    "Black Belt":      {"emoji": PartialEmoji(name="black_belt",      id=1525763978674835506), "price": 3000,  "gen": 2, "desc": "Powers up Fighting-type moves by 20%."},
    "Poison Barb":     {"emoji": PartialEmoji(name="poison_barb",     id=1525763987176947903), "price": 3000,  "gen": 2, "desc": "Powers up Poison-type moves by 20%."},
    "Soft Sand":       {"emoji": PartialEmoji(name="soft_sand",       id=1525763996156956702), "price": 3000,  "gen": 2, "desc": "Powers up Ground-type moves by 20%."},
    "Sharp Beak":      {"emoji": PartialEmoji(name="sharp_beak",      id=1525764004654612500), "price": 3000,  "gen": 2, "desc": "Powers up Flying-type moves by 20%."},
    "Twisted Spoon":   {"emoji": PartialEmoji(name="twisted_spoon",   id=1525764012913070172), "price": 3000,  "gen": 2, "desc": "Powers up Psychic-type moves by 20%."},
    "Silver Powder":   {"emoji": PartialEmoji(name="silver_powder",   id=1525764023050571866), "price": 3000,  "gen": 2, "desc": "Powers up Bug-type moves by 20%."},
    "Hard Stone":      {"emoji": PartialEmoji(name="hard_stone",      id=1525764032433356890), "price": 3000,  "gen": 2, "desc": "Powers up Rock-type moves by 20%."},
    "Spell Tag":       {"emoji": PartialEmoji(name="spell_tag",       id=1525764042000437358), "price": 3000,  "gen": 2, "desc": "Powers up Ghost-type moves by 20%."},
    "Dragon Fang":     {"emoji": PartialEmoji(name="dragon_fang",     id=1525764050750017586), "price": 3000,  "gen": 2, "desc": "Powers up Dragon-type moves by 20%."},
    "Black Glasses":   {"emoji": PartialEmoji(name="black_glasses",   id=1525764060535066775), "price": 3000,  "gen": 2, "desc": "Powers up Dark-type moves by 20%."},
    "Silk Scarf":      {"emoji": PartialEmoji(name="silk_scarf",      id=1525764070542934190), "price": 3000,  "gen": 2, "desc": "Powers up Normal-type moves by 20%."},
    "Metal Coat":      {"emoji": PartialEmoji(name="metal_coat",      id=1519187639650095155), "price": 8000,  "gen": 2, "desc": "Powers up Steel-type moves by 20%."},
    "Razor Fang":      {"emoji": PartialEmoji(name="razor_fang",      id=1525764107582701689), "price": 4000,  "gen": 4, "desc": "May cause the target to flinch when its holder attacks."},
    "Sea Incense":     {"emoji": PartialEmoji(name="sea_incense",     id=1519527099311067246), "price": 2000,  "gen": 3, "desc": "Held by a parent to produce Azurill instead of Marill."},
    "Lax Incense":     {"emoji": PartialEmoji(name="lax_incense",     id=1519527115924705441), "price": 2000,  "gen": 3, "desc": "Held by a parent to produce Wynaut instead of Wobbuffet."},
    "Rose Incense":    {"emoji": PartialEmoji(name="rose_incense",    id=1519527130541719652), "price": 2000,  "gen": 4, "desc": "Held by a parent to produce Budew instead of Roselia."},
    "Pure Incense":    {"emoji": PartialEmoji(name="pure_incense",    id=1519527143460049097), "price": 2000,  "gen": 4, "desc": "Held by a parent to produce Chingling instead of Chimecho."},
    "Rock Incense":    {"emoji": PartialEmoji(name="rock_incense",    id=1519527157746106561), "price": 2000,  "gen": 4, "desc": "Held by a parent to produce Bonsly instead of Sudowoodo."},
    "Odd Incense":     {"emoji": PartialEmoji(name="odd_incense",     id=1519527172665118900), "price": 2000,  "gen": 4, "desc": "Held by a parent to produce Mime Jr. instead of Mr. Mime."},
    "Luck Incense":    {"emoji": PartialEmoji(name="luck_incense",    id=1519527190004498657), "price": 2000,  "gen": 4, "desc": "Held by a parent to produce Happiny instead of Chansey."},
    "Wave Incense":    {"emoji": PartialEmoji(name="wave_incense",    id=1519527206286655670), "price": 2000,  "gen": 4, "desc": "Held by a parent to produce Mantyke instead of Mantine."},
    "Full Incense":    {"emoji": PartialEmoji(name="full_incense",    id=1519527219452448930), "price": 2000,  "gen": 4, "desc": "Held by a parent to produce Munchlax instead of Snorlax."},
}

TYPE_BOOST_ITEMS: dict[str, str] = {
    "Charcoal": "Fire", "Mystic Water": "Water", "Miracle Seed": "Grass",
    "Magnet": "Electric", "Never-Melt Ice": "Ice", "Black Belt": "Fighting",
    "Poison Barb": "Poison", "Soft Sand": "Ground", "Sharp Beak": "Flying",
    "Twisted Spoon": "Psychic", "Silver Powder": "Bug", "Hard Stone": "Rock",
    "Spell Tag": "Ghost", "Dragon Fang": "Dragon", "Black Glasses": "Dark",
    "Metal Coat": "Steel", "Silk Scarf": "Normal", "Fairy Feather": "Fairy",
}
CHOICE_ITEM_STAT = {"Choice Band": "atk", "Choice Specs": "spa", "Choice Scarf": "spe"}
POWER_ITEM_IV_STAT: dict[str, str] = {
    "Power Weight": "hp", "Power Bracer": "atk", "Power Belt": "def",
    "Power Lens": "spa", "Power Band": "spd", "Power Anklet": "spe",
}

# ---------------------------------------------------------------------------
# Evolution items
# ---------------------------------------------------------------------------
EVOLUTION_ITEMS: ItemTable = {
    "Fire Stone":    {"emoji": PartialEmoji(name="fire_stone",    id=1519187542799548617), "price": 5000, "gen": 1, "desc": "Evolves Vulpix, Growlithe, Eevee, and others."},
    "Water Stone":   {"emoji": PartialEmoji(name="water_stone",   id=1519187551108333580), "price": 5000, "gen": 1, "desc": "Evolves Poliwhirl, Shellder, Staryu, Eevee, and others."},
    "Thunder Stone": {"emoji": PartialEmoji(name="thunder_stone", id=1519187560662827088), "price": 5000, "gen": 1, "desc": "Evolves Pikachu, Eevee, Eelektrik, and others."},
    "Leaf Stone":    {"emoji": PartialEmoji(name="leaf_stone",    id=1519187569105965178), "price": 5000, "gen": 1, "desc": "Evolves Gloom, Weepinbell, Exeggcute, Nuzleaf, and others."},
    "Moon Stone":    {"emoji": PartialEmoji(name="moon_stone",    id=1519187579562360932), "price": 5000, "gen": 1, "desc": "Evolves Clefairy, Jigglypuff, Nidorino, Nidorina, Skitty, and others."},
    "Sun Stone":     {"emoji": PartialEmoji(name="sun_stone",     id=1519187592728412242), "price": 5000, "gen": 2, "desc": "Evolves Gloom, Sunkern, Cottonee, Petilil, Helioptile, and others."},
    "Shiny Stone":   {"emoji": PartialEmoji(name="shiny_stone",   id=1519187602157076552), "price": 8000, "gen": 4, "desc": "Evolves Togetic, Roselia, Minccino, Floette."},
    "Dusk Stone":    {"emoji": PartialEmoji(name="dusk_stone",    id=1519187613544747008), "price": 8000, "gen": 4, "desc": "Evolves Murkrow, Misdreavus, Lampent, Doublade."},
    "Dawn Stone":    {"emoji": PartialEmoji(name="dawn_stone",    id=1519187622356844547), "price": 8000, "gen": 4, "desc": "Evolves male Kirlia or female Snorunt."},
}
TRADE_EVOLUTION_ITEMS: ItemTable = {
    "King's Rock":    {"emoji": PartialEmoji(name="kings_rock",     id=1519187631311818853), "price": 8000,  "gen": 2, "slug": "kings-rock",     "desc": "Evolves a Slowpoke/Poliwhirl into a Slowking/Politoed upon trade."},
    "Metal Coat":     {"emoji": PartialEmoji(name="metal_coat",     id=1519187639650095155), "price": 8000,  "gen": 2, "slug": "metal-coat",     "desc": "Evolves an Onix/Scyther into a Steelix/Scizor upon trade."},
    "Dragon Scale":   {"emoji": PartialEmoji(name="dragon_scale",   id=1519187650488041524), "price": 8000,  "gen": 2, "slug": "dragon-scale",   "desc": "Evolves a Seadra into a Kingdra upon trade."},
    "Up-Grade":       {"emoji": PartialEmoji(name="up_grade",       id=1519187660504043594), "price": 8000,  "gen": 2, "slug": "up-grade",       "desc": "Evolves a Porygon into a Porygon 2 upon trade."},
    "Dubious Disc":   {"emoji": PartialEmoji(name="dubious_disc",   id=1519187668519485610), "price": 12000, "gen": 4, "slug": "dubious-disc",   "desc": "Evolves a Porygon 2 into a Porygon-Z upon trade."},
    "Protector":      {"emoji": PartialEmoji(name="protector",      id=1519187678011199519), "price": 8000,  "gen": 4, "slug": "protector",      "desc": "Evolves a Rhydon into a Rhyperior upon trade."},
    "Electirizer":    {"emoji": PartialEmoji(name="electirizer",    id=1519187687498846369), "price": 8000,  "gen": 4, "slug": "electirizer",    "desc": "Evolves an Electabuzz into an Electivire upon trade."},
    "Magmarizer":     {"emoji": PartialEmoji(name="magmarizer",     id=1519187698500370662), "price": 8000,  "gen": 4, "slug": "magmarizer",     "desc": "Evolves a Magmar into a Magmortar upon trade."},
    "Reaper Cloth":   {"emoji": PartialEmoji(name="reaper_cloth",   id=1519187707119800404), "price": 8000,  "gen": 4, "slug": "reaper-cloth",   "desc": "Evolves a Dusclops into a Dusknoir upon trade."},
    "Deep Sea Tooth": {"emoji": PartialEmoji(name="deep_sea_tooth", id=1519187717806620775), "price": 8000,  "gen": 3, "slug": "deep-sea-tooth", "desc": "Evolves a Clamperl into a Huntail upon trade."},
    "Deep Sea Scale": {"emoji": PartialEmoji(name="deep_sea_scale", id=1519187728091320471), "price": 8000,  "gen": 3, "slug": "deep-sea-scale", "desc": "Evolves a Clamperl into a Gorebyss upon trade."},
    "Prism Scale":    {"emoji": PartialEmoji(name="prism_scale",    id=1519187744788840579), "price": 8000,  "gen": 5, "slug": "prism-scale",    "desc": "Evolves a Feebas into a Milotic upon trade."},
    "Sachet":         {"emoji": PartialEmoji(name="sachet",         id=1519187754985197628), "price": 8000,  "gen": 6, "slug": "sachet",         "desc": "Evolves a Spritzee into an Aromatisse upon trade."},
    "Whipped Dream":  {"emoji": PartialEmoji(name="whipped_dream",  id=1519187764808122498), "price": 8000,  "gen": 6, "slug": "whipped-dream",  "desc": "Evolves a Swirlix into a Slurpuff upon trade."},
}

# ---------------------------------------------------------------------------
# Form-change items
# ---------------------------------------------------------------------------
FORM_ITEMS: ItemTable = {
    "Gracidea":      {"emoji": PartialEmoji(name="gracidea",      id=1519186229340405910), "price": 1000,  "gen": 4, "desc": "Changes Shaymin to Sky Forme during daytime (4–20h). Consumed on use."},
    "DNA Splicers":  {"emoji": PartialEmoji(name="dna_splicers",  id=1519186240522682441), "price": 50000, "gen": 5, "desc": "Fuses Kyurem with a Reshiram or Zekrom you own into White/Black Kyurem, or separates it back."},
    "Reveal Glass":  {"emoji": PartialEmoji(name="reveal_glass",  id=1519186254858555453), "price": 30000, "gen": 5, "desc": "Switches Tornadus/Thundurus/Landorus between Incarnate and Therian formes."},
    "Prison Bottle": {"emoji": PartialEmoji(name="prison_bottle", id=1519186267055718502), "price": 30000, "gen": 6, "desc": "Switches Hoopa between Confined and Unbound formes."},
    "Meteorite":     {"emoji": PartialEmoji(name="meteorite",     id=1519186281756885102), "price": 30000, "gen": 3, "desc": "Cycles Deoxys between Normal, Attack, Defense, and Speed formes."},
}
ARCEUS_PLATES: ItemTable = {
    "Flame Plate":  {"emoji": PartialEmoji(name="flame_plate",  id=1519187288603627590), "price": 20000, "gen": 4, "desc": "Changes Arceus to Fire type."},
    "Splash Plate": {"emoji": PartialEmoji(name="splash_plate", id=1519187297978028052), "price": 20000, "gen": 4, "desc": "Changes Arceus to Water type."},
    "Zap Plate":    {"emoji": PartialEmoji(name="zap_plate",    id=1519187312993374369), "price": 20000, "gen": 4, "desc": "Changes Arceus to Electric type."},
    "Meadow Plate": {"emoji": PartialEmoji(name="meadow_plate", id=1519187323764604978), "price": 20000, "gen": 4, "desc": "Changes Arceus to Grass type."},
    "Icicle Plate": {"emoji": PartialEmoji(name="icicle_plate", id=1519187335936348190), "price": 20000, "gen": 4, "desc": "Changes Arceus to Ice type."},
    "Fist Plate":   {"emoji": PartialEmoji(name="fist_plate",   id=1519187351308337255), "price": 20000, "gen": 4, "desc": "Changes Arceus to Fighting type."},
    "Toxic Plate":  {"emoji": PartialEmoji(name="toxic_plate",  id=1519187365078499389), "price": 20000, "gen": 4, "desc": "Changes Arceus to Poison type."},
    "Earth Plate":  {"emoji": PartialEmoji(name="earth_plate",  id=1519187380253491312), "price": 20000, "gen": 4, "desc": "Changes Arceus to Ground type."},
    "Sky Plate":    {"emoji": PartialEmoji(name="sky_plate",    id=1519187393184399520), "price": 20000, "gen": 4, "desc": "Changes Arceus to Flying type."},
    "Mind Plate":   {"emoji": PartialEmoji(name="mind_plate",   id=1519187406887063584), "price": 20000, "gen": 4, "desc": "Changes Arceus to Psychic type."},
    "Insect Plate": {"emoji": PartialEmoji(name="insect_plate", id=1519187418996015166), "price": 20000, "gen": 4, "desc": "Changes Arceus to Bug type."},
    "Stone Plate":  {"emoji": PartialEmoji(name="stone_plate",  id=1519187432145293382), "price": 20000, "gen": 4, "desc": "Changes Arceus to Rock type."},
    "Spooky Plate": {"emoji": PartialEmoji(name="spooky_plate", id=1519187444384272495), "price": 20000, "gen": 4, "desc": "Changes Arceus to Ghost type."},
    "Draco Plate":  {"emoji": PartialEmoji(name="draco_plate",  id=1519187456585633904), "price": 20000, "gen": 4, "desc": "Changes Arceus to Dragon type."},
    "Dread Plate":  {"emoji": PartialEmoji(name="dread_plate",  id=1519187470481096754), "price": 20000, "gen": 4, "desc": "Changes Arceus to Dark type."},
    "Iron Plate":   {"emoji": PartialEmoji(name="iron_plate",   id=1519187483298893968), "price": 20000, "gen": 4, "desc": "Changes Arceus to Steel type."},
    "Pixie Plate":  {"emoji": PartialEmoji(name="pixie_plate",  id=1519187497333035048), "price": 20000, "gen": 6, "desc": "Changes Arceus to Fairy type."},
}
GENESECT_DRIVES: ItemTable = {
    "Burn Drive":  {"emoji": PartialEmoji(name="burn_drive",  id=1519187505755459665), "price": 15000, "gen": 5, "desc": "Changes Genesect's subtype; alters Techno Blast."},
    "Chill Drive": {"emoji": PartialEmoji(name="chill_drive", id=1519187513837752360), "price": 15000, "gen": 5, "desc": "Changes Genesect's subtype; alters Techno Blast."},
    "Douse Drive": {"emoji": PartialEmoji(name="douse_drive", id=1519187522914095245), "price": 15000, "gen": 5, "desc": "Changes Genesect's subtype; alters Techno Blast."},
    "Shock Drive": {"emoji": PartialEmoji(name="shock_drive", id=1519187532967841824), "price": 15000, "gen": 5, "desc": "Changes Genesect's subtype; alters Techno Blast."},
}

# ---------------------------------------------------------------------------
# Mega Stones (per-species, Gen 6 only)
# ---------------------------------------------------------------------------
MEGA_STONES: ItemTable = {
    "Venusaurite":    {"emoji": PartialEmoji(name="venusaurite",    id=1519187775478304820), "price": 15000, "gen": 6, "species_id": 3},
    "Charizardite X": {"emoji": PartialEmoji(name="charizardite_x", id=1519187785582641174), "price": 15000, "gen": 6, "species_id": 6,   "form_suffix": "mega-x"},
    "Charizardite Y": {"emoji": PartialEmoji(name="charizardite_y", id=1519187796428849193), "price": 15000, "gen": 6, "species_id": 6,   "form_suffix": "mega-y"},
    "Blastoisinite":  {"emoji": PartialEmoji(name="blastoisinite",  id=1519187808139477022), "price": 15000, "gen": 6, "species_id": 9},
    "Beedrillite":    {"emoji": PartialEmoji(name="beedrillite",    id=1519187818470051892), "price": 5000,  "gen": 6, "species_id": 15},
    "Pidgeotite":     {"emoji": PartialEmoji(name="pidgeotite",     id=1519187828570062910), "price": 5000,  "gen": 6, "species_id": 18},
    "Alakazite":      {"emoji": PartialEmoji(name="alakazite",      id=1519187838468362400), "price": 5000,  "gen": 6, "species_id": 65},
    "Slowbronite":    {"emoji": PartialEmoji(name="slowbronite",    id=1519187850447425606), "price": 5000,  "gen": 6, "species_id": 80},
    "Gengarite":      {"emoji": PartialEmoji(name="gengarite",      id=1519187862787194880), "price": 15000, "gen": 6, "species_id": 94},
    "Kangaskhanite":  {"emoji": PartialEmoji(name="kangaskhanite",  id=1519187873444921536), "price": 25000, "gen": 6, "species_id": 115},
    "Pinsirite":      {"emoji": PartialEmoji(name="pinsirite",      id=1519187882915659886), "price": 5000,  "gen": 6, "species_id": 127},
    "Gyaradosite":    {"emoji": PartialEmoji(name="gyaradosite",    id=1519187892369363007), "price": 15000, "gen": 6, "species_id": 130},
    "Aerodactylite":  {"emoji": PartialEmoji(name="aerodactylite",  id=1519187903966744627), "price": 5000,  "gen": 6, "species_id": 142},
    "Mewtwonite X":   {"emoji": PartialEmoji(name="mewtwonite_x",   id=1519187915442229379), "price": 15000, "gen": 6, "species_id": 150, "form_suffix": "mega-x"},
    "Mewtwonite Y":   {"emoji": PartialEmoji(name="mewtwonite_y",   id=1519187925277868102), "price": 25000, "gen": 6, "species_id": 150, "form_suffix": "mega-y"},
    "Ampharosite":    {"emoji": PartialEmoji(name="ampharosite",    id=1519187935394795650), "price": 5000,  "gen": 6, "species_id": 181},
    "Steelixite":     {"emoji": PartialEmoji(name="steelixite",     id=1519187944970260512), "price": 5000,  "gen": 6, "species_id": 208},
    "Scizorite":      {"emoji": PartialEmoji(name="scizorite",      id=1519187955263213699), "price": 15000, "gen": 6, "species_id": 212},
    "Heracronite":    {"emoji": PartialEmoji(name="heracronite",    id=1519187965371482142), "price": 5000,  "gen": 6, "species_id": 214},
    "Houndoominite":  {"emoji": PartialEmoji(name="houndoominite",  id=1519187976809222397), "price": 5000,  "gen": 6, "species_id": 229},
    "Tyranitarite":   {"emoji": PartialEmoji(name="tyranitarite",   id=1519187986829279272), "price": 15000, "gen": 6, "species_id": 248},
    "Sceptilite":     {"emoji": PartialEmoji(name="sceptilite",     id=1519187997998710795), "price": 15000, "gen": 6, "species_id": 254},
    "Blazikenite":    {"emoji": PartialEmoji(name="blazikenite",    id=1519188007855460352), "price": 15000, "gen": 6, "species_id": 257},
    "Swampertite":    {"emoji": PartialEmoji(name="swampertite",    id=1519188017724653578), "price": 15000, "gen": 6, "species_id": 260},
    "Gardevoirite":   {"emoji": PartialEmoji(name="gardevoirite",   id=1519188028197699624), "price": 15000, "gen": 6, "species_id": 282},
    "Sablenite":      {"emoji": PartialEmoji(name="sablenite",      id=1519188038998036590), "price": 5000,  "gen": 6, "species_id": 302},
    "Mawilite":       {"emoji": PartialEmoji(name="mawilite",       id=1519188050419126373), "price": 15000, "gen": 6, "species_id": 303},
    "Aggronite":      {"emoji": PartialEmoji(name="aggronite",      id=1519188062922604676), "price": 5000,  "gen": 6, "species_id": 306},
    "Medichamite":    {"emoji": PartialEmoji(name="medichamite",    id=1519188073525543034), "price": 5000,  "gen": 6, "species_id": 308},
    "Manectite":      {"emoji": PartialEmoji(name="manectite",      id=1519188083663175780), "price": 5000,  "gen": 6, "species_id": 310},
    "Sharpedonite":   {"emoji": PartialEmoji(name="sharpedonite",   id=1519188094274768917), "price": 5000,  "gen": 6, "species_id": 319},
    "Cameruptite":    {"emoji": PartialEmoji(name="cameruptite",    id=1519188104437563483), "price": 5000,  "gen": 6, "species_id": 323},
    "Altarianite":    {"emoji": PartialEmoji(name="altarianite",    id=1519188115028443148), "price": 15000, "gen": 6, "species_id": 334},
    "Banettite":      {"emoji": PartialEmoji(name="banettite",      id=1519188124750577754), "price": 5000,  "gen": 6, "species_id": 354},
    "Absolite":       {"emoji": PartialEmoji(name="absolite",       id=1519188133843959969), "price": 5000,  "gen": 6, "species_id": 359},
    "Glalitite":      {"emoji": PartialEmoji(name="glalitite",      id=1519188144745087169), "price": 5000,  "gen": 6, "species_id": 362},
    "Salamencite":    {"emoji": PartialEmoji(name="salamencite",    id=1519188154861486250), "price": 15000, "gen": 6, "species_id": 373},
    "Metagrossite":   {"emoji": PartialEmoji(name="metagrossite",   id=1519188167289471151), "price": 15000, "gen": 6, "species_id": 376},
    "Latiasite":      {"emoji": PartialEmoji(name="latiasite",      id=1519188178580279426), "price": 25000, "gen": 6, "species_id": 380},
    "Latiosite":      {"emoji": PartialEmoji(name="latiosite",      id=1519188189519024300), "price": 25000, "gen": 6, "species_id": 381},
    "Lopunnite":      {"emoji": PartialEmoji(name="lopunnite",      id=1519188201531773008), "price": 5000,  "gen": 6, "species_id": 428},
    "Garchompite":    {"emoji": PartialEmoji(name="garchompite",    id=1519188212923236543), "price": 5000,  "gen": 6, "species_id": 445},
    "Lucarionite":    {"emoji": PartialEmoji(name="lucarionite",    id=1519188224751439872), "price": 15000, "gen": 6, "species_id": 448},
    "Abomasite":      {"emoji": PartialEmoji(name="abomasite",      id=1519188237136953487), "price": 15000, "gen": 6, "species_id": 460},
    "Galladite":      {"emoji": PartialEmoji(name="galladite",      id=1519188248964894730), "price": 5000,  "gen": 6, "species_id": 475},
    "Audinite":       {"emoji": PartialEmoji(name="audinite",       id=1519188259417096222), "price": 5000,  "gen": 6, "species_id": 531},
    "Diancite":       {"emoji": PartialEmoji(name="diancite",       id=1519188271379517511), "price": 25000, "gen": 6, "species_id": 719},
}
_species_mega_stones: dict[int, list[str]] = {}
for _name, _d in MEGA_STONES.items():
    _species_mega_stones.setdefault(_d["species_id"], []).append(_name)
SPECIES_MEGA_STONES: dict[int, list[str]] = _species_mega_stones

# ---------------------------------------------------------------------------
# Training items
# ---------------------------------------------------------------------------
EV_VITAMINS: ItemTable = {
    "HP Up":   {"emoji": PartialEmoji(name="hp_up",   id=1519188275439337562), "stat": "hp",  "price": 2000, "gen": 1, "desc": "+10 EVs in HP (max 100/stat)."},
    "Protein": {"emoji": PartialEmoji(name="protein", id=1519188280015589557), "stat": "atk", "price": 2000, "gen": 1, "desc": "+10 EVs in Attack (max 100/stat)."},
    "Iron":    {"emoji": PartialEmoji(name="iron",    id=1519188283794657321), "stat": "def", "price": 2000, "gen": 1, "desc": "+10 EVs in Defense (max 100/stat)."},
    "Calcium": {"emoji": PartialEmoji(name="calcium", id=1519188288190021642), "stat": "spa", "price": 2000, "gen": 1, "desc": "+10 EVs in Sp. Atk (max 100/stat)."},
    "Zinc":    {"emoji": PartialEmoji(name="zinc",    id=1519188292418011268), "stat": "spd", "price": 2000, "gen": 1, "desc": "+10 EVs in Sp. Def (max 100/stat)."},
    "Carbos":  {"emoji": PartialEmoji(name="carbos",  id=1519188296880754759), "stat": "spe", "price": 2000, "gen": 1, "desc": "+10 EVs in Speed (max 100/stat)."},
}
EV_BERRIES: ItemTable = {
    "Pomeg Berry":  {"emoji": PartialEmoji(name="pomeg_berry",  id=1519188304501801136), "stat": "hp",  "price": 500, "gen": 3, "desc": "-10 EVs in HP."},
    "Kelpsy Berry": {"emoji": PartialEmoji(name="kelpsy_berry", id=1519188312550543413), "stat": "atk", "price": 500, "gen": 3, "desc": "-10 EVs in Attack."},
    "Qualot Berry": {"emoji": PartialEmoji(name="qualot_berry", id=1519188319999623188), "stat": "def", "price": 500, "gen": 3, "desc": "-10 EVs in Defense."},
    "Hondew Berry": {"emoji": PartialEmoji(name="hondew_berry", id=1519188326895063070), "stat": "spa", "price": 500, "gen": 3, "desc": "-10 EVs in Sp. Atk."},
    "Grepa Berry":  {"emoji": PartialEmoji(name="grepa_berry",  id=1519188333643956398), "stat": "spd", "price": 500, "gen": 3, "desc": "-10 EVs in Sp. Def."},
    "Tamato Berry": {"emoji": PartialEmoji(name="tamato_berry", id=1519188342519107715), "stat": "spe", "price": 500, "gen": 3, "desc": "-10 EVs in Speed."},
}
CUSTOM_TRAINING_ITEMS: ItemTable = {
    "IV Booster":       {"emoji": "🧬",                                                         "price": 80000,  "gen": 6, "desc": "Grants a pool of 10 IV points you can distribute across stats you choose."},
    "EV Reorganizer":   {"emoji": "🔄",                                                         "price": 100000, "gen": 6, "desc": "Allows you to redistribute your Pokémon's EVs how you wish."},
    "PP Up":            {"emoji": PartialEmoji(name="pp_up",           id=1522722481134243972), "price": 4000,   "gen": 2, "desc": "Raises one move's max PP by 1/5 of its base PP (max 3 uses per move)."},
    "PP Max":           {"emoji": PartialEmoji(name="pp_max",          id=1522722485232075022), "price": 10000,  "gen": 3, "desc": "Instantly maxes out a single move's PP, as if it had 3 PP Ups."},
    "Ability Capsule":  {"emoji": PartialEmoji(name="ability_capsule", id=1535869544915148840), "price": 5000,   "gen": 6, "desc": "Swaps a Pokémon between its two standard Abilities."},
}

SHOP_CATEGORIES: list[tuple[str, ItemTable]] = [
    ("Poké Balls", POKEBALL_DATA),
    ("Battle Items", BATTLE_ITEMS),
    ("Held Items", HELD_ITEMS),
    ("Evolution Items", {**EVOLUTION_ITEMS, **TRADE_EVOLUTION_ITEMS}),
    ("Training Items", {**EV_VITAMINS, **EV_BERRIES, **CUSTOM_TRAINING_ITEMS}),
    ("Mega Stones", MEGA_STONES),
    ("Miscellaneous", {**FORM_ITEMS, **ARCEUS_PLATES, **GENESECT_DRIVES}),
]

ALL_CATALOG_TABLES: list[ItemTable] = [
    POKEBALL_DATA, BATTLE_ITEMS, HELD_ITEMS, EVOLUTION_ITEMS, TRADE_EVOLUTION_ITEMS,
    FORM_ITEMS, ARCEUS_PLATES, GENESECT_DRIVES, MEGA_STONES, EV_VITAMINS, EV_BERRIES,
    CUSTOM_TRAINING_ITEMS,
]


@dataclass(frozen=True, slots=True)
class ItemInfo:
    name: str
    table: str
    price: int | None
    emoji: object
    description: str
    gen_introduced: int
    fling_power: int | None = None
    fling_effect: str | None = None


def find_item(name: str) -> ItemInfo | None:
    for table_name, table in (
        ("balls", POKEBALL_DATA), ("battle", BATTLE_ITEMS), ("held", HELD_ITEMS),
        ("evolution", EVOLUTION_ITEMS), ("trade_evolution", TRADE_EVOLUTION_ITEMS),
        ("form", FORM_ITEMS), ("plate", ARCEUS_PLATES), ("drive", GENESECT_DRIVES),
        ("mega_stone", MEGA_STONES), ("vitamin", EV_VITAMINS), ("berry", EV_BERRIES),
        ("training", CUSTOM_TRAINING_ITEMS),
    ):
        entry = table.get(name)
        if entry is not None:
            fling = _fling_data.get(name, {})
            return ItemInfo(
                name=name, table=table_name, price=entry.get("price"),
                emoji=entry.get("emoji", "🎁"), description=entry.get("desc", ""),
                gen_introduced=entry.get("gen", 1),
                fling_power=fling.get("fling_power"), fling_effect=fling.get("fling_effect"),
            )
    return None


def available_in(table: ItemTable, ruleset: GenerationRuleset) -> dict[str, dict]:
    """Items from a catalog table legal for the given ruleset's generation."""
    return {name: entry for name, entry in table.items() if entry.get("gen", 1) <= ruleset.number}


# ---------------------------------------------------------------------------
# Mechanical metadata (fling power/effect) sourced from `pokedex_builder.py`.
# Loaded lazily so importing this module never requires the data file to
# exist yet (handy before the first builder run).
# ---------------------------------------------------------------------------
import json as _json
from pathlib import Path as _Path

_FLING_DATA_PATH = _Path(__file__).resolve().parents[2] / "data" / "items_meta.json"
_fling_data: dict[str, dict] = {}


def _load_fling_data() -> None:
    global _fling_data
    if _FLING_DATA_PATH.exists():
        with _FLING_DATA_PATH.open(encoding="utf-8") as fh:
            _fling_data = _json.load(fh)


_load_fling_data()


def get_fling_data(item_name: str | None) -> dict:
    if not item_name:
        return {"fling_power": None, "fling_effect": None}
    return _fling_data.get(item_name, {"fling_power": None, "fling_effect": None})
