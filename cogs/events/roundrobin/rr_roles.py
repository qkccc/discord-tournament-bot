# cogs/roundrobin/rr_roles.py
import discord
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cog import RoundRobinCog

log = logging.getLogger(__name__)

async def get_or_create_role(cog: "RoundRobinCog", guild: discord.Guild, role_name: str, reason: str) -> discord.Role | None:
    """指定された名前のロールを取得または作成する"""
    existing_role = discord.utils.get(guild.roles, name=role_name)
    if existing_role:
        return existing_role
    
    try:
        new_role = await guild.create_role(name=role_name, reason=reason)
        log.info(f"'{role_name}' ロールをサーバー '{guild.name}' に作成しました。")
        return new_role
    except discord.Forbidden:
        log.error(f"サーバー '{guild.name}' でロールを作成する権限がありません。")
        return None
    except Exception as e:
        log.error(f"'{role_name}' ロールの作成中にエラー: {e}", exc_info=True)
        return None

async def assign_roles_on_join(cog: "RoundRobinCog", member: discord.Member, team_id: int):
    """メンバーがチームに参加した際に、参加者ロールとチームロールを付与する"""
    if not isinstance(member, discord.Member):
        return

    guild = member.guild
    
    # 1. 参加者ロールを取得/作成し、メンバーに付与
    participant_role = await get_or_create_role(cog, guild, "参加者", "総当たり戦の全体参加者ロール")
    if participant_role:
        cog.db.execute("INSERT OR IGNORE INTO rr_config (guild_id) VALUES (?)", (guild.id,))
        cog.db.execute("UPDATE rr_config SET role_id = ? WHERE guild_id = ?", (participant_role.id, guild.id))
        try:
            await member.add_roles(participant_role, reason="総当たり戦参加のため")
        except discord.Forbidden:
            log.warning(f"{member.display_name} に '参加者' ロールを付与できませんでした。")

    # 2. チームロールを取得/作成し、メンバーに付与
    team_data = cog.db.fetchone("SELECT name, role_id FROM rr_teams WHERE team_id = ?", (team_id,))
    if not team_data: return

    team_role = guild.get_role(team_data['role_id']) if team_data['role_id'] else None
    if not team_role:
        team_role = await get_or_create_role(cog, guild, team_data['name'], f"チーム '{team_data['name']}' の専用ロール")
        if team_role:
            cog.db.execute("UPDATE rr_teams SET role_id = ? WHERE team_id = ?", (team_role.id, team_id))

    if team_role:
        try:
            await member.add_roles(team_role, reason=f"チーム '{team_data['name']}' に参加したため")
        except discord.Forbidden:
            log.warning(f"{member.display_name} に '{team_data['name']}' ロールを付与できませんでした。")

async def remove_roles_on_leave(cog: "RoundRobinCog", member: discord.Member, team_id: int):
    """メンバーがチームから脱退した際に、チームロールを剥奪する"""
    if not isinstance(member, discord.Member):
        return
    
    team_data = cog.db.fetchone("SELECT role_id FROM rr_teams WHERE team_id = ?", (team_id,))
    if team_data and team_data['role_id']:
        role = member.guild.get_role(team_data['role_id'])
        if role:
            try:
                await member.remove_roles(role, reason="チームから脱退したため")
            except discord.Forbidden:
                log.warning(f"{member.display_name} からチームロールを剥奪できませんでした。")
