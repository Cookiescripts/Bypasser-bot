import discord
from discord import app_commands
import os
import re
import aiohttp
import asyncio
import json
from datetime import datetime, timezone
from urllib.parse import quote_plus

TOKEN = os.environ.get("TOKEN")
HISTORY_FILE = "history.json"
MAX_HISTORY = 20

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

BYPASS_APIS = [
    {"url": "https://bypass.vip/api?url={}", "keys": ["destination", "result", "bypassed", "url", "link"]},
    {"url": "https://api.bypass.vip/?url={}", "keys": ["destination", "result", "bypassed", "url", "link"]},
    {"url": "https://bypass.city/api?url={}", "keys": ["destination", "result", "bypassed", "url", "link"]},
    {"url": "https://api.letsbypass.com/?url={}", "keys": ["destination", "result", "bypassed", "url", "link"]},
]

def extract_redirect_from_html(html: str) -> str:
    patterns = [
        r'window\.location(?:\.href)?\s*=\s*["\']([^"\']+)["\']',
        r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+content=["\'][^"\']*url=([^"\'>\s]+)',
        r'location\.replace\(["\']([^"\']+)["\']\)',
        r'location\.href\s*=\s*["\']([^"\']+)["\']',
        r'<a[^>]+id=["\']skip["\'][^>]+href=["\']([^"\']+)["\']',
        r'<a[^>]+class=["\'][^"\']*skip[^"\']*["\'][^>]+href=["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            found = match.group(1)
            if found.startswith("http"):
                return found
    return None

async def bypass_link(url: str) -> str:
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        for api in BYPASS_APIS:
            try:
                api_url = api["url"].format(quote_plus(url))
                async with session.get(api_url, timeout=timeout) as resp:
                    if resp.status == 200:
                        try:
                            data = await resp.json(content_type=None)
                        except Exception:
                            continue
                        for key in api["keys"]:
                            val = data.get(key)
                            if val and isinstance(val, str) and val.startswith("http"):
                                return val
            except Exception:
                continue

        try:
            async with session.get(url, allow_redirects=True, timeout=timeout) as resp:
                final_url = str(resp.url)
                if final_url.rstrip("/") != url.rstrip("/"):
                    return final_url
                html = await resp.text()
                found = extract_redirect_from_html(html)
                if found:
                    return found
        except Exception:
            pass

        try:
            async with session.head(url, allow_redirects=True, timeout=timeout) as resp:
                final_url = str(resp.url)
                if final_url.rstrip("/") != url.rstrip("/"):
                    return final_url
        except Exception:
            pass

    return None

def load_history() -> dict:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_history(data: dict):
    with open(HISTORY_FILE, "w") as f:
        json.dump(data, f, indent=2)

def add_to_history(user_id: int, original: str, bypassed: str):
    data = load_history()
    key = str(user_id)
    if key not in data:
        data[key] = []
    entry = {
        "original": original,
        "bypassed": bypassed,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    }
    data[key].insert(0, entry)
    data[key] = data[key][:MAX_HISTORY]
    save_history(data)

@tree.command(name="bypass", description="Bypass a link and receive the direct URL via DM")
@app_commands.describe(link="The link you want to bypass")
async def bypass(interaction: discord.Interaction, link: str):
    await interaction.response.defer(ephemeral=True)
    result = await bypass_link(link)
    if result:
        add_to_history(interaction.user.id, link, result)
        try:
            dm_channel = await interaction.user.create_dm()
            embed = discord.Embed(title="Link Bypassed", color=discord.Color.green())
            embed.add_field(name="Original", value=link, inline=False)
            embed.add_field(name="Bypassed", value=result, inline=False)
            await dm_channel.send(embed=embed)
            await interaction.followup.send("Done! Check your DMs for the bypassed link.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(
                f"Couldn't DM you (your DMs may be closed). Here's the bypassed link:\n{result}",
                ephemeral=True
            )
    else:
        await interaction.followup.send(
            "Could not bypass that link. It may not be supported or may already be a direct link.",
            ephemeral=True
        )

@tree.command(name="bulkbypass", description="Bypass multiple links at once and receive all results via DM")
@app_commands.describe(links="Paste multiple links separated by spaces or new lines (max 10)")
async def bulkbypass(interaction: discord.Interaction, links: str):
    await interaction.response.defer(ephemeral=True)
    raw = [l.strip() for l in links.replace("\n", " ").split() if l.strip()]
    unique = list(dict.fromkeys(raw))[:10]
    if not unique:
        await interaction.followup.send("No valid links were found in your input.", ephemeral=True)
        return
    await interaction.followup.send(
        f"Processing {len(unique)} link(s)... You'll receive a DM when done.", ephemeral=True
    )
    tasks = [bypass_link(url) for url in unique]
    results = await asyncio.gather(*tasks)
    for original, bypassed in zip(unique, results):
        if bypassed:
            add_to_history(interaction.user.id, original, bypassed)
    embed = discord.Embed(title=f"Bulk Bypass Results ({len(unique)} links)", color=discord.Color.green())
    for i, (original, bypassed) in enumerate(zip(unique, results), start=1):
        if bypassed:
            embed.add_field(name=f"Link {i}", value=f"**Original:** {original}\n**Bypassed:** {bypassed}", inline=False)
        else:
            embed.add_field(name=f"Link {i} — Failed", value=f"**Original:** {original}\n**Result:** Could not bypass this link.", inline=False)
    embed.set_footer(text=f"Successfully bypassed {sum(1 for r in results if r)}/{len(unique)} links.")
    try:
        dm_channel = await interaction.user.create_dm()
        await dm_channel.send(embed=embed)
    except discord.Forbidden:
        chunks = []
        current = ""
        for field in embed.fields:
            line = f"{field.name}\n{field.value}\n\n"
            if len(current) + len(line) > 1900:
                chunks.append(current)
                current = line
            else:
                current += line
        if current:
            chunks.append(current)
        for chunk in chunks:
            await interaction.followup.send(f"```\n{chunk}\n```", ephemeral=True)

@tree.command(name="history", description="View your previously bypassed links")
async def history(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    data = load_history()
    entries = data.get(str(interaction.user.id), [])
    if not entries:
        await interaction.followup.send(
            "You haven't bypassed any links yet. Use `/bypass` or `/bulkbypass` to get started.",
            ephemeral=True
        )
        return
    embed = discord.Embed(title=f"Your Bypass History (last {len(entries)})", color=discord.Color.blurple())
    for i, entry in enumerate(entries[:10], start=1):
        embed.add_field(
            name=f"#{i} — {entry['timestamp']}",
            value=f"**Original:** {entry['original']}\n**Bypassed:** {entry['bypassed']}",
            inline=False
        )
    if len(entries) > 10:
        embed.set_footer(text=f"Showing 10 of {len(entries)} entries. Up to {MAX_HISTORY} are stored.")
    else:
        embed.set_footer(text=f"Up to {MAX_HISTORY} entries are stored.")
    try:
        dm_channel = await interaction.user.create_dm()
        await dm_channel.send(embed=embed)
        await interaction.followup.send("Check your DMs for your history.", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(embed=embed, ephemeral=True)

@tree.command(name="clearhistory", description="Wipe all of your saved bypass history")
async def clearhistory(interaction: discord.Interaction):
    data = load_history()
    key = str(interaction.user.id)
    if key not in data or not data[key]:
        await interaction.response.send_message("You have no history to clear.", ephemeral=True)
        return
    count = len(data[key])
    data[key] = []
    save_history(data)
    await interaction.response.send_message(
        f"Done! {count} entr{'ies' if count != 1 else 'y'} cleared from your history.",
        ephemeral=True
    )

INVITE_LINK = "https://discord.com/oauth2/authorize?client_id=1504624760997412874&scope=bot%20applications.commands&permissions=8"

@tree.command(name="help", description="View all available commands and how to use them")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(title="Bot Commands", description="Here are all the available commands:", color=discord.Color.blurple())
    embed.add_field(name="/bypass", value="Paste a link to bypass it. The bot will DM you the direct/bypassed URL.\n**Usage:** `/bypass link:<url>`", inline=False)
    embed.add_field(name="/bulkbypass", value="Paste up to 10 links separated by spaces. The bot will bypass them all and DM you every result.\n**Usage:** `/bulkbypass links:<url1> <url2> ...`", inline=False)
    embed.add_field(name="/history", value="View your last 20 successfully bypassed links with timestamps, sent to you via DM.", inline=False)
    embed.add_field(name="/clearhistory", value="Wipe all of your saved bypass history permanently.", inline=False)
    embed.add_field(name="/invite", value="Get the invite link to add this bot to your own server.", inline=False)
    embed.add_field(name="/help", value="Shows this help message with all commands and instructions.", inline=False)
    embed.set_footer(text="Make sure your DMs are open to receive bypassed links.")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="invite", description="Get the invite link to add this bot to your server")
async def invite_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Invite Me to Your Server",
        description=f"[Click here to invite the bot]({INVITE_LINK})",
        color=discord.Color.blurple()
    )
    embed.set_footer(text="Thank you for using this bot!")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@client.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {client.user} (ID: {client.user.id})")
    print("Slash commands synced.")

client.run(TOKEN)
