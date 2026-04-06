import html
import json
import logging
from typing import Any, TYPE_CHECKING

from aiogram import F
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from clankandclaw.telegram.formatters import _fmt_dashboard_header, _fmt_inline_code, _fmt_text
from clankandclaw.utils.llm import suggest_token_metadata, suggest_token_description

if TYPE_CHECKING:
    from clankandclaw.telegram.bot import TelegramBot

logger = logging.getLogger(__name__)

class ManualDeployStates(StatesGroup):
    """FSM States for manual deployment wizard."""
    platform = State()
    name = State()
    symbol = State()
    image = State()
    description = State()
    confirm = State()

class WizardHandler:
    """Encapsulates manual deployment wizard logic."""
    
    def __init__(self, bot: "TelegramBot"):
        self.bot = bot
        self._db = bot._db
        
    def register_handlers(self, dp):
        """Register wizard handlers with the dispatcher."""
        dp.callback_query.register(self._handle_nav_wizard, F.data == "nav_wizard")
        dp.callback_query.register(self._handle_wizard_platform, ManualDeployStates.platform, F.data.startswith("wiz_plat:"))
        dp.message.register(self._handle_wizard_name, ManualDeployStates.name)
        dp.message.register(self._handle_wizard_symbol, ManualDeployStates.symbol)
        dp.message.register(self._handle_wizard_image, ManualDeployStates.image)
        dp.callback_query.register(self._handle_wizard_image_auto, ManualDeployStates.image, F.data == "wiz_img:auto")
        dp.message.register(self._handle_wizard_description, ManualDeployStates.description)
        dp.callback_query.register(self._handle_wizard_description_skip, ManualDeployStates.description, F.data == "wiz_desc:skip")
        dp.callback_query.register(self._handle_wizard_confirm, ManualDeployStates.confirm, F.data == "wiz_confirm")
        
        # Entry points & navigation
        dp.callback_query.register(self._handle_wizard_edit, F.data.startswith("wiz_edit:"))
        dp.callback_query.register(self._handle_wizard_back, F.data == "wiz_back")
        dp.callback_query.register(self._handle_wizard_suggest, F.data == "wiz_suggest")
        dp.callback_query.register(self._handle_wizard_desc_suggest, F.data == "wiz_desc_suggest")
        dp.callback_query.register(self._handle_wizard_apply_suggest, F.data.startswith("wiz_apply_suggest:"))
        
        # Generic cancel
        dp.callback_query.register(self._handle_wizard_cancel, F.data == "wiz_cancel")
        dp.callback_query.register(self._handle_wizard_cancel, ManualDeployStates.platform, F.data == "wiz_cancel")
        dp.callback_query.register(self._handle_wizard_cancel, ManualDeployStates.name, F.data == "wiz_cancel")
        dp.callback_query.register(self._handle_wizard_cancel, ManualDeployStates.symbol, F.data == "wiz_cancel")
        dp.callback_query.register(self._handle_wizard_cancel, ManualDeployStates.image, F.data == "wiz_cancel")
        dp.callback_query.register(self._handle_wizard_cancel, ManualDeployStates.description, F.data == "wiz_cancel")
        dp.callback_query.register(self._handle_wizard_cancel, ManualDeployStates.confirm, F.data == "wiz_cancel")

    def _render_wizard_view(self, state_name: str, data: dict[str, Any]) -> tuple[str, InlineKeyboardMarkup | None]:
        """Unified UI renderer for the Wizard."""
        steps = [
            ("Platform", ManualDeployStates.platform),
            ("Identity", ManualDeployStates.name),
            ("Symbol", ManualDeployStates.symbol),
            ("Visuals", ManualDeployStates.image),
            ("Metadata", ManualDeployStates.description),
            ("Confirm", ManualDeployStates.confirm)
        ]
        
        current_idx = 0
        for i, (name, st) in enumerate(steps):
            if state_name == st:
                current_idx = i
                break
        
        # Progress Bar
        bar = "".join(["◈" if i <= current_idx else "◇" for i in range(len(steps))])
        header = f"<b>{steps[current_idx][0]}</b> (Step {current_idx+1}/{len(steps)})\n<code>{bar}</code>\n\n"
        
        # Current Setup Summary
        summary = "<b>Current Setup</b>\n"
        summary += f"• Platform: <code>{(data.get('platform') or '---').upper()}</code>\n"
        short_name = _fmt_text(data.get('name') or '---')
        summary += f"• Name: <b>{short_name}</b>\n"
        summary += f"• Symbol: <code>{data.get('symbol') or '---'}</code>\n"
        
        img_val = data.get('image', '---')
        if img_val and img_val != '---':
            img_disp = "🪄 AUTO" if img_val == 'auto' else "🔗 URL"
        else:
            img_disp = "---"
        summary += f"• Image: {img_disp}\n\n"
        
        # Instruction logic
        instr = ""
        if state_name == ManualDeployStates.platform:
            instr = "Select deployment platform:"
        elif state_name == ManualDeployStates.name:
            instr = "Please type the <b>Token Name</b>:"
        elif state_name == ManualDeployStates.symbol:
            instr = "Please type the <b>Token Symbol</b>:"
        elif state_name == ManualDeployStates.image:
            instr = "Send an <b>Image URL</b>, upload a <b>Photo</b>, or use <b>AI</b>:"
        elif state_name == ManualDeployStates.description:
            instr = "Type a <b>Token Description</b> (optional):"
        elif state_name == ManualDeployStates.confirm:
            instr = "Verify setup and launch?"
            
        text = _fmt_dashboard_header("Manual Deploy", "🧪") + header + summary + "<b>Next:</b> " + instr
        return text, None

    async def _handle_nav_wizard(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Entry point for Manual Deployment Wizard. Always starts with a blank slate."""
        if not callback.message:
            return
            
        # CLEAR STATE for fresh launch
        current_data = await state.get_data()
        if not current_data.get("candidate_id"):
             await state.clear()
             
        await state.set_state(ManualDeployStates.platform)
        data = await state.get_data()
        text, _ = self._render_wizard_view(ManualDeployStates.platform, data)
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔵 Clanker (Base)", callback_data="wiz_plat:clanker"),
                    InlineKeyboardButton(text="❌ Cancel", callback_data="wiz_cancel")
                ]
            ])
        )

    async def _handle_wizard_edit(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Start Wizard pre-filled from an existing candidate."""
        if not callback.data or not callback.message:
            return
        encoded_id = callback.data.split(":", 1)[1]
        candidate_id = self.bot._decode_callback_candidate_id(encoded_id)
        
        candidate = self._db.get_candidate(candidate_id)
        if not candidate:
             await callback.answer("Candidate not found", show_alert=True)
             return
             
        metadata = {}
        try:
            metadata = json.loads(candidate["metadata_json"] or "{}")
        except: pass
        
        name_val = candidate.get("suggested_name") or metadata.get("suggested_name") or ""
        symbol_val = candidate.get("suggested_symbol") or metadata.get("suggested_symbol") or ""
        
        await state.clear()
        await state.update_data(
            platform="clanker", # Default
            name=name_val,
            symbol=symbol_val,
            image=metadata.get("image_url") or metadata.get("ipfs_image_uri") or "auto",
            description=metadata.get("ai_description") or "",
            candidate_id=candidate_id
        )
        
        await state.set_state(ManualDeployStates.platform)
        # Directly move to name step (skip platform since we know it)
        await self._show_wizard_name_step(callback, state)
        await callback.answer(f"Editing: {symbol_val or 'New Token'}")

    async def _handle_wizard_back(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Navigate back to previous step in the wizard."""
        if not callback.message:
             return
        current_state = await state.get_state()
        
        if current_state == ManualDeployStates.name:
            await state.set_state(ManualDeployStates.platform)
            await self._handle_nav_wizard(callback, state)
        elif current_state == ManualDeployStates.symbol:
            await state.set_state(ManualDeployStates.name)
            await self._show_wizard_name_step(callback, state)
        elif current_state == ManualDeployStates.image:
            await state.set_state(ManualDeployStates.symbol)
            data = await state.get_data()
            await callback.message.edit_text(
                _fmt_dashboard_header("Token Identity", "🧪") +
                f"• <b>Platform:</b> {data['platform'].upper()}\n"
                f"• <b>Name:</b> {html.escape(data['name'])}\n\n"
                "Please type the <b>Token Symbol</b>:",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="↩️ Back", callback_data="wiz_back")],
                    [InlineKeyboardButton(text="❌ Cancel", callback_data="wiz_cancel")]
                ])
            )
        elif current_state == ManualDeployStates.description:
            await state.set_state(ManualDeployStates.image)
            data = await state.get_data()
            await callback.message.edit_text(
                _fmt_dashboard_header("Visuals", "🧪") +
                f"• <b>Platform:</b> {data['platform'].upper()}\n"
                f"• <b>Name:</b> {html.escape(data['name'])}\n"
                f"• <b>Symbol:</b> {data['symbol']}\n\n"
                "Please send an <b>Image URL</b> or <b>Photo</b>:",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🪄 Auto (AI Image)", callback_data="wiz_img:auto")],
                    [InlineKeyboardButton(text="↩️ Back", callback_data="wiz_back")],
                    [InlineKeyboardButton(text="❌ Cancel", callback_data="wiz_cancel")]
                ])
            )
        elif current_state == ManualDeployStates.confirm:
            await state.set_state(ManualDeployStates.description)
            await self._show_wizard_desc_step(callback, state)
        await callback.answer()

    async def _handle_wizard_suggest(self, callback: CallbackQuery, state: FSMContext) -> None:
        """AI Suggestion for Name or Symbol."""
        if not callback.message:
             return
        data = await state.get_data()
        theme = data.get("name") or data.get("theme") or "trending base meme"
        
        # Add salt for re-rolls
        roll_count = data.get("roll_count", 0) + 1
        await state.update_data(roll_count=roll_count)
        if roll_count > 1:
            theme = f"{theme} (variety iteration {roll_count})"

        await callback.answer("🪄 AI Thinking...", show_alert=False)
        
        suggestions = await suggest_token_metadata(theme)
        if not suggestions:
             await callback.answer("AI failed to suggest. Try again.", show_alert=True)
             return
             
        keyboard = []
        for s in suggestions:
            label = f"{s['name']} ({s['symbol']})"
            keyboard.append([InlineKeyboardButton(text=label, callback_data=f"wiz_apply_suggest:{s['name']}:{s['symbol']}")])
        
        keyboard.append([InlineKeyboardButton(text="🔄 Re-roll Ideas", callback_data="wiz_suggest")])
        keyboard.append([InlineKeyboardButton(text="↩️ Back", callback_data="wiz_back")])
        
        text, _ = self._render_wizard_view(await state.get_state(), data)
        await callback.message.edit_text(
            text=text + "\n\n<b>AI Suggestions:</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
        )

    async def _handle_wizard_apply_suggest(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Apply the AI suggestion and move to next step."""
        if not callback.data or not callback.message:
            return
        parts = callback.data.split(":", 2)
        if len(parts) < 3:
             return
        name, symbol = parts[1], parts[2]
        await state.update_data(name=name, symbol=symbol)
        
        await state.set_state(ManualDeployStates.image)
        data = await state.get_data()
        await callback.message.edit_text(
            _fmt_dashboard_header("Visuals", "🧪") +
            f"• <b>Name:</b> {html.escape(name)}\n"
            f"• <b>Symbol:</b> {symbol}\n\n"
            "Please send an <b>Image URL</b> or <b>Photo</b>:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🪄 Auto (AI Image)", callback_data="wiz_img:auto")],
                [InlineKeyboardButton(text="↩️ Back", callback_data="wiz_back")],
                [InlineKeyboardButton(text="❌ Cancel", callback_data="wiz_cancel")]
            ])
        )
        await callback.answer(f"Applied: {symbol}")

    async def _handle_wizard_platform(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Wizard Step 1: Platform Selection."""
        if not callback.message or not callback.data:
            return
        platform = callback.data.split(":")[1]
        await state.update_data(platform=platform)
        
        data = await state.get_data()
        await state.set_state(ManualDeployStates.name)
        text, _ = self._render_wizard_view(ManualDeployStates.name, data)
        
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🪄 AI Suggest Name", callback_data="wiz_suggest")],
                [
                    InlineKeyboardButton(text="↩️ Back", callback_data="wiz_back"),
                    InlineKeyboardButton(text="❌ Cancel", callback_data="wiz_cancel")
                ]
            ]
        )
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        await callback.answer()

    async def _handle_wizard_name(self, message: Message, state: FSMContext) -> None:
        """Wizard Step 2: Capture Token Name."""
        name = (message.text or "").strip()
        if not name:
             await message.answer("⚠️ No text found. Please type the <b>Token Name</b>:", parse_mode="HTML")
             return
        if len(name) < 2:
            await message.answer("⚠️ Name too short. Try again:", parse_mode="HTML")
            return
            
        await state.update_data(name=name)
        await state.set_state(ManualDeployStates.symbol)
        await self._show_wizard_symbol_step(message, state)

    async def _show_wizard_name_step(self, message: Message | CallbackQuery, state: FSMContext) -> None:
        """Render the Name step UI."""
        data = await state.get_data()
        text, _ = self._render_wizard_view(ManualDeployStates.name, data)

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🪄 AI Suggest Name", callback_data="wiz_suggest")],
                [
                    InlineKeyboardButton(text="↩️ Back", callback_data="wiz_back"),
                    InlineKeyboardButton(text="❌ Cancel", callback_data="wiz_cancel")
                ]
            ]
        )
        
        if isinstance(message, CallbackQuery):
            await message.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        else:
            await message.answer(text, parse_mode="HTML", reply_markup=markup)

    async def _show_wizard_symbol_step(self, message: Message | CallbackQuery, state: FSMContext) -> None:
        """Render the Symbol step UI."""
        data = await state.get_data()
        text, _ = self._render_wizard_view(ManualDeployStates.symbol, data)

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🪄 AI Suggest Symbol", callback_data="wiz_suggest")],
                [
                    InlineKeyboardButton(text="↩️ Back", callback_data="wiz_back"),
                    InlineKeyboardButton(text="❌ Cancel", callback_data="wiz_cancel")
                ]
            ]
        )
        
        if isinstance(message, CallbackQuery):
             await message.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        else:
             await message.answer(text, parse_mode="HTML", reply_markup=markup)

    async def _show_wizard_image_step(self, message: Message | CallbackQuery, state: FSMContext) -> None:
        """Render the Image step UI."""
        data = await state.get_data()
        text, _ = self._render_wizard_view(ManualDeployStates.image, data)
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🪄 Auto (AI Image)", callback_data="wiz_img:auto")],
                [
                    InlineKeyboardButton(text="↩️ Back", callback_data="wiz_back"),
                    InlineKeyboardButton(text="❌ Cancel", callback_data="wiz_cancel")
                ]
            ]
        )
        if isinstance(message, CallbackQuery):
             await message.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        else:
             await message.answer(text, parse_mode="HTML", reply_markup=markup)

    async def _show_wizard_desc_step(self, message: Message | CallbackQuery, state: FSMContext) -> None:
        """Render the Description step UI."""
        data = await state.get_data()
        text, _ = self._render_wizard_view(ManualDeployStates.description, data)
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🪄 AI Generate Desc", callback_data="wiz_desc_suggest")],
                [InlineKeyboardButton(text="⏭ Skip", callback_data="wiz_desc:skip")],
                [
                    InlineKeyboardButton(text="↩️ Back", callback_data="wiz_back"),
                    InlineKeyboardButton(text="❌ Cancel", callback_data="wiz_cancel")
                ]
            ]
        )
        if isinstance(message, CallbackQuery):
             await message.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        else:
             await message.answer(text, parse_mode="HTML", reply_markup=markup)

    async def _handle_wizard_symbol(self, message: Message, state: FSMContext) -> None:
        """Wizard Step 3: Capture Token Symbol."""
        symbol = (message.text or "").strip().upper()
        if not symbol:
             await message.answer("⚠️ No text found. Please type the <b>Token Symbol</b>:", parse_mode="HTML")
             return
        if len(symbol) < 2:
            await message.answer("⚠️ Symbol too short. Try again:", parse_mode="HTML")
            return
            
        await state.update_data(symbol=symbol)
        await state.set_state(ManualDeployStates.image)
        await self._show_wizard_image_step(message, state)

    async def _handle_wizard_image(self, message: Message, state: FSMContext) -> None:
        """Wizard Step 4: Capture Token Image (URL or Photo upload)."""
        image_ref = ""
        
        if message.photo:
             await message.answer("⏳ Processing photo upload to IPFS...", parse_mode="HTML")
             try:
                 photo = message.photo[-1]
                 from io import BytesIO
                 file_info = await self.bot.bot.get_file(photo.file_id)
                 downloaded = await self.bot.bot.download_file(file_info.file_path, BytesIO())
                 
                 if hasattr(self.bot, "_pinata") and self.bot._pinata:
                      ipfs_hash = await self.bot._pinata.upload_file_bytes(
                          f"manual_{photo.file_id}.jpg", 
                          downloaded.getvalue(),
                          "image/jpeg"
                      )
                      image_ref = f"ipfs://{ipfs_hash}"
                 else:
                      await message.answer("⚠️ IPFS uploader not configured. Use URL instead.", parse_mode="HTML")
                      return
             except Exception as exc:
                 logger.error(f"Manual photo upload failed: {exc}")
                 await message.answer(f"❌ Upload failed: {exc}", parse_mode="HTML")
                 return
        else:
             image_ref = (message.text or "").strip()

        if not image_ref:
            await message.answer("⚠️ Please provide an image. Try again:", parse_mode="HTML")
            return
            
        await state.update_data(image=image_ref)
        await state.set_state(ManualDeployStates.description)
        await self._show_wizard_desc_step(message, state)

    async def _handle_wizard_image_auto(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Wizard Step 4: Set Image to 'auto'."""
        if not callback.message:
             return
        await state.update_data(image="auto")
        await state.set_state(ManualDeployStates.description)
        await self._show_wizard_desc_step(callback, state)
        await callback.answer("Image set to AUTO")

    async def _handle_wizard_desc_suggest(self, callback: CallbackQuery, state: FSMContext) -> None:
        """AI Suggestion for Description."""
        if not callback.message:
             return
        data = await state.get_data()
        await callback.answer("🪄 AI Writing...", show_alert=False)
        
        desc = await suggest_token_description(
            name=data.get("name", "Unknown"),
            symbol=data.get("symbol", "TKN"),
            theme=data.get("name", "")
        )
        if not desc:
             await callback.answer("AI failed to write. Try again.", show_alert=True)
             return
             
        await state.update_data(description=desc)
        await state.set_state(ManualDeployStates.confirm)
        await self._show_wizard_preview(callback.message, state)
        await callback.answer("Description Generated!")

    async def _handle_wizard_description_skip(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Wizard Step 5 Helper: Skip Description."""
        if not callback.message:
            return
        await state.update_data(description="")
        await state.set_state(ManualDeployStates.confirm)
        await self._show_wizard_preview(callback.message, state)
        await callback.answer()

    async def _handle_wizard_description(self, message: Message, state: FSMContext) -> None:
        """Wizard Step 5: Capture Description."""
        description = (message.text or "").strip()
        await state.update_data(description=description)
        await state.set_state(ManualDeployStates.confirm)
        await self._show_wizard_preview(message, state)

    async def _show_wizard_preview(self, message: Message | CallbackQuery, state: FSMContext) -> None:
        """Final Wizard Preview Card."""
        data = await state.get_data()
        text, _ = self._render_wizard_view(ManualDeployStates.confirm, data)
        
        # Add a more detailed description preview if available
        if data.get('description'):
            text += f"\n\n<b>Description:</b>\n{_fmt_text(data['description'])}"

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🚀 Launch Deployment", callback_data="wiz_confirm")],
                [
                    InlineKeyboardButton(text="↩️ Back", callback_data="wiz_back"),
                    InlineKeyboardButton(text="❌ Cancel Setup", callback_data="wiz_cancel")
                ]
            ]
        )
        if isinstance(message, CallbackQuery):
             await message.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        else:
             await message.answer(text, parse_mode="HTML", reply_markup=markup)

    async def _handle_wizard_confirm(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Final Wizard Trigger: Execute Deployment."""
        if not callback.message:
             return
        data = await state.get_data()
        await state.clear()
        
        await callback.message.edit_text(
            _fmt_dashboard_header("Deployment Started", "⌛") +
            f"Manual deployment for <b>{data['name']}</b> has been triggered.\n"
            "Check <b>cnc-deploy</b> for logs.",
            parse_mode="HTML",
        )
        
        if self.bot.on_manual_deploy:
            try:
                await self.bot.on_manual_deploy(
                    data["platform"],
                    data["name"],
                    data["symbol"],
                    data["image"],
                    data["description"],
                    {
                        "chat_id": callback.message.chat.id,
                        "user_id": getattr(callback.from_user, "id", None),
                    }
                )
            except Exception as exc:
                logger.error(f"Wizard deploy error: {exc}", exc_info=True)
                await callback.message.answer(
                    _fmt_dashboard_header("Deployment Failed", "❌") + 
                    f"Reason: {_fmt_text(str(exc))}",
                    parse_mode="HTML",
                    reply_markup=self.bot._ui_dashboard_keyboard()
                )
        await callback.answer()

    async def _handle_wizard_cancel(self, callback: CallbackQuery, state: FSMContext) -> None:
        """Wizard Cancel Handler: Reset FSM and show Dashboard."""
        if not callback.message:
            return
        await state.clear()
        await callback.message.edit_text(
            _fmt_dashboard_header("Setup Cancelled", "🚫") +
            "Manual deployment wizard was dismissed.",
            parse_mode="HTML",
            reply_markup=self.bot._ui_dashboard_keyboard()
        )
        await callback.answer("Cancelled")
