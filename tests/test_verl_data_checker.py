"""
verl 数据格式检查器单元测试

覆盖:
- check_verl_data_format: RL(grpo/ppo) 和 SFT 模式
- 多种数据格式: json / jsonl
- 正常路径 / 异常路径 / 边界情况
"""

import json
import os
import tempfile

import pytest

from loopai.agents.Trainer.utils.data_checker import (
    check_verl_data_format,
    generate_verl_format_report,
)


# ======================== fixtures ========================

@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ======================== RL 模式 (grpo/ppo) ========================

class TestVerlRLDataCheck:
    """verl RL 数据格式检查"""

    def test_valid_rl_json(self, tmp_dir):
        """正常的 RL 数据 (JSON 格式) 应通过校验"""
        data = [
            {"prompt": [{"role": "user", "content": "1+1=?"}], "data_source": "math"},
            {"prompt": [{"role": "user", "content": "hello"}], "data_source": "chat"},
        ]
        path = os.path.join(tmp_dir, "train.json")
        _write_json(path, data)

        result = check_verl_data_format(path, train_mode="grpo")
        assert result["is_valid"] is True
        assert result["total_samples"] == 2
        assert result["format_type"] == "json"
        assert len(result["errors"]) == 0

    def test_valid_rl_jsonl(self, tmp_dir):
        """正常的 RL 数据 (JSONL 格式)"""
        records = [
            {"prompt": [{"role": "user", "content": f"q{i}"}], "data_source": "ds"}
            for i in range(5)
        ]
        path = os.path.join(tmp_dir, "train.jsonl")
        _write_jsonl(path, records)

        result = check_verl_data_format(path, train_mode="ppo")
        assert result["is_valid"] is True
        assert result["total_samples"] == 5

    def test_missing_prompt_field(self, tmp_dir):
        """缺少 prompt 字段应报错"""
        data = [{"question": "hello", "answer": "world"}]
        path = os.path.join(tmp_dir, "bad.json")
        _write_json(path, data)

        result = check_verl_data_format(path, train_mode="grpo")
        assert result["is_valid"] is False
        assert any("prompt" in e for e in result["errors"])

    def test_prompt_wrong_type(self, tmp_dir):
        """prompt 字段不是列表应报错"""
        data = [{"prompt": "this is a string, not a list"}]
        path = os.path.join(tmp_dir, "bad.json")
        _write_json(path, data)

        result = check_verl_data_format(path, train_mode="grpo")
        assert result["is_valid"] is False
        assert any("列表" in e or "list" in e.lower() for e in result["errors"])

    def test_prompt_message_missing_role(self, tmp_dir):
        """prompt 消息中缺少 role 字段"""
        data = [{"prompt": [{"content": "hello"}]}]
        path = os.path.join(tmp_dir, "bad.json")
        _write_json(path, data)

        result = check_verl_data_format(path, train_mode="grpo")
        assert result["is_valid"] is False
        assert any("role" in e for e in result["errors"])

    def test_prompt_message_missing_content(self, tmp_dir):
        """prompt 消息中缺少 content 字段"""
        data = [{"prompt": [{"role": "user"}]}]
        path = os.path.join(tmp_dir, "bad.json")
        _write_json(path, data)

        result = check_verl_data_format(path, train_mode="grpo")
        assert result["is_valid"] is False
        assert any("content" in e for e in result["errors"])

    def test_missing_data_source_warning(self, tmp_dir):
        """缺少 data_source 字段应给出警告（不是错误）"""
        data = [{"prompt": [{"role": "user", "content": "q1"}]}]
        path = os.path.join(tmp_dir, "train.json")
        _write_json(path, data)

        result = check_verl_data_format(path, train_mode="grpo")
        assert result["is_valid"] is True
        assert any("data_source" in w for w in result["warnings"])

    def test_empty_file(self, tmp_dir):
        """空文件应报错"""
        path = os.path.join(tmp_dir, "empty.json")
        _write_json(path, [])

        result = check_verl_data_format(path, train_mode="grpo")
        assert result["is_valid"] is False
        assert any("空" in e for e in result["errors"])

    def test_file_not_exist(self):
        """文件不存在应报错"""
        result = check_verl_data_format("/nonexistent/path.json", train_mode="grpo")
        assert result["is_valid"] is False
        assert any("不存在" in e for e in result["errors"])

    def test_unsupported_format(self, tmp_dir):
        """不支持的文件格式"""
        path = os.path.join(tmp_dir, "data.csv")
        with open(path, "w") as f:
            f.write("a,b\n1,2\n")

        result = check_verl_data_format(path, train_mode="grpo")
        assert result["is_valid"] is False
        assert any("不支持" in e for e in result["errors"])


# ======================== SFT 模式 ========================

class TestVerlSFTDataCheck:
    """verl SFT 数据格式检查"""

    def test_valid_sft_data(self, tmp_dir):
        """正常的 SFT 数据"""
        data = [
            {"messages": [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好！"},
            ]},
        ]
        path = os.path.join(tmp_dir, "sft.json")
        _write_json(path, data)

        result = check_verl_data_format(path, train_mode="sft")
        assert result["is_valid"] is True
        assert result["verl_mode"] == "sft"

    def test_missing_messages_field(self, tmp_dir):
        """缺少 messages 字段"""
        data = [{"prompt": [{"role": "user", "content": "q1"}]}]
        path = os.path.join(tmp_dir, "bad_sft.json")
        _write_json(path, data)

        result = check_verl_data_format(path, train_mode="sft")
        assert result["is_valid"] is False
        assert any("messages" in e for e in result["errors"])

    def test_messages_wrong_type(self, tmp_dir):
        """messages 字段类型错误"""
        data = [{"messages": "not a list"}]
        path = os.path.join(tmp_dir, "bad_sft.json")
        _write_json(path, data)

        result = check_verl_data_format(path, train_mode="sft")
        assert result["is_valid"] is False

    def test_single_message_warning(self, tmp_dir):
        """只有一条消息应给出警告"""
        data = [{"messages": [{"role": "user", "content": "hi"}]}]
        path = os.path.join(tmp_dir, "sft.json")
        _write_json(path, data)

        result = check_verl_data_format(path, train_mode="sft")
        assert result["is_valid"] is True
        assert any("1" in w for w in result["warnings"])

    def test_unknown_train_mode(self, tmp_dir):
        """未知训练模式应报错"""
        data = [{"prompt": [{"role": "user", "content": "q1"}]}]
        path = os.path.join(tmp_dir, "data.json")
        _write_json(path, data)

        result = check_verl_data_format(path, train_mode="unknown_mode")
        assert result["is_valid"] is False
        assert any("未知" in e for e in result["errors"])


# ======================== 报告生成 ========================

class TestVerlFormatReport:
    """verl 格式检查报告生成"""

    def test_report_valid(self, tmp_dir):
        data = [{"prompt": [{"role": "user", "content": "q1"}], "data_source": "ds"}]
        path = os.path.join(tmp_dir, "train.json")
        _write_json(path, data)

        result = check_verl_data_format(path, train_mode="grpo")
        report = generate_verl_format_report(result)
        assert "通过" in report
        assert "grpo" in report

    def test_report_invalid(self, tmp_dir):
        data = [{"no_prompt": True}]
        path = os.path.join(tmp_dir, "bad.json")
        _write_json(path, data)

        result = check_verl_data_format(path, train_mode="grpo")
        report = generate_verl_format_report(result)
        assert "未通过" in report
        assert "修改建议" in report
