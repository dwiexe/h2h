from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db import Product, Transaction, UserSession, get_or_create_session
from h2h import H2HClient

# ── Pengelompokan kategori menu ───────────────────────────────────────────────
MENU_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ('Pulsa', ('pulsa', 'regular', 'prepaid')),
    ('Paket Data', ('paket data', 'internet', 'kuota', 'data', 'package')),
    ('E-Money', ('gopay', 'ovo', 'dana', 'linkaja', 'shopeepay', 'e-money', 'ewallet', 'wallet')),
    ('Games', ('game', 'voucher', 'mobile legends', 'free fire', 'pubg', 'steam', 'diamond', 'garena')),
    ('PLN', ('pln', 'listrik', 'token', 'kwh')),
    ('TV & Streaming', ('tv', 'vision', 'indovision', 'mnc', 'kvision', 'netflix', 'spotify', 'youtube')),
    ('SMS & Telp', ('sms', 'telepon', 'telp', 'voice', 'nelpon')),
    ('Masa Aktif', ('masa aktif', 'aktif')),
    ('BPJS', ('bpjs',)),
    ('PDAM', ('pdam',)),
    ('Tagihan', ('tagihan', 'pascabayar', 'postpaid', 'billing', 'indihome', 'telkom')),
]

# ── Pola varian otomatis per operator ─────────────────────────────────────────
VARIANT_PATTERNS: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    'Telkomsel': [
        ('FLASH', ('flash',)),
        ('Combo', ('combo',)),
        ('Bulanan', ('30 hari', 'bulanan')),
        ('Mingguan', ('7 hari', 'mingguan')),
        ('Harian', ('harian', '1 hari')),
        ('Orbit', ('orbit',)),
        ('Pulsa', ('pulsa', '5.000', '10.000', '20.000', '25.000', '50.000', '100.000')),
    ],
    'Indosat': [
        ('Freedom Internet', ('freedom internet',)),
        ('Freedom Kuota Harian', ('freedom kuota harian', 'kuota harian')),
        ('Freedom Apps', ('freedom apps',)),
        ('Kuota Maraton', ('kuota maraton',)),
        ('Telepon', ('telepon', 'telpon')),
        ('Masa Aktif', ('tambah masa aktif',)),
        ('Pulsa', ('pulsa',)),
    ],
    'XL': [
        ('Xtra Conference', ('xtra conference', 'conference')),
        ('Xtra Edukasi', ('xtra edukasi', 'edukasi')),
        ('Xtra Kuota', ('xtra kuota',)),
        ('FlexMax', ('flexmax', 'flex max')),
        ('Flex Mini', ('flex mini',)),
        ('Harian', ('harian',)),
        ('Data Circle', ('circle',)),
        ('Masa Aktif', ('masa aktif',)),
        ('Pulsa', ('pulsa',)),
    ],
    'AXIS': [
        ('Bronet', ('bronet',)),
        ('Data', ('data',)),
        ('Masa Aktif', ('masa aktif',)),
        ('Pulsa', ('pulsa', 'axis')),
    ],
    'Smartfren': [
        ('Unlimited', ('unlimited',)),
        ('Combo', ('combo',)),
        ('Data', ('data',)),
        ('Masa Aktif', ('masa aktif',)),
    ],
    'Tri': [
        ('AON', ('aon',)),
        ('Bighit', ('bighit',)),
        ('Always On', ('always on',)),
        ('Data', ('data', '3data')),
        ('Masa Aktif', ('masa aktif',)),
    ],
}


def _to_int(value: object, default: int = 0) -> int:
    try:
        if value is None or value == '':
            return default
        return int(float(str(value).replace(',', '').strip()))
    except Exception:
        return default


def _group_label(product: Product) -> str:
    text = f'{product.category} {product.operator or ""} {product.product_name}'.lower()
    for label, keywords in MENU_GROUPS:
        if any(kw in text for kw in keywords):
            return label
    return (product.category or 'Lainnya').strip()[:40]


def _detect_variant(product: Product) -> str:
    operator = str(product.operator or '').strip()
    name_lower = str(product.product_name or '').lower()
    patterns = VARIANT_PATTERNS.get(operator, [])
    for variant_name, keywords in patterns:
        if any(kw in name_lower for kw in keywords):
            return variant_name
    words = name_lower.replace(operator.lower(), '').strip().split()
    return words[0].title() if words else 'Lainnya'


async def sync_products(db: AsyncSession, client: H2HClient) -> int:
    items = await client.price_list()
    if not items:
        raise ValueError('Sinkron gagal: daftar produk kosong.')

    # Deduplikasi: H2H kadang kirim code duplikat, ambil yang pertama
    merged: dict[str, Product] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        code = str(item.get('code') or '').strip()
        if not code or code in merged:
            continue
        merged[code] = Product(
            code=code,
            product_name=str(item.get('name') or code).strip(),
            category=str(item.get('category') or 'Lainnya').strip(),
            operator=str(item.get('operator') or '').strip() or None,
            price=_to_int(item.get('price'), 0),
            status=str(item.get('status') or 'OPEN').strip(),
            provider_status=str(item.get('provider_status') or 'active').strip(),
        )

    if not merged:
        raise ValueError('Sinkron gagal: daftar produk kosong setelah dedup.')

    # Hapus semua dulu lalu insert baru
    await db.execute(delete(Product))
    await db.commit()  # commit delete dulu sebelum insert
    db.add_all(list(merged.values()))
    await db.commit()
    return len(merged)


