from fastapi import APIRouter, HTTPException

from app.infrastructure.memory_store import store
from app.presentation.schemas import OrderResponse

router = APIRouter(prefix="/orders", tags=["수주"])


def _to_response(order) -> OrderResponse:
    return OrderResponse(
        id=order.id,
        voltage=order.voltage,
        product_group=order.product_group,
        spec=order.spec,
        core_count=order.core_count,
        core_color=order.core_color,
        sheath_color=order.sheath_color,
        customer=order.customer,
        delivery_date=order.delivery_date,
        length_m=order.length_m,
        quantity=order.quantity,
        total_length_m=order.total_length_m,
        cu_weight_kg=order.cu_weight_kg,
        al_weight_kg=order.al_weight_kg,
        packaging=order.packaging,
        is_scheduled=order.is_scheduled,
        priority=order.priority,
    )


@router.get("", response_model=list[OrderResponse])
def list_orders() -> list[OrderResponse]:
    """전체 수주 목록 조회"""
    return [_to_response(o) for o in store.list_orders()]


@router.get("/{order_id}", response_model=OrderResponse)
def get_order(order_id: str) -> OrderResponse:
    """수주 단건 조회"""
    order = store.get_order(order_id)
    if order is None:
        raise HTTPException(
            status_code=404, detail=f"수주 '{order_id}'를 찾을 수 없습니다."
        )
    return _to_response(order)
