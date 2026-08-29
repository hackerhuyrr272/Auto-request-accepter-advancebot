import asyncio
import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
from motor.motor_asyncio import AsyncIOMotorClient

# Load Environment Variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
PAYOUT_CHANNEL = os.getenv("PAYOUT_CHANNEL_ID")

# Setup Logging
logging.basicConfig(level=logging.INFO)

# Initialize Bot, Dispatcher, and Database
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()
router = Router()
dp.include_router(router)

client = AsyncIOMotorClient(MONGO_URI)
db = client['PremiumRewardBot']
users_col = db['users']
settings_col = db['settings']
tx_col = db['transactions']

# Constants
PER_REFERRAL_POINTS = 15
REDEEM_TIERS = {
    "100": {"points": 100, "rupees": 100},
    "200": {"points": 200, "rupees": 200},
    "300": {"points": 300, "rupees": 300},
    "400": {"points": 400, "rupees": 400},
    "1000": {"points": 1000, "rupees": 1000},
}

# FSM States
class AdminStates(StatesGroup):
    waiting_for_broadcast = State()
    waiting_for_giftcode = State()
    waiting_for_reject_reason = State()
    waiting_for_step1_channel = State()
    waiting_for_step2_channel = State()

# --- Database Helpers ---
async def get_settings():
    settings = await settings_col.find_one({"_id": "config"})
    if not settings:
        settings = {
            "_id": "config",
            "step1_channels": [], # list of channel usernames like "@mychannel"
            "step2_channels": [],
            "step2_enabled": False
        }
        await settings_col.insert_one(settings)
    return settings

