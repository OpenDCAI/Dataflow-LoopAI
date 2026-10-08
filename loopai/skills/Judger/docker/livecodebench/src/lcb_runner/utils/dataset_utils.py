import os

from datasets import DatasetDict, load_dataset, load_from_disk


def load_local_dataset(local_dataset_path: str):
    """Load a dataset from a local path.

    Supports directories written by `Dataset.save_to_disk`, as well as
    `.json`, `.jsonl` and `.parquet` files. When the file/directory contains
    several splits, `test`/`train`/`validation` is picked in that order.
    """
    if os.path.isdir(local_dataset_path):
        dataset = load_from_disk(local_dataset_path)
    elif local_dataset_path.endswith(".parquet"):
        dataset = load_dataset("parquet", data_files=local_dataset_path)
    elif local_dataset_path.endswith((".json", ".jsonl")):
        dataset = load_dataset("json", data_files=local_dataset_path)
    else:
        raise ValueError(
            f"Unsupported local dataset path {local_dataset_path!r}. Expected a directory "
            "saved with `Dataset.save_to_disk`, or a .json/.jsonl/.parquet file."
        )

    if isinstance(dataset, DatasetDict):
        for split in ("test", "train", "validation"):
            if split in dataset:
                return dataset[split]
        raise ValueError(
            f"Local dataset {local_dataset_path!r} only has splits "
            f"{list(dataset.keys())}, expected one of test/train/validation."
        )
    return dataset
