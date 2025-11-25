"""
MatruRaksha AI - Telegram Bot

Provides conversational maternal health support, home dashboard, profile switching,
document uploads, and AI-powered answers.
"""

import os
import json
import html
import logging
from uuid import uuid4
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler,
)

from services.supabase_service import (
    get_mothers_by_telegram_id,
    get_recent_reports_for_mother,
    supabase,
)
from agents.orchestrator import route_message
from services.memory_service import save_chat_history

logger = logging.getLogger(__name__)

# States for registration (aligned with main.py)
(AWAITING_NAME, AWAITING_AGE, AWAITING_PHONE, AWAITING_DUE_DATE,
 AWAITING_LOCATION, AWAITING_GRAVIDA, AWAITING_PARITY, AWAITING_BMI,
 AWAITING_LANGUAGE, CONFIRM_REGISTRATION) = range(10)

TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
BACKEND_API_BASE_URL = (os.getenv("BACKEND_API_BASE_URL") or "http://localhost:8000").strip()

# Dashboard & summary configuration
MAX_TIMELINE_EVENTS = 5
MAX_MEMORIES = 5
MAX_REPORTS = 5

# Language mapping for user input and callback codes
LANG_MAP = {
    # Text inputs
    "english": "en",
    "hindi": "hi",
    "marathi": "mr",
    # Callback codes
    "en": "en",
    "hi": "hi",
    "mr": "mr",
}

def _format_date(date_str: Optional[str]) -> str:
    if not date_str:
        return "N/A"
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y")
    except Exception:
        return date_str[:10]


def _calculate_pregnancy_status(due_date: Optional[str]) -> Optional[str]:
    if not due_date:
        return None
    try:
        due = datetime.fromisoformat(due_date.replace("Z", "+00:00"))
        conception = due - timedelta(weeks=40)
        weeks = max(0, min(42, (datetime.now() - conception).days // 7))
        months = max(1, min(10, weeks // 4 or 1))
        return f"Week {weeks} (Month {months})"
    except Exception:
        return None


def _build_dashboard_keyboard(
    mothers: List[Dict[str, Any]],
    active_id: Optional[str],
    show_switch_panel: bool = False,
) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton("📄 Health Reports", callback_data="action_summary")],
        [InlineKeyboardButton("🔁 Switch Profiles", callback_data="action_open_switch")],
        [InlineKeyboardButton("📎 Upload Documents", callback_data="action_upload_hint")],
        [InlineKeyboardButton("🆕 Register Another Mother", callback_data="action_register")],
    ]

    switch_buttons: List[List[InlineKeyboardButton]] = []
    for mother in mothers:
        mother_id = str(mother.get("id"))
        if not mother_id or mother_id == str(active_id):
            continue
        label = mother.get("name") or "Mother"
        switch_buttons.append([
            InlineKeyboardButton(f"👩 {label}", callback_data=f"switch_mother_{mother_id}")
        ])

    if show_switch_panel:
        rows.append([InlineKeyboardButton("❌ Hide Profiles", callback_data="action_close_switch")])
        rows.extend(switch_buttons)

    return InlineKeyboardMarkup(rows)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    context.user_data["chat_id"] = chat_id

    mothers = await get_mothers_by_telegram_id(chat_id)
    if not mothers:
        context.user_data["mothers_list"] = []
        context.user_data.pop("active_mother", None)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🆕 Register Mother", callback_data="register_new")]
        ])
        text = (
            "👋 Welcome to MatruRaksha AI!\n\n"
            "It looks like you haven't registered yet.\n\n"
            f"🆔 Your Telegram Chat ID: `{chat_id}`\n\n"
            "Tap the button below to register as a new mother or use /register."
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)
        return

    context.user_data["mothers_list"] = mothers
    active = context.user_data.get("active_mother") or mothers[0]
    context.user_data["active_mother"] = active
    context.user_data["show_switch_panel"] = False

    await send_home_dashboard(update, context, mother=active, mothers=mothers, as_new_message=True)

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Suspend agents and mark registration active when starting via /register
    context.chat_data['registration_active'] = True
    context.chat_data['agents_suspended'] = True
    context.user_data['registration_data'] = {}
    await update.message.reply_text("Please enter your full name:")
    return AWAITING_NAME