# --- Keyboards ---
def main_menu_kb():
    kb = [
        [KeyboardButton(text="👤 Account"), KeyboardButton(text="🔥 Invites")],
        [KeyboardButton(text="💲 Redeem Code")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- Core Functions ---
async def check_membership(user_id, channel_username):
    try:
        member = await bot.get_chat_member(chat_id=channel_username, user_id=user_id)
        if member.status in ['member', 'administrator', 'creator']:
            return True
        return False
    except TelegramBadRequest:
        return False # Bot is not admin or channel doesn't exist

async def enforce_joins(message: Message, state: FSMContext, step: int = 1):
    settings = await get_settings()
    channels = settings['step1_channels'] if step == 1 else settings['step2_channels']
    
    if not channels or (step == 2 and not settings['step2_enabled']):
        if step == 1 and settings['step2_enabled']:
            return await enforce_joins(message, state, step=2)
        return True # All cleared
        
    not_joined = []
    buttons = []
    for ch in channels:
        if not await check_membership(message.from_user.id, ch):
            not_joined.append(ch)
            buttons.append([InlineKeyboardButton(text=f"↗️ JOIN {ch}", url=f"https://t.me/{ch.replace('@', '')}")])
            
    if not_joined:
        buttons.append([InlineKeyboardButton(text="[ JOINED ]", callback_data=f"check_join_{step}")])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        text = f"🛑 <b>Must Join {'All' if step==1 else 'Sponsor'} Channels To Proceed!</b>\n\nClick the buttons below to join, then click <b>[ JOINED ]</b>."
        
        # If it's a callback query edit message, else send new
        if isinstance(message, CallbackQuery):
            await message.message.edit_text(text, reply_markup=kb)
        else:
            await message.answer(text, reply_markup=kb)
        return False
        
    if step == 1 and settings['step2_enabled']:
        return await enforce_joins(message, state, step=2)
    return True

# --- Handlers ---

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    args = message.text.split(" ")
    ref_id = None
    if len(args) > 1 and args[1].isdigit():
        ref_id = int(args[1])

    # Check if user exists
    user = await users_col.find_one({"_id": user_id})
    if not user:
        # Register new user
        new_user = {
            "_id": user_id,
            "name": message.from_user.first_name,
            "balance": 0.0,
            "total_referrals": 0,
            "referred_by": ref_id if ref_id != user_id else None,
            "joined_date": datetime.now()
        }
        await users_col.insert_one(new_user)
        
        # Reward Referrer (only if they clear force join later, but for simplicity we credit here)
        if ref_id and ref_id != user_id:
            await users_col.update_one({"_id": ref_id}, {"$inc": {"balance": PER_REFERRAL_POINTS, "total_referrals": 1}})
            try:
                await bot.send_message(ref_id, f"🎉 <b>New Referral!</b>\n<a href='tg://user?id={user_id}'>{message.from_user.first_name}</a> joined using your link.\n💰 You earned <b>{PER_REFERRAL_POINTS} Points</b>!")
            except:
                pass

    cleared = await enforce_joins(message, state, step=1)
    if cleared:
        await send_welcome(message)

async def send_welcome(message: Message):
    text = (
        f"✨ <b>Welcome {message.from_user.first_name} to the Premium Reward Bot!</b>\n\n"
        "Earn points by completing tasks and inviting friends, then redeem them for Google Play Gift Cards.\n\n"
        "👇 <b>Select an option from the menu below to start earning!</b>"
    )
    if isinstance(message, CallbackQuery):
        await message.message.delete()
        await bot.send_message(message.from_user.id, text, reply_markup=main_menu_kb())
    else:
        await message.answer(text, reply_markup=main_menu_kb())

@router.callback_query(F.data.startswith("check_join_"))
async def check_join_callback(call: CallbackQuery, state: FSMContext):
    step = int(call.data.split("_")[2])
    cleared = await enforce_joins(call, state, step=step)
    if cleared:
        await call.answer("✅ Verification Successful!", show_alert=True)
        await send_welcome(call)
    else:
        await call.answer("❌ You haven't joined all channels yet!", show_alert=True)

@router.message(F.text == "👤 Account")
async def btn_account(message: Message):
    user = await users_col.find_one({"_id": message.from_user.id})
    if not user:
        return
    text = (
        "👑 <b>Account Overview</b>\n\n"
        f"👤 <b>User:</b> {user['name']}\n"
        f"🆔 <b>ID:</b> <code>{user['_id']}</code>\n\n"
        f"💲 <b>Balance:</b> {user['balance']} Points\n"
        f"💎 <b>Referrals:</b> {user['total_referrals']}\n\n"
        "⚡ <i>Tip: Invite friends to earn more rewards instantly!</i>"
    )
    await message.answer(text, reply_markup=main_menu_kb())

@router.message(F.text == "🔥 Invites")
async def btn_invites(message: Message):
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={message.from_user.id}"
    
    text = (
        "⚡ <b>Invitation Center</b>\n\n"
        f"🔗 <b>Your Invite Link:</b>\n<code>{ref_link}</code>\n\n"
        f"💲 <b>Reward:</b> {PER_REFERRAL_POINTS} Points Per Successful Invite.\n\n"
        "🔥 <i>Pro Tip: Share your link with active friends for maximum earnings!</i>"
    )
    # Adding a share button
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Share Link", url=f"https://t.me/share/url?url={ref_link}&text=Join%20this%20awesome%20bot%20to%20earn%20free%20gift%20cards!")]
    ])
    await message.answer(text, reply_markup=kb)

@router.message(F.text == "💲 Redeem Code")
async def btn_redeem(message: Message):
    user = await users_col.find_one({"_id": message.from_user.id})
    if not user:
        return
    
    text = (
        "🛍 <b>Google Play Redeem Store</b>\n"
        "👑 Choose Your Gift Card.\n"
        f"💲 <b>Your Balance:</b> {user['balance']} Points"
    )
    
    buttons = []
    for tier_id, data in REDEEM_TIERS.items():
        buttons.append([InlineKeyboardButton(
            text=f"💲 💳 {data['points']} Points • ₹{data['rupees']}", 
            callback_data=f"redeem_{tier_id}"
        )])
        
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(text, reply_markup=kb)

