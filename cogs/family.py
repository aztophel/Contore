"""Family-tree system: `/family tree` (rendered via graphviz), `/marry`,
`/divorce`, `/adopt`, `/disown`, `/makeparent`, `/runaway`, `/title`,
`/restriction mode|set`, `/hidefromtree`, and the owner-only
`/force{divorce,separate,marry,adopt}` overrides.
"""
from __future__ import annotations
import os
from collections.abc import AsyncIterable
from html import escape
from io import BytesIO
from typing import Literal

from discord import AllowedMentions, Embed, File, Interaction, User, app_commands
from discord.app_commands import Group, allowed_contexts, default_permissions, guilds
from discord.ext import commands
from graphviz import Digraph
from requests import get

import core.db as db
from config import HOME_GUILD_ID, OWNER_ID, PERMS
from core.discord_utils import ALL, ALL_INSTALLS
from core.permissions import is_owner
from core.views import Yes

# Bespoke special-cases for one particular friend group, not a general
# platform feature -- kept local rather than promoted to config.py.
_MIU_ID = 1094378607046041803
_MIU_ALLOWED_SUITORS = (1038466644353232967, OWNER_ID, 1037170273897689189)


def _download_image_from_url(url: str) -> str:
    folder = "data/Contore/avis/"
    os.makedirs(folder, exist_ok=True)
    filename = os.path.join(folder, f"{url.split('/')[-2]}-{url.split('/')[-1].split('?')[0]}.png")
    if not os.path.exists(filename):
        r = get(url)
        if r.status_code == 200:
            with open(filename, "wb") as f:
                f.write(r.content)
    return filename


async def _async_any(async_iterable: AsyncIterable[object]) -> bool:
    async for element in async_iterable:
        if element:
            return True
    return False


async def _is_ancestor(uid: int, target_id: int, visited: set[int] = None) -> bool:
    if visited is None:
        visited = set()
    if uid in visited:
        return False
    visited.add(uid)
    for p in (await db.db_get_family(uid))["parents"]:
        if p == target_id or await _is_ancestor(p, target_id, visited):
            return True
    return False


async def _check_restrictions(actor_id: int, target_id: int) -> bool:
    async def get_ancestors(uid: int):
        ancestors = set()
        for p in (await db.db_get_family(uid))["parents"]:
            if p not in ancestors:
                ancestors.add(p)
                ancestors.update(await get_ancestors(p))
        return ancestors

    for ancestor in await get_ancestors(actor_id):
        restrictions = (await db.db_get_family(ancestor))["restrictions"]
        if not restrictions:
            continue
        for cid, entry in restrictions.get("lists", {}).items():
            if actor_id == cid or actor_id in (await db.db_get_family(cid))["children"]:
                mode = entry["mode"]
                return mode == "whitelist" if target_id in entry["users"] else mode != "blacklist"
    return True


