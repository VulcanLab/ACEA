from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from models import ServiceRecord
from asap_validator import validate_adapter

router = APIRouter(prefix="/api/services", tags=["services"])
_services: dict[str, ServiceRecord] = {}


class ServiceIn(BaseModel):
    id: str
    name: str
    url: str
    type: str
    token: str = ""


@router.post("")
async def register_service(body: ServiceIn):
    capabilities: dict = {}
    if body.url and "dummy" not in body.url.lower():
        is_valid, validation_err, capabilities = await validate_adapter(body.url, body.type)
        if not is_valid:
            raise HTTPException(status_code=400, detail=f"ASAP validation failed: {validation_err}")
    rec = ServiceRecord(**body.model_dump(), capabilities=capabilities)
    _services[body.id] = rec
    return _services[body.id].__dict__


@router.get("")
def list_services():
    return [s.__dict__ for s in _services.values()]


@router.get("/{service_id}")
def get_service(service_id: str):
    s = _services.get(service_id)
    if not s:
        raise HTTPException(404, "Service not found")
    return s.__dict__


@router.delete("/{service_id}")
def delete_service(service_id: str):
    if service_id not in _services:
        raise HTTPException(404, "Service not found")
    del _services[service_id]
    return {"deleted": service_id}


def get_registry() -> dict[str, ServiceRecord]:
    return _services