@router.callback_query(F.data.startswith("redeem_"))
async def process_redemption(call: CallbackQuery):
    tier_id = call.data.split("_")[1]
    tier_data = REDEEM_TIERS.get(tier_id)
    if not tier_data:
        return await call.answer("Invalid tier.", show_alert=True)
        
    user = await users_col.find_one({"_id": call.from_user.id})
    cost = tier_data['points']
    rupees = tier_data['rupees']
    
    if user['balance'] < cost:
        short = cost - user['balance']
        err_text = (
            "❌ <b>Not Enough Points!</b>\n\n"
            f"💰 <b>Your Balance:</b> {user['balance']} Points\n"
            f"💲 <b>Required:</b> {cost} Points\n"
            f"🔥 <b>Need:</b> {short} More Points\n\n"
            "💌 <i>Invite friends to earn more points!</i>"
        )
        return await call.message.edit_text(err_text)
        
    # Deduct points
    await users_col.update_one({"_id": user['_id']}, {"$inc": {"balance": -cost}})
    
    # Save transaction
    tx_doc = {
        "user_id": user['_id'],
        "user_name": user['name'],
        "cost": cost,
        "rupees": rupees,
        "status": "PENDING",
        "date": datetime.now()
    }
    result = await tx_col.insert_one(tx_doc)
    tx_id = str(result.inserted_id)
    
    # Send to payout channel
    payout_text = (
        "💳 <b>New Redemption Request</b>\n"
        f"👤 <b>User:</b> <a href='tg://user?id={user['_id']}'>{user['name']}</a>\n"
        f"💰 <b>Amount:</b> ₹{rupees} Gift Card\n"
        "⏳ <b>Status:</b> 🟡 PENDING"
    )
    payout_msg = await bot.send_message(PAYOUT_CHANNEL, payout_text)
    
    # Update tx with payout message ID
    await tx_col.update_one({"_id": result.inserted_id}, {"$set": {"payout_msg_id": payout_msg.message_id}})
    
    # Notify Admin
    admin_text = (
        "🚨 <b>New Redemption Request!</b>\n"
        f"User ID: <code>{user['_id']}</code>\n"
        f"Amount: ₹{rupees} (Cost: {cost} Points)\n"
        f"Tx ID: <code>{tx_id}</code>"
    )
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Approve", callback_data=f"adm_approve_{tx_id}"), 
         InlineKeyboardButton(text="❌ Reject", callback_data=f"adm_reject_{tx_id}")]
    ])
    await bot.send_message(ADMIN_ID, admin_text, reply_markup=admin_kb)
    
    await call.message.edit_text(f"✅ <b>Request Submitted!</b>\n\nYour request for ₹{rupees} has been sent to the admins. It is currently pending.", reply_markup=main_menu_kb())

# --- Admin Panel ---
@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
        
    total_users = await users_col.count_documents({})
    settings = await get_settings()
    
    text = (
        "🛠 <b>Admin Panel</b>\n\n"
        f"👥 Total Users: {total_users}\n"
        f"⚙️ Step 2 Force Join: {'✅ ON' if settings['step2_enabled'] else '❌ OFF'}\n"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Broadcast Message", callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="🔄 Toggle Step 2", callback_data="adm_toggle_s2")]
    ])
    await message.answer(text, reply_markup=kb)

