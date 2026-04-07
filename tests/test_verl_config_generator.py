"""
verl 配置生成器单元测试

覆盖:
- VerlConfigGenerator: GRPO/PPO/SFT 脚本生成
- 自动调参逻辑
- 自定义模板
- 保存脚本
"""

import os
import tempfile

import pytest

from loopai.agents.Trainer.utils.verl_config_generator import (
    VerlConfigGenerator,
    generate_verl_config_explanation,
)


@pytest.fixture
def generator():
    return VerlConfigGenerator()


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


# ======================== 脚本生成 ========================

class TestVerlScriptGeneration:
    """verl 训练脚本生成"""

    def test_grpo_script_generation(self, generator):
        """GRPO 脚本应包含关键参数"""
        script = generator.generate_script(
            train_mode="grpo",
            model_path="Qwen/Qwen2-7B",
            train_files="/data/train.parquet",
        )
        assert "grpo" in script
        assert "Qwen/Qwen2-7B" in script
        assert "/data/train.parquet" in script
        assert "verl.trainer.main_ppo" in script

    def test_ppo_script_generation(self, generator):
        """PPO 脚本应包含 critic 配置"""
        script = generator.generate_script(
            train_mode="ppo",
            model_path="deepseek-7b",
            train_files="/data/train.parquet",
        )
        assert "gae" in script
        assert "critic" in script
        assert "deepseek-7b" in script

    def test_sft_script_generation(self, generator):
        """SFT 脚本应使用 torchrun"""
        script = generator.generate_script(
            train_mode="sft",
            model_path="Qwen/Qwen2.5-0.5B",
            train_files="/data/sft.parquet",
        )
        assert "torchrun" in script
        assert "verl.trainer.sft_trainer" in script
        assert "Qwen/Qwen2.5-0.5B" in script

    def test_invalid_train_mode(self, generator):
        """不支持的训练模式应抛出异常"""
        with pytest.raises(ValueError, match="不支持"):
            generator.generate_script(
                train_mode="invalid",
                model_path="model",
                train_files="data.parquet",
            )

    def test_custom_params(self, generator):
        """额外参数应覆盖默认值"""
        script = generator.generate_script(
            train_mode="grpo",
            model_path="model",
            train_files="data.parquet",
            extra_params={"TOTAL_EPOCHS": "99", "LEARNING_RATE": "1e-7"},
        )
        assert 'TOTAL_EPOCHS="99"' in script
        assert 'LEARNING_RATE="1e-7"' in script

    def test_project_experiment_name(self, generator):
        """项目名和实验名应写入脚本"""
        script = generator.generate_script(
            train_mode="grpo",
            model_path="model",
            train_files="data.parquet",
            project_name="my_project",
            experiment_name="exp_001",
        )
        assert 'PROJECT_NAME="my_project"' in script
        assert 'EXPERIMENT_NAME="exp_001"' in script


# ======================== 自动调参 ========================

class TestVerlAutoTune:
    """基于任务描述的自动调参"""

    def test_math_task_tuning(self, generator):
        """数学任务应增加响应长度、降低学习率"""
        script = generator.generate_script(
            train_mode="grpo",
            model_path="model",
            train_files="data.parquet",
            task_description="数学推理任务",
        )
        assert 'MAX_RESPONSE_LENGTH="2048"' in script
        assert 'LEARNING_RATE="5e-7"' in script

    def test_chat_task_tuning(self, generator):
        """对话任务应使用较少 rollout"""
        script = generator.generate_script(
            train_mode="grpo",
            model_path="model",
            train_files="data.parquet",
            task_description="日常对话任务",
        )
        assert 'ROLLOUT_N="3"' in script

    def test_code_sft_tuning(self, generator):
        """代码 SFT 应增加序列长度"""
        script = generator.generate_script(
            train_mode="sft",
            model_path="model",
            train_files="data.parquet",
            task_description="代码生成微调",
        )
        assert 'MAX_LENGTH="4096"' in script

    def test_no_description_uses_defaults(self, generator):
        """无任务描述应使用默认值"""
        script = generator.generate_script(
            train_mode="grpo",
            model_path="model",
            train_files="data.parquet",
            task_description="",
        )
        # 默认值
        assert 'LEARNING_RATE="1e-6"' in script


# ======================== 保存脚本 ========================

class TestVerlScriptSave:
    """训练脚本保存"""

    def test_save_script(self, generator, tmp_dir):
        """保存脚本应创建可执行文件"""
        script = generator.generate_script(
            train_mode="grpo",
            model_path="model",
            train_files="data.parquet",
        )
        path = os.path.join(tmp_dir, "sub", "train.sh")
        assert generator.save_script(script, path) is True
        assert os.path.exists(path)
        assert os.access(path, os.X_OK)

        with open(path, "r") as f:
            content = f.read()
        assert "verl" in content

    def test_custom_template(self, generator, tmp_dir):
        """使用自定义模板"""
        custom_tpl = os.path.join(tmp_dir, "my_template.sh")
        with open(custom_tpl, "w") as f:
            f.write("#!/bin/bash\necho custom ${MODEL_PATH}\n")

        script = generator.generate_script(
            train_mode="grpo",
            model_path="my_model",
            train_files="data.parquet",
            template_path=custom_tpl,
        )
        assert "my_model" in script
        assert "echo custom" in script


# ======================== 配置说明 ========================

class TestVerlConfigExplanation:
    """配置说明文档生成"""

    def test_explanation_grpo(self):
        params = {"MODEL_PATH": "Qwen2-7B", "LEARNING_RATE": "1e-6", "TOTAL_EPOCHS": "15"}
        text = generate_verl_config_explanation("grpo", params, "数学推理")
        assert "GRPO" in text
        assert "Qwen2-7B" in text
        assert "数学推理" in text

    def test_explanation_sft(self):
        params = {"MODEL_PATH": "Qwen2.5-0.5B", "LEARNING_RATE": "1e-5"}
        text = generate_verl_config_explanation("sft", params, "SFT 微调")
        assert "SFT" in text.upper()
