from __future__ import annotations

from math import ceil
from typing import Iterable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

_MAX_NAME = 32


def _trim(text: str, max_len: int = _MAX_NAME) -> str:
    text = text.strip()
    return text if len(text) <= max_len else text[:max_len - 1] + '…'


def chunked(items: list, per_row: int) -> list[list]:
    return [items[i:i + per_row] for i in range(0, len(items), per_row)]


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton('☰ Menu'), KeyboardButton('💳 Saldo')],
        [KeyboardButton('🔄 Sync Produk'), KeyboardButton('📜 Riwayat')],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def categories_grid(categories: Iterable[str], columns: int = 2) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=_trim(cat, 24), callback_data=f'cat:{cat}')
        for cat in categories
    ]
    return InlineKeyboardMarkup(chunked(buttons, columns))


def operators_grid(category: str, operators: Iterable[str], columns: int = 2) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=_trim(op, 24), callback_data=f'op:{category}|{op}')
        for op in operators
    ]
    back_btn = InlineKeyboardButton(text='⬅ Kembali', callback_data='back:categories')
    return InlineKeyboardMarkup(chunked(buttons, columns) + [[back_btn]])


def variants_grid(category: str, operator: str, variants: Iterable[str], columns: int = 2) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=_trim(v, 24), callback_data=f'variant:{category}|{operator}|{v}')
        for v in variants
    ]
    back_btn = InlineKeyboardButton(text='⬅ Kembali', callback_data='back:operators')
    return InlineKeyboardMarkup(chunked(buttons, columns) + [[back_btn]])


def number_keyboard(count: int, back_callback: str = 'back:variants') -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(str(i), callback_data=f'picknum:{i}')
        for i in range(1, count + 1)
    ]
    rows = chunked(buttons, 5)
    rows.append([
        InlineKeyboardButton('⬅ Kembali', callback_data=back_callback),
        InlineKeyboardButton('🏠 Menu', callback_data='back:home'),
    ])
    return InlineKeyboardMarkup(rows)


def product_action_keyboard(code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🛒 Beli Sekarang', callback_data=f'buy:{code}')],
        [
            InlineKeyboardButton('⬅ Kembali', callback_data='back:products'),
            InlineKeyboardButton('🏠 Menu', callback_data='back:home'),
        ],
    ])


def confirm_keyboard(code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ Konfirmasi', callback_data=f'confirm:{code}')],
        [InlineKeyboardButton('✖ Batal', callback_data='back:home')],
    ])
