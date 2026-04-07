"""
verl 日志解析器单元测试

覆盖:
- MetricsExtractor: verl JSONL 格式解析
- MetricsExtractor: LlamaFactory JSON 格式解析 (回归)
- 混合格式兼容性
"""

import json

import pytest

from loopai.agents.Trainer.utils.realtime_log_parser import MetricsExtractor


@pytest.fixture
def extractor():
    return MetricsExtractor(total_steps=100, total_epochs=3)


# ======================== verl JSONL 格式 ========================

class TestVerlLogParsing:
    """verl file logger JSONL 格式解析"""

    def test_verl_jsonl_basic(self, extractor):
        """基本的 verl JSONL 日志行"""
        line = json.dumps({"step": 10, "data": {"train/loss": 0.5, "train/lr": 1e-6}})
        metrics = extractor.extract_metrics(line)

        assert metrics["step"] == 10
        assert metrics["train/loss"] == 0.5
        assert metrics["loss"] == 0.5  # 短 key
        assert metrics["train/lr"] == 1e-6
        assert metrics["lr"] == 1e-6

    def test_verl_jsonl_grad_norm(self, extractor):
        """verl 日志中的 grad_norm"""
        line = json.dumps({"step": 20, "data": {"train/grad_norm": 1.23}})
        metrics = extractor.extract_metrics(line)

        assert metrics["step"] == 20
        assert metrics["grad_norm"] == 1.23

    def test_verl_jsonl_val_metrics(self, extractor):
        """verl 验证指标"""
        line = json.dumps({"step": 50, "data": {"val/loss": 0.3, "val/reward_mean": 0.8}})
        metrics = extractor.extract_metrics(line)

        assert metrics["step"] == 50
        assert metrics["loss"] == 0.3
        assert metrics["reward_mean"] == 0.8

    def test_verl_jsonl_empty_data(self, extractor):
        """data 为空字典时应只返回 step"""
        line = json.dumps({"step": 5, "data": {}})
        metrics = extractor.extract_metrics(line)

        assert metrics["step"] == 5
        assert len(metrics) == 1

    def test_verl_jsonl_non_numeric_skipped(self, extractor):
        """非数值类型的值应被跳过"""
        line = json.dumps({"step": 1, "data": {"train/loss": 0.5, "info": "text_value"}})
        metrics = extractor.extract_metrics(line)

        assert metrics["loss"] == 0.5
        assert "info" not in metrics

    def test_verl_jsonl_with_whitespace(self, extractor):
        """带前后空白的行应正常解析"""
        line = "  " + json.dumps({"step": 3, "data": {"train/loss": 0.7}}) + "  \n"
        metrics = extractor.extract_metrics(line)

        assert metrics["step"] == 3
        assert metrics["loss"] == 0.7


# ======================== LlamaFactory 格式 (回归测试) ========================

class TestLlamaFactoryLogParsing:
    """LlamaFactory 格式日志解析 - 确保不被 verl 改动破坏"""

    def test_llamafactory_json_format(self, extractor):
        """LlamaFactory 的 JSON 格式日志行"""
        line = "{'loss': 0.1469, 'epoch': 2.92}"
        metrics = extractor.extract_metrics(line)

        assert metrics.get("loss") == pytest.approx(0.1469)
        assert metrics.get("epoch") == pytest.approx(2.92)

    def test_llamafactory_key_value_format(self, extractor):
        """LlamaFactory key=value 格式"""
        line = "loss: 0.2345, epoch: 1.5, learning_rate: 1e-5"
        metrics = extractor.extract_metrics(line)

        assert "loss" in metrics
        assert metrics["loss"] == pytest.approx(0.2345)

    def test_plain_text_no_metrics(self, extractor):
        """普通文本行不应产生指标"""
        line = "Loading model from checkpoint..."
        metrics = extractor.extract_metrics(line)
        assert len(metrics) == 0

    def test_malformed_json_fallback(self, extractor):
        """畸形的 JSON 应回退到正则解析"""
        line = '{"step": broken, loss: 0.5}'
        metrics = extractor.extract_metrics(line)
        # 应通过正则模式提取 loss 和 step
        assert "loss" in metrics or "step" in metrics


# ======================== 边界情况 ========================

class TestEdgeCases:
    """边界情况"""

    def test_empty_line(self, extractor):
        metrics = extractor.extract_metrics("")
        assert len(metrics) == 0

    def test_only_whitespace(self, extractor):
        metrics = extractor.extract_metrics("   \n\t  ")
        assert len(metrics) == 0

    def test_verl_like_but_missing_data(self, extractor):
        """有 step 但缺少 data 键，不应匹配 verl 格式"""
        line = json.dumps({"step": 10, "info": "no data key"})
        metrics = extractor.extract_metrics(line)
        # 应回退到正则解析，可能提取 step
        # 不应崩溃
        assert isinstance(metrics, dict)
