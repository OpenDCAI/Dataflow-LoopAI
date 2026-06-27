import os
from typing import Any

from ...models.body import ResourceItem
from ...models.db_models import ResourceModel
from ...utils.resource.resource import (
    format_db_item,
    preview_csv,
    preview_json,
    preview_text,
)


TEXT_FILE_EXTENSIONS = {
    ".txt",
    ".md",
    ".html",
    ".log",
    ".yaml",
    ".yml",
    ".ini",
    ".xml",
}


class ResourceServiceError(Exception):
    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code


def _serialize_resource(resource: Any) -> dict[str, Any]:
    return {
        "id": getattr(resource, "id", None),
        "name": getattr(resource, "name", None),
        "description": getattr(resource, "description", None),
        "path": getattr(resource, "path", None),
        "status": getattr(resource, "status", None),
        "file_type": getattr(resource, "file_type", None),
        "res_type": getattr(resource, "res_type", None),
        "size": getattr(resource, "size", None),
        "createdAt": getattr(resource, "createdAt", None),
        "updatedAt": getattr(resource, "updatedAt", None),
    }


async def list_resources(
    search: str = "", offset: int = 0, limit: int = 30
) -> list[dict[str, Any]]:
    resources = await ResourceModel.filter(name__contains=search).offset(offset).limit(limit)
    return [_serialize_resource(format_db_item(resource)) for resource in resources]


async def count_resources(search: str = "") -> dict[str, int]:
    count = await ResourceModel.filter(name__contains=search).count()
    return {"count": count}


def _build_resource_item(
    *,
    name: str,
    description: str = "",
    path: str = "",
    res_type: str = "",
) -> ResourceItem:
    resource = ResourceItem(
        name=name,
        description=description,
        path=path,
        res_type=res_type,
    )
    return format_db_item(resource)


async def create_resource(
    *, name: str, description: str = "", path: str = "", res_type: str = ""
) -> dict[str, Any]:
    resource = _build_resource_item(
        name=name,
        description=description,
        path=path,
        res_type=res_type,
    )
    ds_model = ResourceModel(
        name=resource.name,
        description=resource.description,
        path=resource.path,
        status=resource.status,
        file_type=resource.file_type,
        res_type=resource.res_type,
        size=resource.size,
    )
    await ds_model.save()
    resource.id = ds_model.id
    return resource.model_dump()


async def update_resource(resource_id: int, resource: ResourceItem) -> dict[str, Any]:
    ds_model = await ResourceModel.get_or_none(id=resource_id)
    if not ds_model:
        raise ResourceServiceError("resource not found", code=404)

    formatted_resource = format_db_item(resource)
    ds_model.name = formatted_resource.name
    ds_model.description = formatted_resource.description
    ds_model.path = formatted_resource.path
    ds_model.status = formatted_resource.status
    ds_model.file_type = formatted_resource.file_type
    ds_model.res_type = formatted_resource.res_type
    ds_model.size = formatted_resource.size
    await ds_model.save()

    formatted_resource.id = ds_model.id
    return formatted_resource.model_dump()


async def delete_resource(resource_id: int) -> None:
    ds_model = await ResourceModel.get_or_none(id=resource_id)
    if not ds_model:
        raise ResourceServiceError("resource not found", code=404)
    await ds_model.delete()


async def _resolve_resource_path(resource_id: str) -> str:
    if resource_id.startswith("file:///"):
        return resource_id[len("file:///") :]

    ds_model = await ResourceModel.get_or_none(id=resource_id)
    if not ds_model:
        raise ResourceServiceError("resource not found", code=404)
    return ds_model.path


def _preview_by_extension(path: str, offset: int, limit: int) -> tuple[list[Any], int, str]:
    ext = os.path.splitext(path)[1]
    if ext in {".jsonl", ".json"}:
        samples, count = preview_json(path, offset, limit)
    elif ext in {".csv", ".tsv"}:
        samples, count = preview_csv(path, offset, limit)
    elif ext in TEXT_FILE_EXTENSIONS:
        samples, count = preview_text(path, offset, limit)
    else:
        raise ResourceServiceError("file type not supported", code=401)

    return samples, count, ext


async def preview_resource(
    resource_id: str, offset: int = 0, limit: int = 15
) -> dict[str, Any]:
    path = await _resolve_resource_path(resource_id)
    if not os.path.exists(path):
        raise ResourceServiceError("file not found", code=404)

    samples, count, ext = _preview_by_extension(path, offset, limit)
    return {
        "samples": samples,
        "count": count,
        "ext": ext,
    }
