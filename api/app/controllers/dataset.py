import os
from fastapi import APIRouter
from ..models.body import response_body, DatasetItem
from ..models.db_models import DatasetModel
from ..utils.dataset.dataset import format_db_item, preview_json, preview_csv, preview_text

router = APIRouter(tags=["dataset"])


@router.get("/dataset", operation_id='getDataset', summary="获取数据集列表")
async def get_dataset(search: str = '', offset: int = 0, limit: int = 30):
    """获取数据集列表"""
    datasets = await DatasetModel.filter(name__contains=search).offset(offset).limit(limit)
    datasets = [format_db_item(ds) for ds in datasets]
    return response_body(data=datasets)()

@router.get("/dataset/count", operation_id='getDatasetCount', summary="获取数据集总数")
async def get_dataset_count(search: str = ''):
    """获取数据集总数"""
    count = await DatasetModel.filter(name__contains=search).count()
    return response_body(data={"count": count})()


@router.post("/dataset", operation_id='createDataset', summary="创建数据集")
async def create_dataset(name: str, description: str = '', path: str = ''):
    """创建数据集"""
    dataset = DatasetItem(
        name=name,
        description=description,
        path=path
    )
    dataset = format_db_item(dataset)
    ds_model = DatasetModel(
        name=dataset.name,
        description=dataset.description,
        path=dataset.path,
        status=dataset.status,
        file_type=dataset.file_type
    )
    await ds_model.save()
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

@router.get("/dataset/preview/{dataset_id}", operation_id='previewDataset', summary="预览数据集")
async def preview_dataset(dataset_id: int, offset: int = 0, limit: int = 15):
    """预览数据集"""
    ds_model = await DatasetModel.get_or_none(id=dataset_id)
    if not ds_model:
        return response_body(code=404, message="dataset not found")()
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
