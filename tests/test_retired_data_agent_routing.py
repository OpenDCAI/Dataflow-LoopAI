from pathlib import Path
from typing import get_args

from api.app.services.task.service import parse_task_state_overrides
from api.app.utils.task.task import build_task_state_config, config_format
from loopai.agents.Starter.tools.check_motivation import MotivationType, check_motivation
from loopai.common.prompts.prompt_loader import PromptLoader
from loopai.schema.states import get_state_config_schema


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_all_data_intents_use_obtainer_route():
    motivations = set(get_args(MotivationType))

    assert "constructor" not in motivations
    assert "webcrawler" not in motivations
    assert check_motivation.invoke({"motivation": "obtain"}) == {
        "motivation": "obtain",
        "next_to": "obtain_node",
    }


def test_active_starter_instructions_do_not_advertise_retired_data_agents():
    prompt = PromptLoader()(prompt_type="system", prompt_name="default_prompt")
    starter_instructions = (
        PROJECT_ROOT / "examples" / "codex_home_example" / "AGENTS.md"
    ).read_text(encoding="utf-8")

    assert "constructor" not in prompt.lower()
    assert "webcrawler" not in prompt.lower()
    assert "`constructor`" not in starter_instructions.lower()
    assert "`webcrawler`" not in starter_instructions.lower()
    assert "obtainercli" in starter_instructions.lower()


def test_retired_data_agent_sections_are_hidden_but_obtainer_remains():
    schema = get_state_config_schema()
    exposed = build_task_state_config(
        {
            "language": "zh",
            "constructor": {"mapping_results": {"output_file": "old.jsonl"}},
            "webcrawler": {"output_dir": "old-web-output"},
            "obtainer": {"mapping_results": {"output_file": "current.jsonl"}},
        }
    )

    assert "constructor" not in schema
    assert "webcrawler" not in schema
    assert "constructor" not in exposed
    assert "webcrawler" not in exposed
    assert exposed["obtainer"]["mapping_results"]["value"]["output_file"] == "current.jsonl"


def test_new_task_config_drops_retired_data_agent_sections():
    formatted = config_format(
        {
            "states": {
                "constructor": {"output_dir": {"value": "old-constructor"}},
                "webcrawler": {"output_dir": {"value": "old-webcrawler"}},
                "obtainer": {"output_dir": {"value": "current-obtainer"}},
            }
        },
        "task-1",
    )

    default_states = formatted["default_states"]
    assert "constructor" not in default_states
    assert "webcrawler" not in default_states
    assert default_states["obtainer"]["output_dir"] == "current-obtainer"


def test_new_task_state_overrides_drop_retired_data_agent_sections():
    parsed = parse_task_state_overrides(
        '{"constructor":{"output_dir":"old"},'
        '"webcrawler":{"output_dir":"old"},'
        '"obtainer":{"output_dir":"current"}}'
    )

    assert parsed == {"obtainer": {"output_dir": "current"}}
