from __future__ import annotations

import asyncio
import json
import logging
import uuid
from html import escape
from typing import Any

from telegram import CallbackQuery, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import settings, validate_settings
from db import AsyncSessionLocal, Product, Transaction, init_db
from h2h import H2HClient, H2HError
from keyboards import (
    categories_grid,
    confirm_keyboard,
    main_menu_keyboard,
    number_keyboard,
    operators_grid,
    variants_grid,
)
from services import (
    active_categories,
    get_product,
    get_session_state,
    latest_transactions,
    normalize_destination,
    operators_by_category,
    products_by_category_operator,
    products_by_variant,
    save_transaction,
    set_session_state,
    sync_products,
    variants_by_operator,
)

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
)
logger = logging.getLogger('h2hbot')


def rupiah(value: float | int | str | None) -> str:
    try:
        amount = int(float(value or 0))
    except (TypeError, ValueError):
        amount = 0
    return f"Rp {amount:,}".replace(',', '.')


async def owner_only(update: Update) -> bool:
    user = update.effective_user
    chat = update.effective_chat
    return bool(user and chat and chat.type == ChatType.PRIVATE and user.id in settings.owner_telegram_ids)


async def ensure_owner(update: Update) -> bool:
    ok = await owner_only(update)
    if not ok:
        if update.effective_message:
            await update.effective_message.reply_text('Bot ini private. Akses ditolak.')
    return ok


# ── Startup & scheduled jobs ──────────────────────────────────────────────────

async def startup_sync(context: ContextTypes.DEFAULT_TYPE) -> None:
    client: H2HClient = context.application.bot_data['h2h']
    async with AsyncSessionLocal() as db:
        try:
            count = await sync_products(db, client)
            logger.info('Initial sync selesai: %s produk', count)
        except Exception:
            logger.exception('Initial sync gagal')


async def scheduled_sync(context: ContextTypes.DEFAULT_TYPE) -> None:
    client: H2HClient = context.application.bot_data['h2h']
    async with AsyncSessionLocal() as db:
        try:
            count = await sync_products(db, client)
            logger.info('Scheduled sync selesai: %s produk', count)
        except Exception:
            logger.exception('Scheduled sync gagal')


async def pending_checker(context: ContextTypes.DEFAULT_TYPE) -> None:
    client: H2HClient = context.application.bot_data['h2h']
    async with AsyncSessionLocal() as db:
        from db import pending_transactions
        rows = await pending_transactions(db)
        for tx in rows:
            try:
                response = await client.check_status(tx.ref_id)
            except Exception as exc:
                logger.warning('Cek pending gagal %s: %s', tx.ref_id, exc)
                continue

            status = str(response.get('transaction_status', tx.status))
            sn = response.get('serial_number') or None
            changed = status != tx.status or (sn and sn != tx.serial_number)

            tx.status = status
            tx.status_label = response.get('status_label', tx.status_label)
            if sn:
                tx.serial_number = sn
            tx.price = int(response.get('price', tx.price) or tx.price)
            tx.raw_response = json.dumps(response, ensure_ascii=False)
            await db.commit()

            if changed and status.lower() not in ('pending', 'processing'):
                emoji = '✅' if status.lower() == 'success' else '❌'
                try:
                    await context.bot.send_message(
                        chat_id=tx.chat_id,
                        text=emoji + ' <b>Update Transaksi</b>\n\n' + render_transaction(tx, response),
                        parse_mode=ParseMode.HTML,
                    )
                except Exception as exc:
                    logger.warning('Gagal kirim update %s: %s', tx.ref_id, exc)

            await asyncio.sleep(0.3)


# ── Render helpers ────────────────────────────────────────────────────────────

def render_transaction(tx: Transaction, response: dict[str, Any]) -> str:
    status = str(response.get('transaction_status', tx.status))
    sn = str(response.get('serial_number', tx.serial_number) or '-')
    status_label = str(response.get('status_label', tx.status_label) or status)
    harga = rupiah(response.get('price', tx.price) or tx.price)
    STATUS_ICON = {'success': '✅', 'failed': '❌', 'pending': '⏳', 'processing': '🔄'}
    icon = STATUS_ICON.get(status.lower(), '❔')
    return (
        f'<blockquote>📦 <b>{escape(tx.product_name)}</b></blockquote>\n'
        f'<blockquote>'
        f'📱 Tujuan  : <b>{escape(tx.destination)}</b>\n'
        f'💰 Harga   : <b>{harga}</b>\n'
        f'{icon} Status  : <b>{escape(status_label)}</b>'
        f'</blockquote>\n'
        f'<blockquote>'
        f'🔑 SN/Ref  : <code>{escape(sn)}</code>\n'
        f'🆔 Ref ID  : <code>{escape(tx.ref_id)}</code>'
        f'</blockquote>'
    )


