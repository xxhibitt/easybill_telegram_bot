import re
import aiosqlite

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, ForceReply
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from database import (
    add_user,
    get_user_by_username,
    log_transaction,
    delete_transaction,
    DB_NAME
)

group_router = Router()
group_router.message.filter(F.chat.type.in_(['group', 'supergroup']))


class TransactionFlow(StatesGroup):
    waiting_for_action = State()
    waiting_for_person = State()
    waiting_for_amount = State()
    waiting_for_reason = State()


@group_router.message(Command("help"))
async def cmd_help(message: Message):
    help_text = (
        "🤖 <b>Welcome to CoffeeTab Bot!</b>\n\n"
        "Your simple group debt manager.\n\n"
        "<b>Group Commands:</b>\n"
        "💸 <code>/eg</code> - Open the interactive menu to log a transaction (Lend, Owe, or Pay someone).\n"
        "<i>Use this instead of remembering complicated slash commands!</i>\n\n"
        "<b>Private Commands (DM the bot):</b>\n"
        "📊 <code>/stats</code> - Check your net balances.\n"
        "📅 <code>/recent</code> - View your transaction history.\n\n"
        "Add me to your group and start tracking!"
    )
    await message.reply(help_text, parse_mode="HTML")


@group_router.message(Command("eg"))
async def cmd_eg(message: Message, state: FSMContext):
    await add_user(message.from_user.id, message.from_user.username)
    builder = InlineKeyboardBuilder()
    builder.button(text="💸 Lent to someone", callback_data="eg_act_lend")
    builder.button(text="🙏 Owe someone", callback_data="eg_act_owe")
    builder.button(text="🤝 Paid someone back", callback_data="eg_act_paid")
    builder.adjust(1)
    
    await message.reply(
        "What do you want to do?",
        reply_markup=builder.as_markup()
    )
    await state.set_state(TransactionFlow.waiting_for_action)


