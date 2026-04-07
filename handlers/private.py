from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
import re
from datetime import datetime

from database import (
    add_user,
    get_net_balances,
    get_recent_transactions,
    add_offline_profile,
    get_offline_profile_by_name,
    log_transaction,
    DB_NAME
)
import aiosqlite

private_router = Router()
private_router.message.filter(F.chat.type == 'private')

@private_router.message(CommandStart())
async def cmd_start(message: Message):
    await add_user(message.from_user.id, message.from_user.username)
    welcome_text = (
        "👋 Welcome to EasyBill!\n\n"
        "To start using the bot with your friends, add me to your group chats!\n\n"
        "Here are the DM commands you can use:\n"
        "📊 /stats - View your net balances\n"
        "📜 /log - View your recent transaction history\n\n"
        "Offline User Management:\n"
        "👤 /offline_add [name] - Create an offline profile for a friend without Telegram\n"
        "💸 /offline_lent [amount] [name] [reason] - Log that an offline friend owes you\n"
        "✅ /offline_paid [amount] [name] - Log a repayment involving an offline friend"
    )
    await message.reply(welcome_text)


@private_router.message(Command("stats"))
async def cmd_stats(message: Message):
    await add_user(message.from_user.id, message.from_user.username)
    balances = await get_net_balances(message.from_user.id)
    
    if not balances:
        return await message.reply("🎉 You have no outstanding debts! Everything is settled.")
        
    response = "📊 <b>Your Balances:</b>\n"
    for bal in balances:
        net = bal['net']
        party = bal['party_name']
        if net > 0:
            response += f"{party} owes you: {net} tg\n"
        elif net < 0:
            response += f"You owe {party}: {abs(net)} tg\n"
            
    await message.reply(response)


async def _resolve_party_name(party_id: int) -> str:
    """Helper for the log command to natively trace identities directly via SQLite."""
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        
        async with db.execute("SELECT username FROM users WHERE telegram_id = ?", (party_id,)) as cursor:
            user_row = await cursor.fetchone()
            if user_row and user_row['username']:
                return f"@{user_row['username']}"
                
        async with db.execute("SELECT name FROM offline_profiles WHERE id = ?", (party_id,)) as cursor:
            off_row = await cursor.fetchone()
            if off_row:
                return off_row['name']
                
    return f"Unknown (ID: {party_id})"


@private_router.message(Command("log"))
async def cmd_log(message: Message):
    await add_user(message.from_user.id, message.from_user.username)
    txs = await get_recent_transactions(message.from_user.id, limit=10)
    
    confirmed_txs = [tx for tx in txs if tx['status'] == 'confirmed']
    
    if not confirmed_txs:
        return await message.reply("📜 No recent confirmed transactions found.")
        
    response = "📜 <b>Recent Transactions:</b>\n\n"
    for tx in confirmed_txs:
        try:
            dt = datetime.fromisoformat(tx['timestamp']).strftime('%Y-%m-%d')
        except ValueError:
            dt = 'Unknown Date'
            
        amount = tx['amount']
        reason = tx['reason'] or 'No reason'
        
        if tx['creditor_id'] == message.from_user.id:
            direction = "Lent to"
            other_id = tx['debtor_id']
        else:
            direction = "Borrowed from"
            other_id = tx['creditor_id']
            
        party_name = await _resolve_party_name(other_id)
        
        response += f"• <b>{dt}</b> | {direction} <b>{party_name}</b> | {amount} tg | <i>{reason}</i>\n"
        
    await message.reply(response)


@private_router.message(Command("offline_add"))
async def cmd_offline_add(message: Message):
    await add_user(message.from_user.id, message.from_user.username)
    parts = message.text.split(maxsplit=1)
    
    if len(parts) < 2:
        return await message.reply("❌ Usage: <code>/offline_add [name]</code>")
    
    name = parts[1].strip()
    existing = await get_offline_profile_by_name(message.from_user.id, name)
    if existing:
        return await message.reply(f"⚠️ Offline profile <b>{name}</b> already exists.")
        
    await add_offline_profile(message.from_user.id, name)
    await message.reply(f"✅ Offline profile <b>{name}</b> created!")


async def parse_offline_args(text: str, command: str):
    """Utility to parse amount and name specifically formatted for offline commands."""
    pattern = rf"(?i)^/{command}\s+(\d+)\s+([^\s]+)(?:\s+(.*))?"
    match = re.match(pattern, text)
    if match:
        amount = int(match.group(1))
        name = match.group(2)
        reason = match.group(3) or ""
        return amount, name, reason
    return None, None, None


@private_router.message(Command("offline_lent"))
async def cmd_offline_lent(message: Message):
    await add_user(message.from_user.id, message.from_user.username)
    amount, name, reason = await parse_offline_args(message.text, "offline_lent")
    
    if not amount:
        return await message.reply("❌ Usage: <code>/offline_lent [amount] [name] [reason]</code>")
        
    prof = await get_offline_profile_by_name(message.from_user.id, name)
    if not prof:
        return await message.reply(f"❌ Offline profile '{name}' not found. Create it first using /offline_add")
        
    await log_transaction(message.from_user.id, prof['id'], amount, reason, "confirmed")
    await message.reply(f"✅ Logged! Offline user <b>{name}</b> owes you {amount} tg.")


@private_router.message(Command("offline_paid"))
async def cmd_offline_paid(message: Message):
    await add_user(message.from_user.id, message.from_user.username)
    amount, name, _ = await parse_offline_args(message.text, "offline_paid")
    
    if not amount:
        return await message.reply("❌ Usage: <code>/offline_paid [amount] [name]</code>")
        
    prof = await get_offline_profile_by_name(message.from_user.id, name)
    if not prof:
        return await message.reply(f"❌ Offline profile '{name}' not found.")
        
    # The user serves dynamically as the creditor to naturally offset the balance curve
    await log_transaction(message.from_user.id, prof['id'], amount, "Offline Repayment", "confirmed")
    await message.reply(f"✅ Logged! You paid {amount} tg to offline user <b>{name}</b>.")