def render_confirmation(product: Product, destination: str) -> str:
    return (
        f'🛒 <b>Konfirmasi Transaksi</b>\n\n'
        f'<blockquote>'
        f'📦 Produk  : <b>{escape(product.product_name)}</b>\n'
        f'📱 Tujuan  : <b>{escape(destination)}</b>\n'
        f'💰 Harga   : <b>{rupiah(product.price)}</b>'
        f'</blockquote>\n\n'
        f'Lanjutkan pembelian?'
    )


async def show_products_text(
    update_or_query,
    products: list,
    title: str,
    back_callback: str = 'back:variants',
    is_callback: bool = True,
) -> None:
    if not products:
        text = f'{title}\n\nTidak ada produk tersedia.'
        kb = number_keyboard(0, back_callback=back_callback)
        if is_callback:
            await update_or_query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        else:
            await update_or_query.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    lines = [title, '']
    for i, p in enumerate(products, 1):
        stok = '✅' if p.status == 'OPEN' else '❌'
        lines.append(
            f'<blockquote>{i}. {stok} <b>{escape(p.product_name)}</b>\n'
            f'💰 Harga: <b>{rupiah(p.price)}</b></blockquote>'
        )
    lines.append('')
    lines.append('🛒 Klik nomor sesuai Produk yang ingin di beli:')

    kb = number_keyboard(len(products), back_callback=back_callback)
    msg_text = '\n'.join(lines)
    if is_callback:
        await update_or_query.message.edit_text(msg_text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update_or_query.reply_text(msg_text, parse_mode=ParseMode.HTML, reply_markup=kb)


# ── Command handlers ──────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_owner(update):
        return
    await send_home(update, context)


async def send_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    client: H2HClient = context.application.bot_data['h2h']
    async with AsyncSessionLocal() as db:
        cats = await active_categories(db)
        try:
            balance = await client.check_balance()
            balance_text = rupiah(balance)
        except Exception:
            balance_text = 'Gagal diambil'
        await set_session_state(
            db, update.effective_chat.id,
            current_category='', current_operator='',
            current_code='', current_destination='',
            last_action='home',
        )

    text = (
        f'🛍 <b>{escape(settings.app_name)}</b>\n'
        f'⚡ Server Otomatis\n'
        f'🔥 Online 24 Jam\n\n'
        f'💳 Saldo: <b>{balance_text}</b>\n'
        f'📦 Produk: <b>{len(cats)}</b>\n\n'
        + ('🧪 Mode testing aktif\n' if settings.transaction_testing_mode else '')
        + 'Pilih kategori:'
    )
    keyboard = categories_grid(cats, columns=2)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        await update.effective_message.reply_text(
            'Gunakan tombol bawah untuk Menu, Saldo, Sync, dan Riwayat.',
            reply_markup=main_menu_keyboard()
        )


async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_owner(update):
        return
    client: H2HClient = context.application.bot_data['h2h']
    try:
        balance = await client.check_balance()
        text = f'💳 Saldo H2H.id: <b>{rupiah(balance)}</b>'
    except Exception as exc:
        text = f'Gagal cek saldo: {escape(str(exc))}'
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_owner(update):
        return
    client: H2HClient = context.application.bot_data['h2h']
    msg = await update.effective_message.reply_text('Sedang sinkron produk dari H2H.id...')
    async with AsyncSessionLocal() as db:
        try:
            count = await sync_products(db, client)
            await msg.edit_text(f'✅ Sync selesai. Total produk: {count}')
        except Exception as exc:
            await msg.edit_text(f'❌ Sync gagal: {exc}')


async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_owner(update):
        return
    async with AsyncSessionLocal() as db:
        rows = await latest_transactions(db, update.effective_chat.id)
    if not rows:
        await update.effective_message.reply_text('Belum ada transaksi.')
        return
    STATUS_ICON = {'success': '✅', 'failed': '❌', 'pending': '⏳'}
    lines = ['📋 <b>Riwayat Transaksi</b>', '─' * 28]
    for i, tx in enumerate(rows, 1):
        icon = STATUS_ICON.get(tx.status.lower(), '❔')
        nama = escape(tx.product_name[:28] + '…' if len(tx.product_name) > 28 else tx.product_name)
        sn = escape(str(tx.serial_number or '-'))
        lines.append(
            f'{i}. {icon} <b>{nama}</b>\n'
            f'   📱 {escape(tx.destination)}  |  💰 {rupiah(tx.price)}\n'
            f'   🔑 <code>{sn}</code>'
        )
    await update.effective_message.reply_text('\n'.join(lines), parse_mode=ParseMode.HTML)


# ── Text router ───────────────────────────────────────────────────────────────

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_owner(update):
        return
    text = (update.effective_message.text or '').strip()

    if text in {'/start', '☰ Menu'}:
        await send_home(update, context)
        return
    if text in {'💳 Saldo', '/saldo'}:
        await show_balance(update, context)
        return
    if text in {'🔄 Sync Produk', '/sync'}:
        await sync_command(update, context)
        return
    if text in {'📜 Riwayat', '/history'}:
        await show_history(update, context)
        return

    async with AsyncSessionLocal() as db:
        state = await get_session_state(db, update.effective_chat.id)

        if state.last_action == 'waiting_product_number' and text.isdigit():
            await handle_product_number(update, context, state, int(text))
            return

        if state.last_action != 'waiting_destination' or not state.current_code:
            await update.effective_message.reply_text('Pilih menu dulu dari tombol yang tersedia.')
            return

        destination = normalize_destination(text)
        if len(destination) < 4:
            await update.effective_message.reply_text('Nomor tujuan terlalu pendek.')
            return

        await set_session_state(db, update.effective_chat.id, current_destination=destination, last_action='waiting_confirm')
        product = await get_product(db, state.current_code)

    if not product:
        await update.effective_message.reply_text('Produk tidak ditemukan. Silakan ulangi dari menu.')
        return

    await update.effective_message.reply_text(
        render_confirmation(product, destination),
        parse_mode=ParseMode.HTML,
        reply_markup=confirm_keyboard(product.code),
    )


# ── Product number & buy ──────────────────────────────────────────────────────

async def handle_product_number(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    state,
    number: int,
) -> None:
    async with AsyncSessionLocal() as db:
        current_variant = str(getattr(state, 'current_variant', '') or '')
        if current_variant:
            products = await products_by_variant(db, state.current_category, state.current_operator, current_variant)
        else:
            products = await products_by_category_operator(db, state.current_category, state.current_operator)

        if not products or number < 1 or number > len(products):
            await update.effective_message.reply_text(f'Nomor tidak valid. Pilih antara 1 - {len(products)}.')
            return

        product = products[number - 1]
        await set_session_state(
            db, update.effective_chat.id,
            current_code=product.code,
            current_destination='',
            last_action='waiting_destination',
        )

    await update.effective_message.reply_text(
        f'Kirim nomor HP / tujuan untuk produk:\n<b>{escape(product.product_name)}</b>',
        parse_mode=ParseMode.HTML,
    )


async def handle_picknum(query: CallbackQuery, update: Update, context: ContextTypes.DEFAULT_TYPE, number: int) -> None:
    async with AsyncSessionLocal() as db:
        state = await get_session_state(db, update.effective_chat.id)
        current_variant = str(getattr(state, 'current_variant', '') or '')
        if current_variant:
            products = await products_by_variant(db, state.current_category, state.current_operator, current_variant)
        else:
            products = await products_by_category_operator(db, state.current_category, state.current_operator)

        if not products or number < 1 or number > len(products):
            await query.answer(f'Nomor tidak valid! Pilih 1-{len(products)}', show_alert=True)
            return

        product = products[number - 1]
        await set_session_state(
            db, update.effective_chat.id,
            current_code=product.code,
            current_destination='',
            last_action='waiting_destination',
        )

    await query.answer()
    await query.message.reply_text(
        f'Kirim nomor HP / tujuan untuk produk:\n<b>{escape(product.product_name)}</b>',
        parse_mode=ParseMode.HTML,
    )


async def process_buy(query: CallbackQuery, update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    client: H2HClient = context.application.bot_data['h2h']
    async with AsyncSessionLocal() as db:
        state = await get_session_state(db, update.effective_chat.id)
        product = await get_product(db, code)
        destination = normalize_destination(state.current_destination or '')

    if not product or not destination:
        await query.answer('Tujuan belum diisi.', show_alert=True)
        return
    if product.status != 'OPEN':
        await query.answer('Produk sedang nonaktif.', show_alert=True)
        return

    ref_id = 'TRX' + uuid.uuid4().hex[:10].upper()
    await query.answer('Memproses...')
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    # Loading animation
    loading_msg = await query.message.reply_text('⏳ Memproses transaksi...', parse_mode=ParseMode.HTML)

    try:
        balance = await client.check_balance()
        if balance <= 0:
            await loading_msg.edit_text('Saldo H2H.id masih Rp 0. Isi deposit dulu.')
            return
        if balance < product.price:
            await loading_msg.edit_text(
                f'Saldo tidak cukup. Saldo: <b>{rupiah(balance)}</b>, harga: <b>{rupiah(product.price)}</b>.',
                parse_mode=ParseMode.HTML,
            )
            return

        response = await client.topup(product.code, destination, ref_id)

    except H2HError as exc:
        logger.error('H2H error ref=%s code=%s dest=%s error=%s', ref_id, code, destination, str(exc))
        await loading_msg.edit_text(
            f'Gagal ke H2H.id.\n\nError: <code>{escape(str(exc))}</code>\nRef ID: <code>{escape(ref_id)}</code>',
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception as exc:
        logger.exception('Transaksi gagal')
        await loading_msg.edit_text(f'Terjadi error: {escape(str(exc))}', parse_mode=ParseMode.HTML)
        return

    async with AsyncSessionLocal() as db:
        tx = await save_transaction(db, update.effective_chat.id, product, destination, response)
        await set_session_state(db, update.effective_chat.id, current_destination='', last_action='home')

    status_awal = str(response.get('transaction_status', 'pending'))
    await loading_msg.edit_text('⏳ Transaksi diproses:\n\n' + render_transaction(tx, response), parse_mode=ParseMode.HTML)

    # Polling untuk status pending
    if status_awal.lower() == 'pending':
        MAX_RETRY = 10
        DELAY = 0.5
        for attempt in range(MAX_RETRY):
            await asyncio.sleep(DELAY)
            try:
                updated = await client.check_status(ref_id)
            except Exception as exc:
                logger.warning('Polling gagal ref=%s: %s', ref_id, exc)
                break

            new_status = str(updated.get('transaction_status', 'pending'))
            sn = updated.get('serial_number') or None

            async with AsyncSessionLocal() as db2:
                from sqlalchemy import select as _sel
                _res = await db2.execute(_sel(Transaction).where(Transaction.ref_id == tx.ref_id))
                tx_db = _res.scalar_one_or_none()
                if tx_db:
                    if tx_db.status.lower() in {'success', 'failed'}:
                        break
                    tx_db.status = new_status
                    tx_db.status_label = updated.get('status_label', tx_db.status_label)
                    if sn:
                        tx_db.serial_number = sn
                    tx_db.price = int(updated.get('price', tx_db.price) or tx_db.price)
                    tx_db.raw_response = json.dumps(updated, ensure_ascii=False)
                    await db2.commit()
                    await db2.refresh(tx_db)
                    tx = tx_db

            if new_status.lower() not in ('pending', 'processing'):
                emoji = '✅' if new_status.lower() == 'success' else '❌'
                try:
                    await query.message.reply_text(
                        emoji + ' <b>Transaksi ' + str(updated.get('status_label', new_status)) + '!</b>\n\n' + render_transaction(tx, updated),
                        parse_mode=ParseMode.HTML,
                    )
                except Exception as exc:
                    logger.warning('Gagal kirim notif sukses: %s', exc)
                break


# ── Callback & navigation ─────────────────────────────────────────────────────

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_owner(update):
        return
    query = update.callback_query
    data = query.data or ''

    if data == 'noop':
        await query.answer()
        return
    if data.startswith('cat:'):
        await handle_category(query, update, data.split(':', 1)[1])
        return
    if data.startswith('op:'):
        parts = data.split(':', 1)[1].split('|', 1)
        if len(parts) == 2:
            await handle_operator(query, update, parts[0], parts[1])
        return
    if data.startswith('variant:'):
        parts = data.split(':', 1)[1].split('|', 2)
        if len(parts) == 3:
            await handle_variant(query, update, parts[0], parts[1], parts[2])
        return
    if data.startswith('picknum:'):
        num = int(data.split(':', 1)[1])
        await handle_picknum(query, update, context, num)
        return
    if data.startswith('buy:'):
        await ask_destination(query, update, data.split(':', 1)[1])
        return
    if data.startswith('confirm:'):
        await process_buy(query, update, context, data.split(':', 1)[1])
        return
    if data.startswith('back:'):
        await handle_back(query, update, context, data.split(':', 1)[1])
        return
    await query.answer('Aksi tidak dikenali.', show_alert=True)


async def handle_category(query: CallbackQuery, update: Update, category: str) -> None:
    async with AsyncSessionLocal() as db:
        operators = await operators_by_category(db, category)
        await set_session_state(db, update.effective_chat.id, current_category=category, current_operator='', current_code='', current_destination='', last_action='operators')
    await query.answer()
    await query.message.edit_text(
        f'📂 <b>{escape(category)}</b>\nSilakan pilih operator / provider:',
        parse_mode=ParseMode.HTML,
        reply_markup=operators_grid(category, operators, columns=2),
    )


async def handle_operator(query: CallbackQuery, update: Update, category: str, operator: str) -> None:
    async with AsyncSessionLocal() as db:
        variants = await variants_by_operator(db, category, operator)
        await set_session_state(db, update.effective_chat.id, current_category=category, current_operator=operator, current_code='', current_destination='', current_variant='', last_action='waiting_product_number')

    await query.answer()
    if variants:
        await query.message.edit_text(
            f'📦 <b>{escape(operator)}</b> - {escape(category)}\nSilakan Pilih Varian:',
            parse_mode=ParseMode.HTML,
            reply_markup=variants_grid(category, operator, variants, columns=2),
        )
    else:
        async with AsyncSessionLocal() as db:
            products = await products_by_category_operator(db, category, operator)
        title = f'📦 <b>{escape(operator)}</b> - {escape(category)}'
        await show_products_text(query, products, title, back_callback='back:operators', is_callback=True)


async def handle_variant(query: CallbackQuery, update: Update, category: str, operator: str, variant: str) -> None:
    async with AsyncSessionLocal() as db:
        products = await products_by_variant(db, category, operator, variant)
        await set_session_state(
            db, update.effective_chat.id,
            current_category=category, current_operator=operator,
            current_code='', current_destination='',
            current_variant=variant, last_action='waiting_product_number',
        )
    await query.answer()
    title = f'📦 <b>{escape(operator)}</b> - {escape(category)}\nKategori: {escape(variant)}'
    await show_products_text(query, products, title, back_callback='back:variants', is_callback=True)


async def ask_destination(query: CallbackQuery, update: Update, code: str) -> None:
    async with AsyncSessionLocal() as db:
        product = await get_product(db, code)
        await set_session_state(db, update.effective_chat.id, current_code=code, current_destination='', last_action='waiting_destination')
    if not product:
        await query.answer('Produk tidak ditemukan.', show_alert=True)
        return
    await query.answer()
    await query.message.reply_text(
        f'Kirim nomor HP / tujuan untuk produk:\n<b>{escape(product.product_name)}</b>',
        parse_mode=ParseMode.HTML,
    )


async def handle_back(query: CallbackQuery, update: Update, context: ContextTypes.DEFAULT_TYPE, target: str) -> None:
    if target == 'home':
        await send_home(update, context)
        return
    async with AsyncSessionLocal() as db:
        state = await get_session_state(db, update.effective_chat.id)
        if target == 'categories':
            cats = await active_categories(db)
            await query.answer()
            await query.message.edit_text('Pilih kategori:', reply_markup=categories_grid(cats, columns=2))
            return
        if target == 'operators':
            operators = await operators_by_category(db, state.current_category)
            await query.answer()
            await query.message.edit_text(
                f'📂 <b>{escape(state.current_category)}</b>\nSilakan pilih operator:',
                parse_mode=ParseMode.HTML,
                reply_markup=operators_grid(state.current_category, operators, columns=2),
            )
            return
        if target == 'variants':
            variants = await variants_by_operator(db, state.current_category, state.current_operator)
            await query.answer()
            if variants:
                await query.message.edit_text(
                    f'📦 <b>{escape(state.current_operator)}</b> - {escape(state.current_category)}\nSilakan Pilih Varian:',
                    parse_mode=ParseMode.HTML,
                    reply_markup=variants_grid(state.current_category, state.current_operator, variants, columns=2),
                )
            else:
                products = await products_by_category_operator(db, state.current_category, state.current_operator)
                title = f'📦 <b>{escape(state.current_operator)}</b> - {escape(state.current_category)}'
                await show_products_text(query, products, title, back_callback='back:operators', is_callback=True)
            return
    await query.answer()


# ── Webhook server ────────────────────────────────────────────────────────────

async def h2h_webhook_handler(request) -> None:
    from aiohttp import web as _web
    import os as _os

    body = await request.read()
    try:
        data = await request.json()
    except Exception:
        return _web.Response(status=400, text='Bad Request')

    ref_id = str(data.get('ref_id') or '')
    status = str(data.get('transaction_status') or '')
    sn = data.get('serial_number') or None

    if not ref_id:
        return _web.Response(status=200, text='OK')

    logger.info('Webhook H2H diterima ref_id=%s status=%s', ref_id, status)

    application = request.app['bot_application']
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select as _sel
        result = await db.execute(_sel(Transaction).where(Transaction.ref_id == ref_id))
        tx = result.scalar_one_or_none()
        if not tx:
            return _web.Response(status=200, text='OK')

        old_status = tx.status
        tx.status = status
        tx.status_label = data.get('status_label', tx.status_label)
        if sn:
            tx.serial_number = sn
        tx.price = int(data.get('price', tx.price) or tx.price)
        tx.raw_response = json.dumps(data, ensure_ascii=False)
        await db.commit()
        await db.refresh(tx)

    if status.lower() != old_status.lower() and status.lower() not in ('pending', 'processing'):
        emoji = '✅' if status.lower() == 'success' else '❌'
        try:
            await application.bot.send_message(
                chat_id=tx.chat_id,
                text=emoji + ' <b>Update Transaksi</b>\n\n' + render_transaction(tx, data),
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            logger.warning('Gagal kirim notif webhook %s: %s', ref_id, exc)

    return _web.Response(status=200, text='OK')


async def start_webhook_server(application: Application, port: int = 8080) -> None:
    from aiohttp import web as _web
    web_app = _web.Application()
    web_app['bot_application'] = application
    web_app.router.add_post('/webhook/h2h', h2h_webhook_handler)
    runner = _web.AppRunner(web_app)
    await runner.setup()
    site = _web.TCPSite(runner, '127.0.0.1', port)
    await site.start()
    logger.info('Webhook server berjalan di http://127.0.0.1:%s/webhook/h2h', port)


# ── Error handler ─────────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    from telegram.error import NetworkError, TimedOut, RetryAfter
    err = context.error
    ignored = (asyncio.CancelledError, NetworkError, TimedOut, RetryAfter)
    if isinstance(err, ignored):
        logger.warning('Error non-fatal diabaikan: %s', err)
        return
    logger.exception('Unhandled error', exc_info=err)
    target = None
    if isinstance(update, Update):
        target = update.effective_message
    if target:
        try:
            await target.reply_text('Terjadi error internal. Coba ulang beberapa saat lagi.')
        except Exception:
            logger.exception('Gagal kirim pesan error')


# ── Build & run ───────────────────────────────────────────────────────────────

def build_app() -> Application:
    validate_settings()
    application = Application.builder().token(settings.telegram_bot_token).build()
    application.bot_data['h2h'] = H2HClient.from_settings()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('saldo', show_balance))
    application.add_handler(CommandHandler('sync', sync_command))
    application.add_handler(CommandHandler('history', show_history))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    application.add_error_handler(error_handler)

    jq = application.job_queue
    if jq is not None:
        jq.run_once(startup_sync, when=3)
        jq.run_repeating(scheduled_sync, interval=settings.product_refresh_minutes * 60, first=120)
        jq.run_repeating(pending_checker, interval=settings.pending_check_minutes * 60, first=180)
    return application


def main() -> None:
    validate_settings()

    async def _run() -> None:
        await init_db()
        application = build_app()
        await application.initialize()
        await start_webhook_server(application, port=8080)
        await application.start()
        logger.info('Bot H2H dan webhook server berjalan...')
        await application.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )
        try:
            await asyncio.Event().wait()
        finally:
            await application.updater.stop()
            await application.stop()
            await application.shutdown()

    asyncio.run(_run())


if __name__ == '__main__':
    main()