@router.callback_query(F.data == "adm_toggle_s2")
async def toggle_step2(call: CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    settings = await get_settings()
    new_status = not settings['step2_enabled']
    await settings_col.update_one({"_id": "config"}, {"$set": {"step2_enabled": new_status}})
    await call.answer(f"Step 2 Force Join is now {'ON' if new_status else 'OFF'}", show_alert=True)

# Admin Approvals
@router.callback_query(F.data.startswith("adm_approve_"))
async def adm_approve(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    tx_id = call.data.split("_")[2]
    
    await state.update_data(tx_id=tx_id)
    await state.set_state(AdminStates.waiting_for_giftcode)
    await call.message.answer(f"✅ Please send the Google Play Redeem Code for transaction <code>{tx_id}</code>:")
    await call.answer()

@router.message(AdminStates.waiting_for_giftcode)
async def process_giftcode(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    
    data = await state.get_data()
    tx_id = data['tx_id']
    gift_code = message.text
    
    from bson.objectid import ObjectId
    tx = await tx_col.find_one({"_id": ObjectId(tx_id)})
    
    if not tx:
        return await message.answer("Transaction not found!")
        
    # Update DB
    await tx_col.update_one({"_id": ObjectId(tx_id)}, {"$set": {"status": "APPROVED", "code": gift_code}})
    
    # Notify User
    user_text = (
        "🎉 <b>Redemption Approved!</b>\n\n"
        f"💳 <b>Amount:</b> ₹{tx['rupees']}\n"
        f"🔑 <b>Your Code:</b> <code>{gift_code}</code>\n\n"
        "<i>Tap the code to copy it!</i>"
    )
    await bot.send_message(tx['user_id'], user_text)
    
    # Update Payout Channel
    payout_text = (
        "💳 <b>Redemption Successful!</b>\n"
        f"👤 <b>User:</b> <a href='tg://user?id={tx['user_id']}'>{tx['user_name']}</a>\n"
        f"💰 <b>Amount:</b> ₹{tx['rupees']} Gift Card\n"
        "⏳ <b>Status:</b> ✅ APPROVED"
    )
    try:
        await bot.edit_message_text(text=payout_text, chat_id=PAYOUT_CHANNEL, message_id=tx['payout_msg_id'])
    except Exception as e:
        logging.error(f"Could not update payout channel: {e}")
        
    await message.answer("✅ Code sent to user and payout channel updated!")
    await state.clear()

@router.callback_query(F.data.startswith("adm_reject_"))
async def adm_reject(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    tx_id = call.data.split("_")[2]
    
    from bson.objectid import ObjectId
    tx = await tx_col.find_one({"_id": ObjectId(tx_id)})
    
    # Refund Points
    await users_col.update_one({"_id": tx['user_id']}, {"$inc": {"balance": tx['cost']}})
    await tx_col.update_one({"_id": ObjectId(tx_id)}, {"$set": {"status": "REJECTED"}})
    
    # Notify User
    await bot.send_message(tx['user_id'], f"❌ <b>Redemption Rejected.</b>\nYour request for ₹{tx['rupees']} was rejected. Your {tx['cost']} points have been refunded.")
    
    # Update Payout Channel
    payout_text = (
        "💳 <b>Redemption Request</b>\n"
        f"👤 <b>User:</b> <a href='tg://user?id={tx['user_id']}'>{tx['user_name']}</a>\n"
        f"💰 <b>Amount:</b> ₹{tx['rupees']} Gift Card\n"
        "⏳ <b>Status:</b> ❌ REJECTED"
    )
    try:
        await bot.edit_message_text(text=payout_text, chat_id=PAYOUT_CHANNEL, message_id=tx['payout_msg_id'])
    except Exception as e:
        logging.error(f"Could not update payout channel: {e}")
        
    await call.message.edit_text("❌ Request rejected and points refunded.")

# Broadcast Feature
@router.callback_query(F.data == "adm_broadcast")
async def adm_broadcast_start(call: CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_for_broadcast)
    await call.message.answer("Send the message you want to broadcast (Text, Photo, or Video):")
    await call.answer()

@router.message(AdminStates.waiting_for_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    
    await message.answer("📢 Broadcasting message... This might take a while.")
    cursor = users_col.find({})
    success = 0
    async for user in cursor:
        try:
            await message.send_copy(chat_id=user['_id'])
            success += 1
            await asyncio.sleep(0.05) # Prevent flood limits
        except Exception:
            pass
            
    await message.answer(f"✅ Broadcast complete! Sent to {success} users.")
    await state.clear()


async def main():
    print("Bot is starting...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