async def active_categories(db: AsyncSession) -> list[str]:
    result = await db.execute(
        select(Product)
        .where(Product.status == 'OPEN', Product.provider_status == 'active')
        .order_by(Product.category.asc(), Product.product_name.asc())
    )
    labels: list[str] = []
    seen: set[str] = set()
    for product in result.scalars().all():
        label = _group_label(product)
        if label not in seen:
            labels.append(label)
            seen.add(label)
    return labels


async def operators_by_category(db: AsyncSession, category: str) -> list[str]:
    result = await db.execute(
        select(Product)
        .where(Product.status == 'OPEN', Product.provider_status == 'active')
        .order_by(Product.operator.asc(), Product.product_name.asc())
    )
    operators: list[str] = []
    seen: set[str] = set()
    for product in result.scalars().all():
        if _group_label(product) != category:
            continue
        op = product.operator or 'Umum'
        if op not in seen:
            operators.append(op)
            seen.add(op)
    return operators


async def variants_by_operator(db: AsyncSession, category: str, operator: str) -> list[str]:
    result = await db.execute(
        select(Product)
        .where(
            Product.operator == operator,
            Product.status == 'OPEN',
            Product.provider_status == 'active',
        )
        .order_by(Product.price.asc(), Product.product_name.asc())
    )
    products = [p for p in result.scalars().all() if _group_label(p) == category]
    if len(products) <= 6:
        return []
    variants: list[str] = []
    seen: set[str] = set()
    for p in products:
        v = _detect_variant(p)
        if v not in seen:
            variants.append(v)
            seen.add(v)
    return variants


async def products_by_category_operator(db: AsyncSession, category: str, operator: str) -> list[Product]:
    result = await db.execute(
        select(Product)
        .where(
            Product.operator == operator,
            Product.status == 'OPEN',
            Product.provider_status == 'active',
        )
        .order_by(Product.price.asc(), Product.product_name.asc())
    )
    return [p for p in result.scalars().all() if _group_label(p) == category]


async def products_by_variant(db: AsyncSession, category: str, operator: str, variant: str) -> list[Product]:
    all_products = await products_by_category_operator(db, category, operator)
    return [p for p in all_products if _detect_variant(p) == variant]


async def get_product(db: AsyncSession, code: str) -> Product | None:
    return await db.get(Product, code)


async def set_session_state(
    db: AsyncSession,
    chat_id: int,
    *,
    current_category: str | None = None,
    current_operator: str | None = None,
    current_code: str | None = None,
    current_destination: str | None = None,
    current_variant: str | None = None,
    last_action: str | None = None,
) -> UserSession:
    session = await get_or_create_session(db, chat_id)
    if current_category is not None:
        session.current_category = current_category
    if current_operator is not None:
        session.current_operator = current_operator
    if current_code is not None:
        session.current_code = current_code
    if current_destination is not None:
        session.current_destination = current_destination
    if current_variant is not None:
        session.current_variant = current_variant
    if last_action is not None:
        session.last_action = last_action
    await db.commit()
    await db.refresh(session)
    return session


async def get_session_state(db: AsyncSession, chat_id: int) -> UserSession:
    return await get_or_create_session(db, chat_id)


async def save_transaction(
    db: AsyncSession, chat_id: int, product: Product, destination: str, response: dict
) -> Transaction:
    tx = Transaction(
        ref_id=str(response.get('ref_id') or uuid.uuid4().hex[:12]),
        invoice=response.get('invoice'),
        chat_id=chat_id,
        product_code=product.code,
        destination=destination,
        product_name=product.product_name,
        category=product.category,
        operator=product.operator,
        price=int(response.get('price', product.price) or 0),
        status=str(response.get('transaction_status', 'pending')),
        status_label=response.get('status_label'),
        serial_number=response.get('serial_number'),
        raw_response=json.dumps(response, ensure_ascii=False),
    )
    db.add(tx)
    await db.commit()
    await db.refresh(tx)
    return tx


async def latest_transactions(db: AsyncSession, chat_id: int, limit: int = 10) -> list[Transaction]:
    result = await db.execute(
        select(Transaction)
        .where(Transaction.chat_id == chat_id)
        .order_by(Transaction.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


def normalize_destination(raw: str) -> str:
    value = re.sub(r'\s+', '', raw.strip())
    if value.startswith('+'):
        return '+' + re.sub(r'\D', '', value[1:])
    return re.sub(r'[^0-9.|@]', '', value)
