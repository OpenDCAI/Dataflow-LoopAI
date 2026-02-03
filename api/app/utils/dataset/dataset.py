import os
import json
from ...models.body import DatasetItem


def format_db_item(dataset: DatasetItem):
    """
    format_db_item 的 Docstring

    :param dataset: 说明
    :type dataset: DatasetItem
    """
    path = dataset.path
    if not os.path.exists(path):
        status = 'not_exist'
        size = 0
    else:
        status = 'exist'
        size = os.path.getsize(path)
    file_type = os.path.splitext(path)[1]
    dataset.status = status
    dataset.file_type = file_type
    dataset.size = size
    return dataset


def preview_json(json_path: str, offset: int = 0, limit: int = 15):
    """
    preview_json 的 Docstring

    :param json_path: 说明
    :type json_path: str
    :param offset: 说明
    :type offset: int
    :param limit: 说明
    :type limit: int
    """
    ext = os.path.splitext(json_path)[1]
    samples = []
    count = 0
    if ext == '.jsonl':
        with open(json_path, 'r') as f:
            lines = f.readlines()
            count = len(lines)
            samples = [json.loads(line.strip())
                       for line in lines[offset:limit+offset]]
    elif ext == '.json':
        with open(json_path, 'r') as f:
            sample = json.load(f)
            if type(sample) == list:
                count = len(sample)
                samples.extend(sample[offset:limit+offset])
            else:
                count = 1
                samples.append(sample)
    return samples, count


def preview_csv(file_path: str, offset: int = 0, limit: int = 15, delimiter: str = ','):
    """
    preview_csv 的 Docstring

    :param file_path: 说明
    :type file_path: str
    :param offset: 说明
    :type offset: int
    :param limit: 说明
    :type limit: int
    :param delimiter: 说明
    :type delimiter: str
    """
    with open(file_path, 'r') as f:
        lines = f.readlines()
    count = len(lines)
    samples = [line.strip().split(delimiter)
               for line in lines[offset:limit+offset]]
    for idx, item in enumerate(samples):
        row_item = []
        for j, cell in enumerate(item):
            row_item.append({j: cell})
        samples[idx] = row_item
    return samples, count


def preview_text(text_path: str, offset: int = 0, limit: int = 15):
    """
    preview_text 的 Docstring

    :param text_path: 说明
    :type text_path: str
    :param offset: 说明
    :type offset: int
    :param limit: 说明
    :type limit: int
    """
    with open(text_path, 'r') as f:
        lines = f.readlines()
        count = len(lines)
        samples = [line.strip() for line in lines[offset:limit+offset]]
    return samples, count
