import importlib.util
from pathlib import Path

from loopai.skills.Trainer.runtime_config import _DEFAULT_TEMPLATE_PATH


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_trainer_implementation_exists_only_in_skills() -> None:
    assert not (REPO_ROOT / "loopai" / "agents" / "Trainer").exists()
    legacy_module = ".".join(("loopai", "agents", "Trainer"))
    assert importlib.util.find_spec(legacy_module) is None
    assert importlib.util.find_spec("loopai.skills.Trainer.trainer_agent") is not None


def test_default_trainer_template_comes_from_skill_package() -> None:
    expected = (
        REPO_ROOT
        / "loopai"
        / "skills"
        / "Trainer"
        / "templates"
        / "qwen2_5_coder_bird_full_sft.yaml"
    )
    assert _DEFAULT_TEMPLATE_PATH == expected
    assert _DEFAULT_TEMPLATE_PATH.is_file()
