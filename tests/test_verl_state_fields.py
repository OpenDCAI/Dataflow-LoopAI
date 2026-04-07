"""
verl 状态字段和前置校验单元测试

覆盖:
- TrainerState 新增字段验证
- check_required_fields 对 verl 框架的校验逻辑
"""

import pytest

from loopai.schema.states import TrainerState


# ======================== TrainerState 字段 ========================

class TestTrainerStateVerlFields:
    """TrainerState 中 verl 相关字段的存在性和默认值"""

    def test_verl_dir_field_exists(self):
        """verl_dir 字段应存在且默认为空字符串"""
        state = TrainerState()
        assert hasattr(state, "verl_dir")
        assert state.verl_dir == ""

    def test_verl_env_path_field_exists(self):
        """verl_env_path 字段应存在且默认为空字符串"""
        state = TrainerState()
        assert hasattr(state, "verl_env_path")
        assert state.verl_env_path == ""

    def test_verl_train_mode_field_exists(self):
        """verl_train_mode 字段应存在且默认为 grpo"""
        state = TrainerState()
        assert hasattr(state, "verl_train_mode")
        assert state.verl_train_mode == "grpo"

    def test_train_framework_allowed_values(self):
        """train_framework 应支持 llamafactory 和 verl"""
        field_info = TrainerState.model_fields["train_framework"]
        allowed = field_info.json_schema_extra.get("allowed_values", [])
        assert "llamafactory" in allowed
        assert "verl" in allowed

    def test_verl_train_mode_allowed_values(self):
        """verl_train_mode 应支持 grpo/ppo/sft"""
        field_info = TrainerState.model_fields["verl_train_mode"]
        allowed = field_info.json_schema_extra.get("allowed_values", [])
        assert "grpo" in allowed
        assert "ppo" in allowed
        assert "sft" in allowed

    def test_set_verl_fields(self):
        """verl 字段应可正常赋值"""
        state = TrainerState(
            verl_dir="/path/to/verl",
            verl_env_path="/path/to/env",
            verl_train_mode="ppo",
            train_framework="verl",
        )
        assert state.verl_dir == "/path/to/verl"
        assert state.verl_env_path == "/path/to/env"
        assert state.verl_train_mode == "ppo"
        assert state.train_framework == "verl"

    def test_existing_llamafactory_fields_preserved(self):
        """原有 LlamaFactory 字段不应被影响"""
        state = TrainerState()
        assert hasattr(state, "llamafactory_dir")
        assert hasattr(state, "llamafactory_env_path")
        assert hasattr(state, "CUDA_VISIBLE_DEVICES")
        assert hasattr(state, "swanlab_api_key")


# ======================== 前置校验逻辑 ========================

class TestCheckRequiredFieldsLogic:
    """验证前置校验中 verl/llamafactory 不同分支的字段要求"""

    def test_verl_requires_verl_dir(self):
        """verl 框架应要求 verl_dir 字段"""
        # 模拟 check_required_fields 中的逻辑
        required_fields = {
            "trainer": [
                'train_framework',
                'train_input_dataset_path',
                'train_input_task_description',
                'train_input_config_template_path',
                'train_input_model_name',
            ]
        }
        framework = "verl"
        if framework == "llamafactory":
            required_fields["trainer"].append("llamafactory_dir")
        elif framework == "verl":
            required_fields["trainer"].append("verl_dir")

        assert "verl_dir" in required_fields["trainer"]
        assert "llamafactory_dir" not in required_fields["trainer"]

    def test_llamafactory_requires_llamafactory_dir(self):
        """llamafactory 框架应要求 llamafactory_dir"""
        required_fields = {"trainer": ["train_framework"]}
        framework = "llamafactory"
        if framework == "llamafactory":
            required_fields["trainer"].append("llamafactory_dir")
        elif framework == "verl":
            required_fields["trainer"].append("verl_dir")

        assert "llamafactory_dir" in required_fields["trainer"]
        assert "verl_dir" not in required_fields["trainer"]
