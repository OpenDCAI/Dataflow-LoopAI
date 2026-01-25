import os
from fastapi import APIRouter
from ..models.body import response_body, DatasetItem
from ..models.db_models import DatasetModel
from ..utils.dataset.dataset import format_db_item

router = APIRouter(tags=["dataset"])


@router.get("/dataset", operation_id='getDataset', summary="获取数据集列表")
async def get_dataset(offset: int = 0, limit: int = 30):
    """获取数据集列表"""
    datasets = await DatasetModel.all().offset(offset).limit(limit)
    for ds in datasets:
        ds = format_db_item(ds)
    return response_body(data=datasets)()

@router.get("/dataset/count", operation_id='getDatasetCount', summary="获取数据集总数")
async def get_dataset_count():
    """获取数据集总数"""
    count = await DatasetModel.all().count()
    return response_body(data={"count": count})()


@router.post("/dataset", operation_id='createDataset', summary="创建数据集")
async def create_dataset(dataset: DatasetItem):
    """创建数据集"""
    path = dataset.path
    if not path:
        return response_body(code=401, message="path is required")()
    dataset = format_db_item(dataset)
    ds_model = DatasetModel(
        name=dataset.name,
        path=dataset.path,
        status=dataset.status,
        file_type=dataset.file_type
    )
    await ds_model.create()
    return response_body(data=dataset)()


@router.put("/dataset/{dataset_id}", operation_id='updateDataset', summary="更新数据集")
async def update_dataset(dataset_id: int, dataset: DatasetItem):
    """更新数据集"""
    ds_model = await DatasetModel.get_or_none(id=dataset_id)
    if not ds_model:
        return response_body(code=404, message="dataset not found")()
    dataset = format_db_item(dataset)
    ds_model.name = dataset.name
    ds_model.path = dataset.path
    ds_model.status = dataset.status
    ds_model.file_type = dataset.file_type
    await ds_model.save()
    return response_body(data=dataset)()

@router.delete("/dataset/{dataset_id}", operation_id='deleteDataset', summary="删除数据集")
async def delete_dataset(dataset_id: int):
    """删除数据集"""
    ds_model = await DatasetModel.get_or_none(id=dataset_id)
    if not ds_model:
        return response_body(code=404, message="dataset not found")()
    await ds_model.delete()
    return response_body()()