import json
import os
from pathlib import Path
from typing import Any

from tortoise.expressions import Q

from ...models.body import ConfigModel
from ...models.db_models import StarterConfig
from ...utils.config.config import format_value, get_state_config, get_system_config
from loopai.common.tracking import strip_retired_tracking_fields


CURRENT_DIR = Path(__file__).resolve().parent
APP_DIR = CURRENT_DIR.parent.parent
API_DIR = APP_DIR.parent
LOOPAI_DIR = API_DIR.parent


class ConfigServiceError(Exception):
    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code


async def get_starter_config() -> dict[str, Any]:
    system_config = await get_system_config(str(LOOPAI_DIR))
    state_config = await get_state_config(str(LOOPAI_DIR))
    return {
        "id": system_config["id"],
        "name": system_config["name"],
        "system": system_config["config"],
        "states": state_config["config"],
    }


def _parse_config_payload(raw_config: str) -> dict[str, Any]:
    try:
        return strip_retired_tracking_fields(json.loads(raw_config))
    except Exception as exc:
        raise ConfigServiceError("config格式错误") from exc


def _apply_system_config(
    original_config: dict[str, Any], system_config: dict[str, Any]
) -> None:
    for key, value in system_config.items():
        format_item = format_value(value)
        original_config.setdefault("system", {})[key] = format_item["value"]


def _apply_states_config(
    original_config: dict[str, Any], states_config: dict[str, Any]
) -> None:
    default_states = original_config.setdefault("default_states", {})
    for series_key, series_value in states_config.items():
        if series_key == "default":
            for key, value in series_value.items():
                format_item = format_value(value)
                default_states[key] = format_item["value"]
            continue

        nested_state = default_states.setdefault(series_key, {})
        for key, value in series_value.items():
            format_item = format_value(value)
            nested_state[key] = format_item["value"]


async def update_starter_config(config: ConfigModel) -> dict[str, Any]:
    config_obj = _parse_config_payload(config.config)

    original_config = await StarterConfig.filter(Q(id=config.id)).first()
    if not original_config:
        raise ConfigServiceError("config不存在")

    original_config_obj = strip_retired_tracking_fields(json.loads(original_config.config))
    _apply_system_config(original_config_obj, config_obj.get("system", {}))
    _apply_states_config(original_config_obj, config_obj.get("states", {}))

    original_config.config = json.dumps(original_config_obj)
    await original_config.save()

    return {
        "id": original_config.id,
        "name": original_config.name,
        "config": original_config_obj,
    }


def list_directory(path: str) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        raise ConfigServiceError("目录不存在")

    entries = []
    for name in os.listdir(path):
        entries.append(
            {
                "name": name,
                "is_dir": os.path.isdir(os.path.join(path, name)),
            }
        )

    return sorted(entries, key=lambda item: (not item["is_dir"], item["name"]))