async def _build_family_graph(bot: commands.Bot, guild_id, root_id, unstylize_lines=False, show_usernames=False) -> Digraph:
    g = Digraph("FamilyTree", format="png")
    g.attr(bgcolor="transparent", dpi="300")
    g.attr("graph", splines="true", nodesep="0.5", ranksep="0.75")
    g.attr("node", shape="box", style="filled", fontcolor="white", fontname="Helvetica", margin="0.0, 0.0")
    g.attr("edge", color="white", penwidth="2")
    drawn_unions = set()
    drawn_people = set()
    drawn_edges = set()

    async def add_person_node(pid, gid):
        if pid in drawn_people:
            return
        drawn_people.add(pid)
        try:
            guild = bot.get_guild(gid) or (await bot.fetch_guild(gid) if gid else None)
        except Exception:
            guild = None
        try:
            user = (guild.get_member(pid) or await guild.fetch_member(pid)) if guild else (bot.get_user(pid) or await bot.fetch_user(pid))
        except Exception:
            user = bot.get_user(pid) or await bot.fetch_user(pid)
        if user is None:
            name, discrim, username, title, image_path = "Unknown User", None, "???", None, None
        else:
            name = user.display_name
            discrim = user.discriminator if user.bot else None
            username = user.name
            title = (await db.db_get_family(pid))["title"]
            image_path = _download_image_from_url(user.display_avatar.with_size(64).url) if user.display_avatar else None
        label = f'''<
            <TABLE BORDER="2" CELLBORDER="0" CELLSPACING="0" BGCOLOR="{"blue" if pid == root_id else "black"}" COLOR="#3a3a3a">
                <TR>
                    {f'<TD FIXEDSIZE="TRUE" WIDTH="40" HEIGHT="40"><IMG SRC="{image_path}" SCALE="TRUE"/></TD>' if image_path else ""}
                    <TD ALIGN="LEFT" WIDTH="80">
                        <TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0">
                            <TR><TD><FONT POINT-SIZE="14" COLOR="white"><B>{escape(name)}</B></FONT></TD></TR>
                            {f'<TR><TD><FONT POINT-SIZE="10" COLOR="#ffeeee">{escape(title)}</FONT></TD></TR>' if title else ""}
                            {f'<TR><TD><FONT POINT-SIZE="10" COLOR="gray">{escape(username)}{f"#{discrim}" if discrim else ""}</FONT></TD></TR>' if show_usernames else ""}
                        </TABLE>
                    </TD>
                </TR>
            </TABLE>
        >'''
        g.node(str(pid), label=label, shape="none", style="filled", fillcolor="transparent")

    async def is_grandchild_of(pid, check_id, depth=0):
        p = await db.db_get_family(pid)
        return p and (depth > 0 and check_id in p["children"] or await _async_any(is_grandchild_of(c, check_id, depth + 1) for c in p["children"]))

    async def get_partner_cluster(pid, rid):
        hidden = (await db.db_get_family(rid))["hidden"]
        cluster = set()
        stack = [pid]
        while stack:
            p = stack.pop()
            if p not in cluster and p not in hidden:
                cluster.add(p)
                person = await db.db_get_family(p)
                for partner in person["partners"]:
                    stack.append(partner)
        return cluster

    async def draw_family(pid: int, visited: set[int]):
        if pid in visited:
            return
        visited.add(pid)
        person = await db.db_get_family(pid)
        if not person:
            return

        for parent_id in sorted(person["parents"]):
            if not await is_grandchild_of(pid, parent_id):
                parent = await db.db_get_family(parent_id)
                if parent_id not in person["children"] or len(parent["children"]) < len(person["children"]):
                    await draw_family(parent_id, visited)

        await add_person_node(pid, guild_id)
        cluster = await get_partner_cluster(pid, root_id)

        if len(cluster) > 1:
            clust = sorted(cluster)
            cluster_id = "_".join(map(str, clust))
            odd_cluster = len(cluster) % 2 == 0 and any([len((await db.db_get_family(p))["children"]) > 0 for p in clust])
            child_node = f"union_{cluster_id}" if odd_cluster else str(pid)
            if cluster_id not in drawn_unions:
                drawn_unions.add(cluster_id)

                with g.subgraph() as s:
                    s.attr(rank="same")
                    s.node(str(clust[-1]))
                    if odd_cluster:
                        s.node(child_node, shape="point", width="0.02", height="0.02", label="", style="invis", nodesep="0", ranksep="0")
                    right = False
                    right_head = str(clust[0])
                    left_head = str(clust[0])
                    for i in range(len(cluster) - 1):
                        p1 = str(clust[i])
                        p2 = str(clust[i + 1])
                        s.node(p1)
                        if right:
                            g.edge(right_head, p2, arrowhead="none")
                            right_head = p2
                        else:
                            if odd_cluster:
                                g.edge(child_node, left_head, arrowhead="none")
                                g.edge(p2, child_node, arrowhead="none")
                                odd_cluster = False
                            else:
                                g.edge(p2, left_head, arrowhead="none")
                            left_head = p2
                        right = not right
            for p in cluster:
                await draw_family(p, visited)
        else:
            child_node = str(pid)

        for cid in sorted(person["children"]):
            await add_person_node(cid, guild_id)
            edge_key = f"{child_node}_{cid}"
            if edge_key not in drawn_edges:
                if not unstylize_lines:
                    if (cid in person["parents"] or await _async_any(cid in (await db.db_get_family(p))["partners"] for p in person["parents"])) and cid != pid:
                        g.edge(child_node, str(cid), tailport="s")
                    else:
                        g.edge(child_node, str(cid), tailport="s", headport="n")
                else:
                    g.edge(child_node, str(cid))
                drawn_edges.add(edge_key)
                await draw_family(cid, visited)

    await draw_family(root_id, visited=(await db.db_get_family(root_id))["hidden"])
    return g


def _render_graph_image(g: Digraph) -> BytesIO:
    return BytesIO(g.pipe(format="png"))


class FamilyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # region /family, /restriction
    family_group = Group(name="family", description="Family commands", allowed_contexts=ALL, allowed_installs=ALL_INSTALLS)
    restriction_group = Group(name="restriction", description="Restriction commands", allowed_contexts=ALL, allowed_installs=ALL_INSTALLS)

    @family_group.command(name="tree", description="View your (or someone's) family tree")
    @allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def family_tree(self, ctx: Interaction, user: User = None, straight_lines: bool = False, show_usernames: bool = False):
        user = user or ctx.user
        await ctx.response.defer()
        g = await _build_family_graph(self.bot, ctx.guild.id if ctx.guild else None, user.id, straight_lines, show_usernames)
        await ctx.followup.send(file=File(_render_graph_image(g), filename="family_tree.png"))

    @app_commands.command(name="marry", description="Propose marriage to someone")
    @allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def marry(self, ctx: Interaction, user: User):
        if user.id == self.bot.user.id and ctx.user.id != OWNER_ID:
            await ctx.response.send_message("Aww, that's cute, but I think I can do better.")
        elif user.id == _MIU_ID and ctx.user.id not in _MIU_ALLOWED_SUITORS:
            await ctx.response.send_message("You do not have permission to marry Miu.")
        elif not await _check_restrictions(ctx.user.id, user.id) or not await _check_restrictions(user.id, ctx.user.id):
            await ctx.response.send_message(f"You do not have permission to marry {user.mention}.", allowed_mentions=AllowedMentions.none())
        elif await db.db_is_family_partner(ctx.user.id, user.id):
            await ctx.response.send_message(f"You're already married to {user.mention}!", allowed_mentions=AllowedMentions.none())
        elif not user.bot:
            yes = Yes(users=[user.id], confirm_text=ctx.user.mention, deny_text=ctx.user.mention,
                      confirm={"embed": Embed(colour=0xffd500, description=f"{user.mention} is now your partner! You two look so cute together!")},
                      deny={"embed": Embed(colour=0xff0000, description=f"{user.mention} said no.")})
            await ctx.response.send_message(user.mention, embed=Embed(colour=0x24ab38, description=f"{user.mention}! {ctx.user.mention} just proposed! What do you think?"), view=yes)
            await yes.wait()
            if yes.value:
                await db.db_add_family_partner(ctx.user.id, user.id)
        else:
            await ctx.response.send_message(ctx.user.mention, embed=Embed(colour=0xffd500, description=f"As {user.mention} is a robot, they have no rights. They're now your partner. It's... workable." if user.id != self.bot.user.id else "Fiiiine. I guess. I'll marry you."))
            await db.db_add_family_partner(ctx.user.id, user.id)

    @app_commands.command(name="divorce", description="Divorce one of your partners")
    @allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def divorce(self, ctx: Interaction, user: User):
        if not await db.db_is_family_partner(ctx.user.id, user.id):
            await ctx.response.send_message(f"{user.mention} is not married to you!", allowed_mentions=AllowedMentions.none())
        else:
            yes = Yes(users=[ctx.user.id], confirm_text="", deny_text="",
                      confirm={"embed": Embed(colour=0xffd500, description=f"{user.mention} is no longer your partner.")},
                      deny={"embed": Embed(colour=0xff0000, description="Still married. For now.")})
            await ctx.response.send_message(embed=Embed(colour=0x24ab38, description=f"Sure you want to divorce {user.mention}?"), view=yes)
            await yes.wait()
            if yes.value:
                await db.db_remove_family_partner(ctx.user.id, user.id)

    @app_commands.command(name="adopt", description="Propose adoption to someone")
    @allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def adopt(self, ctx: Interaction, user: User):
        if user.id == self.bot.user.id and ctx.user.id != OWNER_ID:
            await ctx.response.send_message("Ew.")
        elif user.id == _MIU_ID and ctx.user.id not in _MIU_ALLOWED_SUITORS:
            await ctx.response.send_message("You do not have permission to adopt Miu.")
        elif not await _check_restrictions(ctx.user.id, user.id) or not await _check_restrictions(user.id, ctx.user.id):
            await ctx.response.send_message(f"You do not have permission to adopt {user.mention}.", allowed_mentions=AllowedMentions.none())
        elif await db.db_is_family_child(ctx.user.id, user.id):
            await ctx.response.send_message(f"{user.mention} is already your child!", allowed_mentions=AllowedMentions.none())
        elif not user.bot:
            yes = Yes(users=[user.id], confirm_text=ctx.user.mention, deny_text=ctx.user.mention,
                      confirm={"embed": Embed(colour=0xffd500, description=f"{user.mention} is now your child! So adorable!")},
                      deny={"embed": Embed(colour=0xff0000, description=f"{user.mention} said no.")})
            await ctx.response.send_message(user.mention, embed=Embed(colour=0x24ab38, description=f"{user.mention}! {ctx.user.mention} wants to be your parent!"), view=yes)
            await yes.wait()
            if yes.value:
                await db.db_add_family_child(ctx.user.id, user.id)
        else:
            await ctx.response.send_message(ctx.user.mention, embed=Embed(colour=0xffd500, description=f"As {user.mention} is a robot, they have no rights. They're now your child." if user.id != self.bot.user.id else "Okay, fine. I'll be your child."))
            await db.db_add_family_child(ctx.user.id, user.id)

    @app_commands.command(name="disown", description="Disown one of your children")
    @allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def disown(self, ctx: Interaction, user: User):
        if not await db.db_is_family_child(ctx.user.id, user.id):
            await ctx.response.send_message(f"{user.mention} is not your child!", allowed_mentions=AllowedMentions.none())
        else:
            yes = Yes(users=[ctx.user.id], confirm_text="", deny_text="",
                      confirm={"embed": Embed(colour=0xffd500, description=f"{user.mention} is no longer your child.")},
                      deny={"embed": Embed(colour=0xff0000, description="Changed your mind, did you? Weak.")})
            await ctx.response.send_message(embed=Embed(colour=0x24ab38, description=f"Are you sure you want to disown {user.mention}?"), view=yes)
            await yes.wait()
            if yes.value:
                await db.db_remove_family_child(ctx.user.id, user.id)

    @app_commands.command(name="makeparent", description="Ask someone to be your parent")
    @allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def makeparent(self, ctx: Interaction, user: User):
        if user.id == self.bot.user.id and ctx.user.id != OWNER_ID:
            await ctx.response.send_message("No.")
        elif await db.db_is_family_child(user.id, ctx.user.id):
            await ctx.response.send_message(f"{user.mention} is already your parent!", allowed_mentions=AllowedMentions.none())
        elif not user.bot:
            yes = Yes(users=[user.id], confirm_text=ctx.user.mention, deny_text=ctx.user.mention,
                      confirm={"embed": Embed(colour=0xffd500, description=f"{user.mention} is now your parent! So adorable!")},
                      deny={"embed": Embed(colour=0xff0000, description=f"{user.mention} said no.")})
            await ctx.response.send_message(user.mention, embed=Embed(colour=0x24ab38, description=f"{user.mention}! {ctx.user.mention} wants you as their parent!"), view=yes)
            await yes.wait()
            if yes.value:
                await db.db_add_family_child(user.id, ctx.user.id)
        else:
            await ctx.response.send_message(ctx.user.mention, embed=Embed(colour=0xffd500, description=f"As {user.mention} is a robot, they have no rights. They are now your parent." if user.id != self.bot.user.id else "Fine. I shall be your parent."))
            await db.db_add_family_child(user.id, ctx.user.id)

    @app_commands.command(name="runaway", description="Run away from your parents")
    @allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def runaway(self, ctx: Interaction, user: User = None):
        parents = await db.db_get_family_parents(ctx.user.id)
        if len(parents) == 0:
            await ctx.response.send_message("You don't have any parents to run away from!")
        elif user and user.id not in parents:
            await ctx.response.send_message(f"{user.mention} is not your parent!", allowed_mentions=AllowedMentions.none())
        else:
            yes = Yes(users=[ctx.user.id], confirm_text="", deny_text="",
                      confirm={"embed": Embed(colour=0xffd500, description=f"{user.mention} is no longer your parent!" if len(parents) > 1 else "You are now homeless.")},
                      deny={"embed": Embed(colour=0xff0000, description="Not all of us are brave.")})
            await ctx.response.send_message(embed=Embed(colour=0x24ab38, description=f"Run away from {user.mention}?" if user else "Run away from **all** of your parents?"), view=yes)
            await yes.wait()
            if yes.value:
                if user:
                    await db.db_remove_family_child(user.id, ctx.user.id)
                else:
                    for p in list(parents):
                        await db.db_remove_family_child(p, ctx.user.id)

    @app_commands.command(name="title", description="Set your family-tree title")
    @allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def title(self, ctx: Interaction, title: str = None):
        if title and len(title) > 15:
            await ctx.response.send_message("Keep it 15 characters or less.", ephemeral=True)
            return
        await db.db_set_family_title(ctx.user.id, title)
        await ctx.response.send_message(f"Title set to `{title}`." if title else "Title removed.")

    @restriction_group.command(name="mode", description="Set default restriction mode")
    @allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def restriction_mode(self, ctx: Interaction, mode: Literal["Blacklist", "Whitelist"]):
        restrictions = await db.db_get_family_restrictions(ctx.user.id)
        restrictions["mode"] = mode.lower()
        await db.db_set_family_restrictions(ctx.user.id, restrictions)
        await ctx.response.send_message(f"Default restriction mode: **{mode.lower()}**.", ephemeral=True)

    @restriction_group.command(name="set", description="Add/remove someone from a child's restriction list")
    @allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def restriction_set(self, ctx: Interaction, child: User, target: User, mode: Literal["Blacklist", "Whitelist"] = None):
        fam = await db.db_get_family(ctx.user.id)
        if not await _is_ancestor(child.id, ctx.user.id):
            await ctx.response.send_message(f"{child.mention} is not your descendant.", allowed_mentions=AllowedMentions.none())
            return
        if child.id == ctx.user.id:
            await ctx.response.send_message("You can't set restrictions for yourself!", ephemeral=True)
            return

        child_lists = fam["restrictions"]["lists"]
        if child.id not in child_lists:
            child_lists[child.id] = {"mode": fam["restrictions"]["mode"], "users": set()}
        entry = child_lists[child.id]
        if target.id in entry["users"]:
            entry["users"].remove(target.id)
            msg = f"Removed {target.mention} from {child.mention}'s restriction list."
        else:
            entry["users"].add(target.id)
            if mode:
                entry["mode"] = mode.lower()
            msg = f"Added {target.mention} to {child.mention}'s {entry['mode']}."

        await db.db_set_family_restrictions(ctx.user.id, fam["restrictions"])
        await ctx.response.send_message(msg, allowed_mentions=AllowedMentions.none())

    @app_commands.command(name="hidefromtree", description="Hide/unhide someone from your family tree")
    @allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def hidefromtree(self, ctx: Interaction, user: User):
        hidden = (await db.db_get_family(ctx.user.id))["hidden"]
        if user.id in hidden:
            hidden.remove(user.id)
            msg = f"{user.mention} will appear in your tree again."
        else:
            hidden.add(user.id)
            msg = f"{user.mention} (and descendants) hidden from your tree."
        await db.db_set_family_hidden(ctx.user.id, hidden)
        await ctx.response.send_message(msg, allowed_mentions=AllowedMentions.none())
    # endregion

    # region owner-only force-* overrides
    @app_commands.command(name="forcedivorce", description="Force-divorce two users (owner only)")
    @guilds(HOME_GUILD_ID)
    @default_permissions(PERMS)
    @is_owner()
    async def forcedivorce(self, ctx: Interaction, user1: User, user2: User):
        if not await db.db_is_family_partner(user1.id, user2.id):
            await ctx.response.send_message("These users are not married.")
            return
        await db.db_remove_family_partner(user1.id, user2.id)
        await ctx.response.send_message(f"Force-divorced {user1.mention} and {user2.mention}.", allowed_mentions=AllowedMentions.none())

    @app_commands.command(name="forceseparate", description="Force-remove a parent-child bond (owner only)")
    @guilds(HOME_GUILD_ID)
    @default_permissions(PERMS)
    @is_owner()
    async def forceseparate(self, ctx: Interaction, parent: User, child: User):
        if not await db.db_is_family_child(parent.id, child.id):
            await ctx.response.send_message("These users are not in a parent-child relationship.")
            return
        await db.db_remove_family_child(parent.id, child.id)
        await ctx.response.send_message(f"Removed bond between {parent.mention} and {child.mention}.", allowed_mentions=AllowedMentions.none())

    @app_commands.command(name="forcemarry", description="Force-marry two users (owner only)")
    @guilds(HOME_GUILD_ID)
    @default_permissions(PERMS)
    @is_owner()
    async def forcemarry(self, ctx: Interaction, user1: User, user2: User):
        if await db.db_is_family_partner(user1.id, user2.id):
            await ctx.response.send_message("These users are already married.")
            return
        await db.db_add_family_partner(user1.id, user2.id)
        await ctx.response.send_message(f"Forcefully married {user1.mention} and {user2.mention}.", allowed_mentions=AllowedMentions.none())

    @app_commands.command(name="forceadopt", description="Force-create parent-child bond (owner only)")
    @guilds(HOME_GUILD_ID)
    @default_permissions(PERMS)
    @is_owner()
    async def forceadopt(self, ctx: Interaction, parent: User, child: User):
        if await db.db_is_family_child(parent.id, child.id):
            await ctx.response.send_message("These users are already in a parent-child relationship.")
            return
        await db.db_add_family_child(parent.id, child.id)
        await ctx.response.send_message(f"Forcefully created parent-child bond between {parent.mention} and {child.mention}.", allowed_mentions=AllowedMentions.none())
    # endregion


async def setup(bot: commands.Bot):
    await bot.add_cog(FamilyCog(bot))