async def send_home_dashboard(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    mother: Optional[Dict[str, Any]] = None,
    mothers: Optional[List[Dict[str, Any]]] = None,
    as_new_message: bool = False,
) -> None:
    """Send or refresh the home dashboard with profile highlights and actions."""
    chat = getattr(update, "effective_chat", None) or getattr(update, "chat", None)
    chat_id = context.user_data.get("chat_id") or (str(chat.id) if chat else None)

    if mothers is None:
        if chat_id:
            mothers = await get_mothers_by_telegram_id(chat_id)
            context.user_data["mothers_list"] = mothers
        else:
            mothers = []

    if mother is None:
        mother = context.user_data.get("active_mother") or (mothers[0] if mothers else None)

    if not mother:
        if chat_id:
            await context.bot.send_message(
                chat_id=chat_id,
                text="It looks like no mother profile is linked to this chat yet. Use /register to create one.",
                parse_mode=ParseMode.MARKDOWN,
            )
        return

    context.user_data["active_mother"] = mother
    active_id = str(mother.get("id"))
    show_switch = context.user_data.get("show_switch_panel", False)
    keyboard = _build_dashboard_keyboard(mothers or [mother], active_id, show_switch_panel=show_switch)

    pregnancy_line = _calculate_pregnancy_status(mother.get("due_date"))
    due_line = _format_date(mother.get("due_date"))
    name = mother.get("name") or "Mother"
    location = mother.get("location") or "Not set"

    lines = [
        f"👋 *Welcome back, {name}!*",
        "",
        f"🆔 *Telegram Chat ID:* `{chat_id}`" if chat_id else "",
        f"👩‍🍼 *Active Profile:* {name}",
        f"📍 *Location:* {location}",
        f"📅 *Due Date:* {due_line}",
        f"🤰 *Pregnancy:* {pregnancy_line}" if pregnancy_line else "",
        "",
        "Use the buttons below to view your health summary, upload documents, "
        "or switch between registered mothers.",
    ]

    text = "\n".join(filter(None, lines))

    if getattr(update, "callback_query", None) and not as_new_message:
        await update.callback_query.message.edit_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    else:
        if chat_id:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )


async def handle_home_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle dashboard action buttons."""
    query = update.callback_query
    action = query.data.replace("action_", "", 1)

    # Block all actions except starting registration when registration is active
    if context.chat_data.get('registration_active') and action != "register":
        await query.answer()
        await query.message.reply_text("Finish registration first or send /cancel.")
        return

    if action == "summary":
        await query.answer("Fetching summary…")
        await action_summary(update, context)
    elif action == "register":
        await query.answer()
        await _prompt_registration(query)
    elif action == "upload_hint":
        await query.answer("Upload a PDF/image as a message.", show_alert=True)
    elif action == "open_switch":
        await query.answer("Choose a profile to make it active.")
        context.user_data["show_switch_panel"] = True
        await send_home_dashboard(update, context, as_new_message=False)
    elif action == "close_switch":
        await query.answer("Hiding switch panel.")
        context.user_data["show_switch_panel"] = False
        await send_home_dashboard(update, context, as_new_message=False)
    else:
        await query.answer()


async def _prompt_registration(query):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Register Mother", callback_data="register_new")]
    ])
    await query.message.reply_text(
        "Tap below to start the maternal profile registration flow.",
        reply_markup=keyboard,
    )

async def register_button_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        context.user_data['registration_data'] = {}
        # Suspend agents and mark registration active
        context.chat_data['registration_active'] = True
        context.chat_data['agents_suspended'] = True
        await query.message.reply_text("Please enter your full name:")
    else:
        # Suspend agents and mark registration active
        context.chat_data['registration_active'] = True
        context.chat_data['agents_suspended'] = True
        await update.effective_chat.send_message("Please enter your full name:")
    return AWAITING_NAME


async def action_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetch and display an enriched health summary for the active mother."""
    query = update.callback_query
    mother = context.user_data.get("active_mother")
    if not mother:
        await query.message.reply_text("⚠️ No active mother profile. Please register first.")
        return

    mother_id = str(mother.get("id"))
    name = mother.get("name", "Mother")
    summary_lines = [
        f"<b>📊 Health Summary for {html.escape(name)}</b>",
        f"<b>🆔 Mother ID:</b> <code>{html.escape(mother_id)}</code>",
    ]

    pregnancy_status = _calculate_pregnancy_status(mother.get("due_date"))
    if pregnancy_status:
        summary_lines.append(f"<b>🤰 Pregnancy:</b> {html.escape(pregnancy_status)}")
    if mother.get("due_date"):
        summary_lines.append(f"<b>📅 Due Date:</b> {html.escape(_format_date(mother.get('due_date')))}")
    if mother.get("location"):
        summary_lines.append(f"<b>📍 Location:</b> {html.escape(mother.get('location'))}")
    # Use a plain newline separator instead of unsupported <br>
    summary_lines.append("")

    try:
        async with aiohttp.ClientSession() as session:
            url = f"{BACKEND_API_BASE_URL}/api/v1/summary/{mother_id}"
            timeout = aiohttp.ClientTimeout(total=25)
            async with session.get(url, timeout=timeout) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Summary API returned {resp.status}")
                summary_payload = await resp.json()
    except Exception as exc:
        logger.error(f"Summary endpoint failed: {exc}")
        summary_lines.append("⚠️ Unable to fetch latest summary right now. Please try again later.")
        await query.message.reply_text(
            "\n".join(summary_lines),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return

    recent_timeline = summary_payload.get("recent_timeline", [])[:MAX_TIMELINE_EVENTS]
    key_memories = summary_payload.get("key_memories", [])[:MAX_MEMORIES]
    reports = await get_recent_reports_for_mother(mother_id, limit=MAX_REPORTS)

    if recent_timeline:
        summary_lines.append("<b>🗂 Key Timeline Events:</b>")
        for event in recent_timeline:
            date = _format_date(event.get("event_date") or event.get("date") or event.get("created_at"))
            text = event.get("summary") or event.get("event_summary") or "Update"
            summary_lines.append(f"• {html.escape(date)}: {html.escape(text)}")
        summary_lines.append("")

    if key_memories:
        summary_lines.append("<b>🧠 Important Notes:</b>")
        for memory in key_memories:
            summary_lines.append(
                f"• {html.escape(str(memory.get('memory_key', 'Note')))}: "
                f"{html.escape(str(memory.get('memory_value', '')))}"
            )
        summary_lines.append("")

    if reports:
        summary_lines.append("<b>📎 Uploaded Documents:</b>")
        for report in reports:
            title = report.get("file_name") or report.get("filename") or "Document"
            uploaded_at = _format_date(report.get("uploaded_at") or report.get("created_at"))
            analysis_summary = report.get("analysis_summary")
            summary_lines.append(f"• {html.escape(uploaded_at)} — {html.escape(title)}")
            if analysis_summary:
                summary_lines.append(f"  ↳ {html.escape(str(analysis_summary))}")
        summary_lines.append("")
    else:
        summary_lines.append("📎 No documents uploaded yet.")
        summary_lines.append("")

    if summary_payload.get("summary") and isinstance(summary_payload["summary"], dict):
        overview = summary_payload["summary"]
        recommendations = overview.get("recommendations")
        risks = overview.get("risk_flags") or overview.get("risks")
        if recommendations:
            summary_lines.append("<b>✅ Recommendations:</b>")
            if isinstance(recommendations, list):
                for rec in recommendations[:5]:
                    summary_lines.append(f"• {html.escape(str(rec))}")
            else:
                summary_lines.append(f"• {html.escape(str(recommendations))}")
            summary_lines.append("")
        if risks:
            summary_lines.append("<b>⚠️ Risks / Alerts:</b>")
            if isinstance(risks, list):
                for risk in risks[:5]:
                    summary_lines.append(f"• {html.escape(str(risk))}")
            else:
                summary_lines.append(f"• {html.escape(str(risks))}")
            summary_lines.append("")

    summary_lines.append("💬 Ask me anything for personalized guidance based on these records.")

    await query.message.reply_text(
        "\n".join(summary_lines),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def handle_switch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle switching between registered mothers via inline buttons."""
    query = update.callback_query
    await query.answer()

    # Block switching during active registration
    if context.chat_data.get('registration_active'):
        await query.message.reply_text("Finish registration first or send /cancel.")
        return

    mother_id = query.data.replace("switch_mother_", "", 1)
    chat_id = context.user_data.get("chat_id") or str(query.message.chat.id)

    mothers = context.user_data.get("mothers_list")
    if not mothers:
        mothers = await get_mothers_by_telegram_id(chat_id)
        context.user_data["mothers_list"] = mothers

    target = next((m for m in mothers if str(m.get("id")) == mother_id), None)
    if not target:
        await query.message.reply_text("⚠️ Could not find that profile. Please try again.")
        return

    context.user_data["active_mother"] = target
    context.user_data["show_switch_panel"] = False
    await send_home_dashboard(update, context, as_new_message=False)


async def handle_document_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle PDF/image uploads and push them to Supabase for analysis."""
    # Block uploads during active registration
    if context.chat_data.get('registration_active'):
        await update.message.reply_text("Finish registration first or send /cancel.")
        return

    chat_id = str(update.effective_chat.id)
    context.user_data["chat_id"] = chat_id

    mother = context.user_data.get("active_mother")
    mothers = context.user_data.get("mothers_list")
    if not mother:
        if not mothers:
            mothers = await get_mothers_by_telegram_id(chat_id)
            context.user_data["mothers_list"] = mothers
        if mothers:
            mother = mothers[0]
            context.user_data["active_mother"] = mother

    if not mother:
        await update.message.reply_text(
            "⚠️ No mother profile found. Use /register to add one before uploading reports."
        )
        return

    mother_id = mother.get("id")
    document = update.message.document
    photo = update.message.photo
    file_info = None
    filename = ""
    file_type = ""

    try:
        if document:
            file_info = await context.bot.get_file(document.file_id)
            filename = document.file_name or f"document_{document.file_id}"
            file_type = filename.split(".")[-1].lower() if "." in filename else "unknown"
        elif photo:
            largest_photo = max(photo, key=lambda p: p.file_size or 0)
            file_info = await context.bot.get_file(largest_photo.file_id)
            filename = f"photo_{largest_photo.file_id}.jpg"
            file_type = "jpg"
        else:
            await update.message.reply_text("Please send a PDF or image to upload.")
            return

        if file_type not in ["pdf", "jpg", "jpeg", "png", "webp"]:
            await update.message.reply_text(
                f"❌ Unsupported file type: {file_type}. Please upload PDF or image files."
            )
            return

        processing_msg = await update.message.reply_text(
            f"📄 Received *{filename}*\n"
            f"⏳ Uploading to your health records...",
            parse_mode=ParseMode.MARKDOWN,
        )

        file_url = file_info.file_path
        if not file_url.startswith("http"):
            file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_url}"

        report_id = str(uuid4())
        insert_data = {
            "id": report_id,
            "mother_id": mother_id,
            "telegram_chat_id": chat_id,
            "file_name": filename,
            "file_type": file_type,
            "file_url": file_url,
            "file_path": file_url,
            "uploaded_at": datetime.now().isoformat(),
            "analysis_status": "processing",
            "created_at": datetime.now().isoformat(),
        }

        supabase.table("medical_reports").insert(insert_data).execute()

        try:
            async with aiohttp.ClientSession() as session:
                analyze_url = f"{BACKEND_API_BASE_URL}/analyze-report"
                payload = {
                    "mother_id": str(mother_id),
                    "report_id": report_id,
                    "file_url": file_url,
                    "file_type": file_type,
                }
                timeout = aiohttp.ClientTimeout(total=60)
                async with session.post(analyze_url, json=payload, timeout=timeout) as resp:
                    if resp.status == 200:
                        analysis = await resp.json()
                        concerns = analysis.get("concerns") or []
                        risk_level = (analysis.get("risk_level") or "normal").upper()
                        msg = (
                            f"✅ *Document uploaded & analyzed!*\n\n"
                            f"📄 File: {filename}\n"
                            f"📊 Risk Level: {risk_level}\n"
                        )
                        if concerns:
                            msg += "⚠️ Concerns:\n"
                            for concern in concerns[:3]:
                                msg += f"• {concern}\n"
                        msg += "\nUse /start to refresh your dashboard."
                        await processing_msg.edit_text(msg, parse_mode=ParseMode.MARKDOWN)
                    else:
                        await processing_msg.edit_text(
                            "✅ Document uploaded!\n\n"
                            "Analysis will continue in the background. "
                            "Check back in a minute.",
                            parse_mode=ParseMode.MARKDOWN,
                        )
        except Exception as api_error:
            logger.error(f"Document analysis error: {api_error}")
            await processing_msg.edit_text(
                "✅ Document uploaded!\n\n"
                "Analysis is running in the background.",
                parse_mode=ParseMode.MARKDOWN,
            )

        await save_chat_history(
            mother_id,
            "document",
            f"Uploaded document {filename}",
            telegram_chat_id=chat_id,
        )
    except Exception as exc:
        logger.error(f"Document upload failed: {exc}", exc_info=True)
        await update.message.reply_text(
            f"❌ Error uploading document: {exc}\nPlease try again."
        )

async def receive_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Handle both text replies and button callbacks
    query = update.callback_query
    if query:
        await query.answer()
        data = (query.data or "").strip()
        code = data.replace("lang_", "", 1) if data.startswith("lang_") else data
        lang = LANG_MAP.get(code.lower())
        target = query.message
    else:
        text = (update.message.text or "").strip().lower()
        lang = LANG_MAP.get(text)
        target = update.message

    if not lang:
        await target.reply_text("Please choose a valid language: English, Hindi, or Marathi.")
        return AWAITING_LANGUAGE

    context.user_data.setdefault('registration_data', {})
    context.user_data['registration_data']['preferred_language'] = lang

    await target.reply_text("Processing your registration...")
    return await finalize_registration(target, context)

# === Wrapper bot class to match main.py expectations ===
class MatruRakshaBot:
    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await register_button_entry(update, context)

    async def receive_name(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await globals()["receive_name"](update, context)

    async def receive_age(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await globals()["receive_age"](update, context)

    async def receive_phone(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await globals()["receive_phone"](update, context)

    async def receive_due_date(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await globals()["receive_due_date"](update, context)

    async def receive_location(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await globals()["receive_location"](update, context)

    async def receive_gravida(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await globals()["receive_gravida"](update, context)

    async def receive_parity(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await globals()["receive_parity"](update, context)

    async def receive_bmi(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await globals()["receive_bmi"](update, context)

    async def receive_language(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await globals()["receive_language"](update, context)

    async def confirm_registration(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await globals()["confirm_registration"](update, context)

    async def cancel_registration(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await globals()["cancel_registration"](update, context)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await globals()["start"](update, context)

# Backward-compatibility alias for typo
MatruRakkshaBot = MatruRakshaBot

# === Minimal registration step handlers (module-level) ===
async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    context.user_data.setdefault('registration_data', {})
    context.user_data['registration_data']['name'] = None if text.lower() == "skip" else text
    await update.message.reply_text("Please enter your age (or type 'skip').")
    return AWAITING_AGE

async def receive_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    try:
        value = int(text)
    except Exception:
        value = None
    context.user_data.setdefault('registration_data', {})
    context.user_data['registration_data']['age'] = value
    await update.message.reply_text("Please enter your phone number (or type 'skip').")
    return AWAITING_PHONE

async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    value = None if text.lower() == "skip" else text
    context.user_data.setdefault('registration_data', {})
    context.user_data['registration_data']['phone'] = value
    await update.message.reply_text("Please enter your due date in YYYY-MM-DD (or 'skip').")
    return AWAITING_DUE_DATE

async def receive_due_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    value = None if text.lower() == "skip" else text
    context.user_data.setdefault('registration_data', {})
    context.user_data['registration_data']['due_date'] = value
    await update.message.reply_text("Please enter your city/location (or 'skip').")
    return AWAITING_LOCATION

async def receive_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    value = None if text.lower() == "skip" else text
    context.user_data.setdefault('registration_data', {})
    context.user_data['registration_data']['location'] = value
    await update.message.reply_text("Please enter gravida (number of pregnancies, or 'skip').")
    return AWAITING_GRAVIDA

async def receive_gravida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    try:
        value = int(text)
    except Exception:
        value = None
    context.user_data.setdefault('registration_data', {})
    context.user_data['registration_data']['gravida'] = value
    await update.message.reply_text("Please enter parity (number of births, or 'skip').")
    return AWAITING_PARITY

async def receive_parity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    try:
        value = int(text)
    except Exception:
        value = None
    context.user_data.setdefault('registration_data', {})
    context.user_data['registration_data']['parity'] = value
    await update.message.reply_text("Please enter BMI (e.g., 22.5, or 'skip').")
    return AWAITING_BMI

async def receive_bmi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    try:
        value = float(text)
    except Exception:
        value = None
    context.user_data.setdefault('registration_data', {})
    context.user_data['registration_data']['bmi'] = value
    # Prompt for language selection (text input acceptable)
    await update.message.reply_text("Choose your preferred language: English, Hindi, or Marathi. You can type the language name.")
    return AWAITING_LANGUAGE

# === Finalize registration and persist to Supabase ===
async def finalize_registration(target, context: ContextTypes.DEFAULT_TYPE):
    data = context.user_data.get('registration_data', {})
    chat_id = str(getattr(getattr(target, 'chat', None), 'id', '') or (getattr(getattr(target, 'from_user', None), 'id', '') or ''))

    payload = {
        "name": data.get("name") or "Unknown",
        "age": data.get("age"),
        "phone": data.get("phone") or "0000000000",
        "due_date": data.get("due_date"),
        "location": data.get("location"),
        "gravida": data.get("gravida"),
        "parity": data.get("parity"),
        "bmi": data.get("bmi"),
        "preferred_language": data.get("preferred_language") or "en",
        "telegram_chat_id": chat_id,
    }

    try:
        api_url = f"{BACKEND_API_BASE_URL}/mothers/register"
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json=payload) as resp:
                ok = resp.status in (200, 201)
                body = await resp.json(content_type=None)
                if ok and body.get("status") == "success":
                    saved = body.get("data") or {}
                    context.chat_data['registration_active'] = False
                    context.chat_data['agents_suspended'] = False
                    context.user_data.pop('registration_data', None)
                    await target.reply_text("✅ Registration saved! Loading your dashboard...")
                    mothers = await get_mothers_by_telegram_id(chat_id) if callable(get_mothers_by_telegram_id) else None
                    await send_home_dashboard(target, context, mother=saved, mothers=mothers, as_new_message=True)
                    return ConversationHandler.END
                else:
                    logger.warning(f"Backend register failed: status={resp.status} body={body}")
        try:
            res = supabase.table("mothers").insert(payload).execute()
            saved = res.data[0] if hasattr(res, 'data') and res.data else None
            context.chat_data['registration_active'] = False
            context.chat_data['agents_suspended'] = False
            context.user_data.pop('registration_data', None)
            await target.reply_text("✅ Registration saved! Loading your dashboard...")
            mothers = await get_mothers_by_telegram_id(chat_id) if callable(get_mothers_by_telegram_id) else None
            await send_home_dashboard(target, context, mother=saved, mothers=mothers, as_new_message=True)
        except Exception as db_exc:
            logger.error(f"Registration save failed via Supabase: {db_exc}", exc_info=True)
            await target.reply_text("⚠️ Could not save registration right now. Please try again later.")
        return ConversationHandler.END
    except Exception as exc:
        logger.error(f"Registration flow error: {exc}", exc_info=True)
        await target.reply_text("⚠️ Could not save registration right now. Please try again later.")
        return ConversationHandler.END

# === Confirm registration callback ===
async def confirm_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = (getattr(query, 'data', '') or '')
    action = data.split('_', 1)[1] if data.startswith('confirm_') else data
    target = query.message
    if action in ('yes','accept','ok','confirm','y'):
        await target.reply_text('Processing your registration...')
        return await finalize_registration(target, context)
    else:
        await target.reply_text('Registration not confirmed. You can update details or restart with /start.')
        return ConversationHandler.END

# === Cancel registration command ===
async def cancel_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Clear any in-progress registration data and end the conversation
    try:
        context.user_data.pop('registration_data', None)
    except Exception:
        pass
    context.chat_data['registration_active'] = False
    context.chat_data['agents_suspended'] = False
    await update.message.reply_text('Registration cancelled. You can start again anytime with /start.')
    return ConversationHandler.END

# === Minimal text handler to satisfy imports ===
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text.startswith("/"):
        return
    await update.message.reply_text("I’m here to help. Use the menu buttons or type /start.")