@group_router.callback_query(TransactionFlow.waiting_for_action, F.data.startswith("eg_act_"))
async def cb_eg_action(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split("_")[2]
    await state.update_data(action=action)
    
    # Query top 5 users (just distinct recent users with usernames, exclude the caller)
    top_users = []
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute(
                '''SELECT DISTINCT username 
                   FROM users 
                   WHERE username IS NOT NULL AND telegram_id != ? 
                   LIMIT 5''',
                (callback.from_user.id,)
            ) as cursor:
                rows = await cursor.fetchall()
                top_users = [row[0] for row in rows]
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error fetching users: {e}")

    builder = InlineKeyboardBuilder()
    for u in top_users:
        builder.button(text=f"@{u}", callback_data=f"eg_usr_{u}")
    builder.button(text="✍️ Type @username", callback_data="eg_usr_TYPE")
    builder.adjust(2)
    
    await callback.message.edit_text("Who?", reply_markup=builder.as_markup())
    await state.set_state(TransactionFlow.waiting_for_person)


@group_router.callback_query(TransactionFlow.waiting_for_person, F.data.startswith("eg_usr_"))
async def cb_eg_person(callback: CallbackQuery, state: FSMContext):
    target_username = callback.data.split("_", 2)[2]
    
    user_mention = (f"@{callback.from_user.username}" if callback.from_user.username 
                    else f"<a href='tg://user?id={callback.from_user.id}'>{callback.from_user.first_name}</a>")
    
    if target_username == "TYPE":
        # Force reply prompt. Note: using .answer() to send a new message with mention to trigger selective=True
        await callback.message.answer(
            f"{user_mention}, please type the @username of the person:",
            reply_markup=ForceReply(selective=True),
            parse_mode="HTML"
        )
        await callback.answer()
        # Stay in waiting_for_person state, expecting a message
    else:
        await state.update_data(target_username=target_username)
        await callback.message.answer(
            f"{user_mention}, enter the amount (e.g., 1500):",
            reply_markup=ForceReply(selective=True),
            parse_mode="HTML"
        )
        await state.set_state(TransactionFlow.waiting_for_amount)
        await callback.answer()


@group_router.message(TransactionFlow.waiting_for_person, F.text)
async def msg_eg_person(message: Message, state: FSMContext):
    text = message.text.strip()
    match = re.search(r"@?([a-zA-Z0-9_]+)", text)
    if not match:
        return await message.reply(
            "Please provide a valid @username.",
            reply_markup=ForceReply(selective=True)
        )
    
    target_username = match.group(1)
    await state.update_data(target_username=target_username)
    await message.reply(
        "Enter the amount (e.g., 1500):",
        reply_markup=ForceReply(selective=True)
    )
    await state.set_state(TransactionFlow.waiting_for_amount)


@group_router.message(TransactionFlow.waiting_for_amount, F.text)
async def msg_eg_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError("Amount must be positive")
    except ValueError:
        await message.reply(
            "Invalid number. Try again.",
            reply_markup=ForceReply(selective=True)
        )
        return
        
    await state.update_data(amount=amount)
    
    builder = InlineKeyboardBuilder()
    reasons = [("🍔 Food", "Food"), ("🚕 Taxi", "Taxi"), ("🍿 Cinema", "Cinema"), ("🛒 Other", "Other")]
    for emoji, text in reasons:
        builder.button(text=f"{emoji}", callback_data=f"eg_rsn_{text}")
    builder.button(text="✍️ Custom Reason", callback_data="eg_rsn_CUSTOM")
    builder.adjust(2)
    
    await message.reply("What for?", reply_markup=builder.as_markup())
    await state.set_state(TransactionFlow.waiting_for_reason)


async def execute_final_logic(action: str, amount: int, target_username: str, reason: str, user_id: int, message: Message):
    """Encapsulates the database logging and Confirmation/Denial logic."""
    if action == "owe":
        target_user = await get_user_by_username(target_username)
        if not target_user:
            await message.reply(f"⚠️ @{target_username} is not registered. Please ask them to start the bot in DM first!")
            return
            
        debtor_id = user_id
        creditor_id = target_user['telegram_id']
        
        await log_transaction(creditor_id, debtor_id, amount, f"[{reason}]", "confirmed")
        await message.reply(f"Recorded: You owe @{target_username} {amount} for [{reason}]")
        
    elif action == "lend":
        creditor_id = user_id
        tx_id = await log_transaction(creditor_id, 0, amount, f"[{reason}]", "pending")
        
        builder = InlineKeyboardBuilder()
        builder.button(text="Confirm ✅", callback_data=f"lent_y_{tx_id}_{target_username}")
        builder.button(text="Deny ❌", callback_data=f"lent_n_{tx_id}_{target_username}")
        
        await message.reply(
            f"@{target_username}, do you confirm you owe {amount} for [{reason}]?",
            reply_markup=builder.as_markup()
        )
        
    elif action == "paid":
        creditor_id = user_id
        tx_id = await log_transaction(creditor_id, 0, amount, f"Repayment: [{reason}]", "pending")
        
        builder = InlineKeyboardBuilder()
        builder.button(text="Confirm Receipt ✅", callback_data=f"paid_y_{tx_id}_{target_username}")
        builder.button(text="Deny ❌", callback_data=f"paid_n_{tx_id}_{target_username}")
        
        await message.reply(
            f"@{target_username}, please confirm receipt of {amount} for [{reason}].",
            reply_markup=builder.as_markup()
        )


@group_router.callback_query(TransactionFlow.waiting_for_reason, F.data.startswith("eg_rsn_"))
async def cb_eg_reason(callback: CallbackQuery, state: FSMContext):
    reason = callback.data.split("_")[2]
    
    # Catch manual "Custom Reason"
    if reason == "CUSTOM":
        user_mention = (f"@{callback.from_user.username}" if callback.from_user.username 
                        else f"<a href='tg://user?id={callback.from_user.id}'>{callback.from_user.first_name}</a>")
        
        await callback.message.answer(
            f"{user_mention}, please type your custom reason:",
            reply_markup=ForceReply(selective=True),
            parse_mode="HTML"
        )
        await callback.answer()
        return

    data = await state.get_data()
    
    # We edit the message to remove the inline keyboard to avoid clutter
    await callback.message.edit_text(f"Reason selected: {reason}")
    
    await execute_final_logic(
        action=data.get("action"),
        amount=data.get("amount"),
        target_username=data.get("target_username"),
        reason=reason,
        user_id=callback.from_user.id,
        message=callback.message
    )
    await state.clear()


@group_router.message(TransactionFlow.waiting_for_reason, F.text)
async def msg_eg_reason(message: Message, state: FSMContext):
    reason = message.text.strip()
    data = await state.get_data()
    
    await execute_final_logic(
        action=data.get("action"),
        amount=data.get("amount"),
        target_username=data.get("target_username"),
        reason=reason,
        user_id=message.from_user.id,
        message=message
    )
    await state.clear()


# ==========================================
# CONFIRMATION HANDLERS (From previous log)
# ==========================================

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
