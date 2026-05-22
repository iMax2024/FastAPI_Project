import ipaddress
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yookassa.domain.notification import WebhookNotification

from app.db_depends import get_async_db
from app.models.orders import Order as OrderModel

router = APIRouter(
    prefix="/payments",
    tags=["payments"],
)


# Список сетей/адресов ЮKassa (Яндекс.Касса) для проверки источника вебхука
YANDEX_IP_LIST: tuple[str, ...] = (
    "185.71.76.0/27",
    "185.71.77.0/27",
    "77.75.153.0/25",
    "77.75.156.11",
    "77.75.156.35",
    "77.75.154.128/25",
    "2a02:5180::/32",
)


def is_ip_allowed(ip: str | None) -> bool:
    if ip is None:
        return False
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False

    for mask in YANDEX_IP_LIST:
        if "/" in mask:
            if address in ipaddress.ip_network(mask, strict=False):
                return True
        else:
            if address == ipaddress.ip_address(mask):
                return True
    return False

def _extract_client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else None


@router.post("/yookassa/webhook", status_code=status.HTTP_200_OK)
async def yookassa_webhook(
        request: Request,
        db: AsyncSession = Depends(get_async_db),
):
    client_ip = _extract_client_ip(request)
    if not is_ip_allowed(client_ip):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="IP not allowed")

    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid JSON: {exc}")

    try:
        notification = WebhookNotification(payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Invalid notification: {exc}")

    payment = notification.object
    order_id = payment.metadata.get("order_id") if payment.metadata else None

    if not order_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing order id")

    result = await db.scalars(select(OrderModel).where(OrderModel.id == int(order_id)))
    order = result.first()
    if order is None:
        return {"status": "ignored"}

    if payment.status == "succeeded":
        if not order.paid_at:
            order.status = "paid"
            order.paid_at = datetime.now(timezone.utc)
            order.payment_id = payment.id
    elif payment.status == "canceled":
        order.status = "canceled"

    await db.commit()
    return {"status": "ok"}