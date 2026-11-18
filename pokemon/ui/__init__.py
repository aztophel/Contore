"""Discord-facing battle UI: embeds, buttons, and PNG battle-scene
rendering. Unlike pokemon/battle/abilities and held_items, these modules
are imported explicitly by whatever cog wires up battle commands (there's
no auto-discovery contract here -- there's nothing to auto-discover, since
BattleView/battle_render aren't pluggable per-slug the way abilities are).
"""
