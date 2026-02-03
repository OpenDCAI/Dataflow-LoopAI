import os
from fastapi import APIRouter
from ..models.body import response_body, ResourceItem
from ..models.db_models import ResourceModel
from ..utils.resource.resource import format_db_item, preview_json, preview_csv, preview_text

router = APIRouter(tags=["resource"])


@router.get("/resource", operation_id='getResource', summary="获取资源列表")
async def get_resource(search: str = '', offset: int = 0, limit: int = 30):
    """获取资源列表"""
    resources = await ResourceModel.filter(name__contains=search).offset(offset).limit(limit)
    resources = [format_db_item(ds) for ds in resources]
    return response_body(data=resources)()

@router.get("/resource/count", operation_id='getResourceCount', summary="获取资源总数")
async def get_resource_count(search: str = ''):
    """获取资源总数"""
    count = await ResourceModel.filter(name__contains=search).count()
    return response_body(data={"count": count})()


@router.post("/resource", operation_id='createResource', summary="创建资源")
async def create_resource(name: str, description: str = '', path: str = '', res_type: str = ''):
    """创建资源"""
    resource = ResourceItem(
        name=name,
        description=description,
        path=path,
        res_type=res_type
    )
    resource = format_db_item(resource)
    ds_model = ResourceModel(
        name=resource.name,
        description=resource.description,
        path=resource.path,
        status=resource.status,
        file_type=resource.file_type,
        res_type=resource.res_type
    )
    await ds_model.save()
    return response_body(data=resource)()


@router.put("/resource/{resource_id}", operation_id='updateResource', summary="更新资源")
async def update_resource(resource_id: int, resource: ResourceItem):
    """更新资源"""
    ds_model = await ResourceModel.get_or_none(id=resource_id)
    if not ds_model:
        return response_body(code=404, message="resource not found")()
    resource = format_db_item(resource)
    ds_model.name = resource.name
    ds_model.path = resource.path
    ds_model.status = resource.status
    ds_model.file_type = resource.file_type
    ds_model.res_type = resource.res_type
    await ds_model.save()
    return response_body(data=resource)()

@router.delete("/resource/{resource_id}", operation_id='deleteResource', summary="删除资源")
async def delete_resource(resource_id: int):
    """删除资源"""
    ds_model = await ResourceModel.get_or_none(id=resource_id)
    if not ds_model:
        return response_body(code=404, message="resource not found")()
    await ds_model.delete()
    return response_body()()

@router.get("/resource/preview/{resource_id}", operation_id='previewResource', summary="预览资源")
async def preview_resource(resource_id: int, offset: int = 0, limit: int = 15):
    """预览资源"""
    ds_model = await ResourceModel.get_or_none(id=resource_id)
    if not ds_model:
        return response_body(code=404, message="resource not found")()
    path = ds_model.path
    ext = os.path.splitext(path)[1]
    if ext == '.jsonl' or ext == '.json':
        samples, count = preview_json(path, offset, limit)
    elif ext in ['.csv', '.tsv']:
        samples, count = preview_csv(path, offset, limit)
    elif ext in ['.txt', '.md']:
        samples, count = preview_text(path, offset, limit)
    else:
        return response_body(code=401, message="file type not supported")()
    return response_body(data={
        "samples": samples,
        "count": count
    })()
