from fastapi import APIRouter
from ..models.body import response_body, ResourceItem
from ..services.resource import (
    ResourceServiceError,
    count_resources,
    create_resource as create_resource_service,
    delete_resource as delete_resource_service,
    list_resources,
    preview_resource as preview_resource_service,
    update_resource as update_resource_service,
)

router = APIRouter(tags=["resource"])


@router.get("/resource", operation_id='getResource', summary="获取资源列表")
async def get_resource(search: str = '', offset: int = 0, limit: int = 30):
    """获取资源列表"""
    return response_body(data=await list_resources(search, offset, limit))()

@router.get("/resource/count", operation_id='getResourceCount', summary="获取资源总数")
async def get_resource_count(search: str = ''):
    """获取资源总数"""
    return response_body(data=await count_resources(search))()


@router.post("/resource", operation_id='createResource', summary="创建资源")
async def create_resource(name: str, description: str = '', path: str = '', res_type: str = ''):
    """创建资源"""
    return response_body(
        data=await create_resource_service(
            name=name,
            description=description,
            path=path,
            res_type=res_type,
        )
    )()


@router.put("/resource/{resource_id}", operation_id='updateResource', summary="更新资源")
async def update_resource(resource_id: int, resource: ResourceItem):
    """更新资源"""
    try:
        return response_body(data=await update_resource_service(resource_id, resource))()
    except ResourceServiceError as exc:
        return response_body(code=exc.code, message=exc.message)()

@router.delete("/resource/{resource_id}", operation_id='deleteResource', summary="删除资源")
async def delete_resource(resource_id: int):
    """删除资源"""
    try:
        await delete_resource_service(resource_id)
        return response_body()()
    except ResourceServiceError as exc:
        return response_body(code=exc.code, message=exc.message)()

@router.post("/resource/preview", operation_id='previewResource', summary="预览资源")
async def preview_resource(resource_id: str, offset: int = 0, limit: int = 15):
    """预览资源
    :param resource_id: 资源ID, 可以是文件路径或数据库ID, 其中文件路径需要以`file:///`开头
    """
    try:
        return response_body(data=await preview_resource_service(resource_id, offset, limit))()
    except ResourceServiceError as exc:
        return response_body(code=exc.code, message=exc.message)()
