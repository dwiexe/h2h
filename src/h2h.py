from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from config import settings


class H2HError(Exception):
    def __init__(self, message: str, *, response_text: str = '', request_payload: dict | None = None):
        super().__init__(message)
        self.response_text = response_text
        self.request_payload = request_payload or {}


@dataclass
class H2HClient:
    member_id: str
    pin: str
    password: str
    base_url: str = 'https://api.h2h.id/api/trx'

    @classmethod
    def from_settings(cls) -> 'H2HClient':
        return cls(
            member_id=settings.h2h_member_id,
            pin=settings.h2h_pin,
            password=settings.h2h_password,
            base_url=settings.h2h_base_url,
        )

    def _auth_params(self) -> dict[str, str]:
        return {
            'memberID': self.member_id,
            'pin': self.pin,
            'password': self.password,
        }

    async def _get(self, path: str, params: dict[str, Any] = {}) -> dict[str, Any]:
        url = f'{self.base_url}{path}'
        all_params = {**self._auth_params(), **params}
        timeout = httpx.Timeout(45, connect=15)
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(url, params=all_params)
        except httpx.TimeoutException as exc:
            raise H2HError('Request timeout ke H2H.id') from exc
        except httpx.HTTPError as exc:
            raise H2HError(f'Gagal koneksi ke H2H.id: {exc}') from exc

        raw = response.text.strip()
        try:
            data = response.json()
        except ValueError:
            raise H2HError(f'Response bukan JSON: {raw[:500]}')

        if not data.get('status', False):
            msg = data.get('message', 'Error tidak diketahui')
            raise H2HError(msg, response_text=raw, request_payload=all_params)

        return data

    async def check_balance(self) -> float:
        data = await self._get('/balance')
        return float(data.get('data', {}).get('balance', 0))

    async def price_list(self) -> list[dict[str, Any]]:
        data = await self._get('/pricelist')
        items = data.get('data', [])
        if isinstance(items, list):
            return items
        return []

    async def topup(self, product: str, dest: str, ref_id: str) -> dict[str, Any]:
        data = await self._get('', {
            'product': product,
            'dest': dest,
            'refID': ref_id,
        })
        return data.get('data', data)

    async def check_status(self, ref_id: str) -> dict[str, Any]:
        data = await self._get('/status', {'refID': ref_id})
        return data.get('data', data)

    async def check_pln(self, meter_id: str) -> dict[str, Any]:
        """Cek nama pelanggan PLN (endpoint publik, tanpa auth)."""
        timeout = httpx.Timeout(30, connect=10)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                'https://api.h2h.id/api/pln/check',
                json={'meter_id': meter_id},
            )
        data = response.json()
        if not data.get('success', False):
            raise H2HError(data.get('message', 'PLN check gagal'))
        return data.get('data', {})

    async def check_bill(self, buyer_sku_code: str, customer_no: str) -> dict[str, Any]:
        """Cek tagihan pascabayar (endpoint publik)."""
        timeout = httpx.Timeout(30, connect=10)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                'https://api.h2h.id/api/bill/check',
                json={'buyer_sku_code': buyer_sku_code, 'customer_no': customer_no},
            )
        data = response.json()
        if not data.get('success', False):
            raise H2HError(data.get('message', 'Bill check gagal'))
        return data.get('data', {})
