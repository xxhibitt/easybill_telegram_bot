from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
import re

from database import (
    add_user,
    get_user_by_username,
    log_transaction,
    delete_transaction,
    update_transaction_reason,
    get_transaction,
    DB_NAME
)
import aiosqlite

group_router = Router()
group_router.message.filter(F.chat.type.in_(['group', 'supergroup']))

async def parse_args(text: str, command: str):
    """Utility to parse amount and @username from command text"""
    pattern = rf"(?i)^/{command}(?:@[a-zA-Z0-9_]+)?\s+(\d+)\s+@(\w+)(?:\s+(.*))?"
    match = re.match(pattern, text)
    if match:
        amount = int(match.group(1))
        target_username = match.group(2)
        reason = match.group(3) or ""
        return amount, target_username, reason
    return None, None, None


@group_router.message(Command("owe"))
async def cmd_owe(message: Message):
    await add_user(message.from_user.id, message.from_user.username)
    amount, target_username, _ = await parse_args(message.text, "owe")
    
    if not amount:
        return await message.reply("❌ Format: <code>/owe [amount] [@username]</code>")
        
    target_user = await get_user_by_username(target_username)
    if not target_user:
        return await message.reply(f"⚠️ @{target_username} is not registered. Please ask them to start the bot in DM first!")
        
    debtor_id = message.from_user.id
    creditor_id = target_user['telegram_id']
    
    # Log immediately as confirmed
    tx_id = await log_transaction(creditor_id, debtor_id, amount, "Pending tag", "confirmed")
    
    # Render inline keyboard for tags
    builder = InlineKeyboardBuilder()
    reasons = [("🍔 Food", "Food"), ("🚕 Taxi", "Taxi"), ("🍿 Cinema", "Cinema"), ("🛒 Other", "Other")]
    for emoji, text in reasons:
        # tx_owe_ {tx_id} _ {text} _ {target_username}
        builder.button(text=emoji, callback_data=f"tx_owe_{tx_id}_{text}_{target_username}")
    builder.adjust(2)
    
    await message.reply(
        "Please select a reason tag:",
        reply_markup=builder.as_markup()
    )


@group_router.callback_query(F.data.startswith("tx_owe_"))
async def cb_owe_reason(callback: CallbackQuery):
    parts = callback.data.split("_")
    tx_id = int(parts[2])
    tag = parts[3]
    target_username = parts[4]
    
    tx = await get_transaction(tx_id)
    if not tx:
        return await callback.answer("Transaction not found.", show_alert=True)
        
    if callback.from_user.id != tx['debtor_id']:
        return await callback.answer("Only the person who submitted /owe can select the tag!", show_alert=True)
        
    # Update reason in database
    await update_transaction_reason(tx_id, f"[{tag}]")
    
    # Edit original message
    await callback.message.edit_text(f"Recorded: You owe @{target_username} {tx['amount']} for [{tag}]")


@group_router.message(Command("lent"))
async def cmd_lent(message: Message):
    await add_user(message.from_user.id, message.from_user.username)
    amount, target_username, reason = await parse_args(message.text, "lent")
    
    if not amount:
        return await message.reply("❌ Format: <code>/lent [amount] [@username] [reason]</code>")
        
    creditor_id = message.from_user.id
    
    # Log as pending. Debtor ID is temporarily 0 until confirmation
    tx_id = await log_transaction(creditor_id, 0, amount, reason, "pending")
    
    builder = InlineKeyboardBuilder()
    builder.button(text="Confirm ✅", callback_data=f"lent_y_{tx_id}_{target_username}")
    builder.button(text="Deny ❌", callback_data=f"lent_n_{tx_id}_{target_username}")
    
    await message.reply(
        f"@{target_username}, do you confirm you owe {amount}?",
        reply_markup=builder.as_markup()
    )


@group_router.callback_query(F.data.startswith("lent_"))
async def cb_lent_confirm(callback: CallbackQuery):
    parts = callback.data.split("_")
    action = parts[1]
    tx_id = int(parts[2])
    target_username = parts[3]
    
    username = callback.from_user.username or ""
    if username.lower() != target_username.lower():
        return await callback.answer("This button is for the tagged user only!", show_alert=True)
        
    await add_user(callback.from_user.id, callback.from_user.username)
    
    if action == "y":
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE transactions SET debtor_id = ?, status = 'confirmed' WHERE id = ?", (callback.from_user.id, tx_id))
            await db.commit()
        await callback.message.edit_text("Debt confirmed!")
    else:
        await delete_transaction(tx_id)
        await callback.message.edit_text("Debt denied.")


@group_router.message(Command("paid"))
async def cmd_paid(message: Message):
    await add_user(message.from_user.id, message.from_user.username)
    amount, target_username, _ = await parse_args(message.text, "paid")
    
    if not amount:
        return await message.reply("❌ Format: <code>/paid [amount] [@username]</code>")
        
    # Sender is paying money, mathematically making them the creditor to offset their existing debts
    creditor_id = message.from_user.id
    
    # Log as pending
    tx_id = await log_transaction(creditor_id, 0, amount, "Repayment", "pending")
    
    builder = InlineKeyboardBuilder()
    builder.button(text="Confirm Receipt ✅", callback_data=f"paid_y_{tx_id}_{target_username}")
    builder.button(text="Deny ❌", callback_data=f"paid_n_{tx_id}_{target_username}")
    
    await message.reply(
        f"@{target_username}, please confirm receipt of {amount}.",
        reply_markup=builder.as_markup()
    )


@group_router.callback_query(F.data.startswith("paid_"))
async def cb_paid_confirm(callback: CallbackQuery):
    parts = callback.data.split("_")
    action = parts[1]
    tx_id = int(parts[2])
    target_username = parts[3]
    
    username = callback.from_user.username or ""
    if username.lower() != target_username.lower():
        return await callback.answer("This button is for the tagged user only!", show_alert=True)
        
    await add_user(callback.from_user.id, callback.from_user.username)
    
    if action == "y":
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE transactions SET debtor_id = ?, status = 'confirmed' WHERE id = ?", (callback.from_user.id, tx_id))
            await db.commit()
        await callback.message.edit_text("Receipt confirmed!")
    else:
        await delete_transaction(tx_id)
        await callback.message.edit_text("Receipt denied.")
