"""
配置生成工具
根据任务描述智能生成 LlamaFactory 训练配置
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Any, Optional
from langchain_openai import ChatOpenAI
from loopai.logger import get_logger
from loopai.common.prompts import PromptLoader

logger = get_logger()


class ConfigGenerator:
    """LlamaFactory 配置生成器"""
    
    def __init__(self, 
                 model_path: Optional[str] = None,
                 base_url: Optional[str] = None, 
                 api_key: Optional[str] = None,
                 temperature: float = 0.3,
                 top_p: float = 0.95):
        """
        初始化配置生成器
        
        Args:
            model_path: 大模型路径
            base_url: 模型服务base_url
            api_key: API密钥
            temperature: 生成温度
            top_p: top_p参数
        """
        self.default_template = self._get_default_template()
        self.model_path = model_path
        self.base_url = base_url
        self.api_key = api_key
        self.temperature = temperature
        self.top_p = top_p
        self.llm = None
        self.prompt_loader = PromptLoader()
        
        # 如果提供了模型参数，则初始化LLM
        if model_path and base_url and api_key:
            self._init_llm()
    
    def _init_llm(self):
        """初始化大模型"""
        try:
            self.llm = ChatOpenAI(
                model=self.model_path,
                api_key=self.api_key,
                base_url=self.base_url,
                temperature=self.temperature,
                top_p=self.top_p
            )
            logger.info(f"大模型初始化成功: {self.model_path}")
        except Exception as e:
            logger.error(f"大模型初始化失败: {str(e)}")
            self.llm = None
    
    def generate_config(
        self, 
        task_description: str,
        dataset_path: str,
        model_name: str = "qwen2.5-7b",
        output_dir: str = "./output",
        template_path: Optional[str] = None,
        use_swanlab: bool = True,
        swanlab_project: str = "llamafactory_training"
    ) -> Dict[str, Any]:
        """
        根据任务描述生成训练配置
        
        Args:
            task_description: 任务描述
            dataset_path: 数据集路径
            model_name: 基础模型名称
            output_dir: 输出目录
            template_path: 配置模板路径（可选）
            use_swanlab: 是否使用 SwanLab 监控
            swanlab_project: SwanLab 项目名称
        
        Returns:
            生成的配置字典
        """
        
        # 加载模板
        if template_path and os.path.exists(template_path):
            template = self._load_template(template_path)
        else:
            template = self.default_template.copy()
        
        # 根据任务描述调整配置
        config = self._customize_config(
            template=template,
            task_description=task_description,
            dataset_path=dataset_path,
            model_name=model_name,
            output_dir=output_dir,
            use_swanlab=use_swanlab,
            swanlab_project=swanlab_project
        )
        
        return config
    
    def save_config(self, config: Dict[str, Any], output_path: str) -> bool:
        """
        保存配置到文件
        
        Args:
            config: 配置字典
            output_path: 输出文件路径
        
        Returns:
            是否保存成功
        """
        try:
            output_dir = Path(output_path).parent
            output_dir.mkdir(parents=True, exist_ok=True)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            
            logger.info(f"配置文件已保存到: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"保存配置文件失败: {str(e)}")
            return False
    
    def _load_template(self, template_path: str) -> Dict[str, Any]:
        """加载配置模板"""
        try:
            with open(template_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"加载模板文件失败，使用默认模板: {str(e)}")
            return self.default_template.copy()
    
    def _customize_config(
        self,
        template: Dict[str, Any],
        task_description: str,
        dataset_path: str,
        model_name: str,
        output_dir: str,
        use_swanlab: bool,
        swanlab_project: str
    ) -> Dict[str, Any]:
        """根据任务描述定制配置"""
        
        config = template.copy()
        
        # 基础设置
        config["model_name"] = model_name
        config["dataset"] = self._get_dataset_name(dataset_path)
        config["output_dir"] = output_dir
        
        # 如果有大模型可用，使用大模型生成配置
        if self.llm:
            logger.info("使用大模型智能生成训练配置参数")
            llm_config = self._generate_config_with_llm(
                template, task_description, dataset_path, model_name
            )
            # 合并大模型生成的配置
            config.update(llm_config)
        else:
            logger.info("大模型不可用，使用规则式生成配置参数")
            # 使用原有的规则式方法
            config = self._customize_config_with_rules(
                config, task_description, use_swanlab, swanlab_project
            )
        
        return config
    
    def _generate_config_with_llm(
        self,
        template: Dict[str, Any],
        task_description: str,
        dataset_path: str,
        model_name: str
    ) -> Dict[str, Any]:
        """使用大模型生成配置参数"""
        
        try:
            # 构建提示词
            prompt = self._build_config_generation_prompt(
                task_description, dataset_path, model_name, template
            )
            
            # 调用大模型
            response = self.llm.invoke(prompt)
            config_text = response.content
            
            # 解析大模型返回的配置
            llm_config = self._parse_llm_config_response(config_text)
            
            logger.info("大模型配置生成成功")
            return llm_config
            
        except Exception as e:
            logger.error(f"大模型配置生成失败: {str(e)}")
            # 如果大模型失败，回退到规则式方法
            return {}
    
    def _build_config_generation_prompt(
        self,
        task_description: str,
        dataset_path: str,
        model_name: str,
        template: Dict[str, Any]
    ) -> str:
        """构建配置生成的提示词"""
        
        # 获取系统提示
        system_prompt = self.prompt_loader("system", "config_generation_prompt")
        
        # 分析数据集信息
        dataset_info = self._analyze_dataset_info(dataset_path)
        
        # 构建用户提示
        user_prompt = f"""
请根据以下信息为 LlamaFactory 训练生成最优的配置参数：

**任务描述：**
{task_description}

**模型信息：**
- 基础模型: {model_name}

**数据集信息：**
- 数据集路径: {dataset_path}
- 样本数量: {dataset_info.get('sample_count', '未知')}
- 数据格式: {dataset_info.get('format_type', '未知')}
- 平均长度: {dataset_info.get('avg_length', '未知')}

**当前配置模板：**
```json
{json.dumps(template, ensure_ascii=False, indent=2)}
```

请基于任务特点和数据特征，输出一个 JSON 格式的配置更新，只包含需要调整的参数：

```json
{{
  "learning_rate": 数值,
  "num_train_epochs": 数值,
  "per_device_train_batch_size": 数值,
  "gradient_accumulation_steps": 数值,
  "lora_r": 数值,
  "lora_alpha": 数值,
  "lora_target": "字符串",
  "cutoff_len": 数值,
  "warmup_ratio": 数值
}}
```
"""
        
        return system_prompt + "\n\n" + user_prompt
    
    def _analyze_dataset_info(self, dataset_path: str) -> Dict[str, Any]:
        """分析数据集基本信息"""
        
        info = {
            "sample_count": 0,
            "format_type": "unknown",
            "avg_length": 0
        }
        
        try:
            if dataset_path.endswith('.json'):
                with open(dataset_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        info["sample_count"] = len(data)
                        info["format_type"] = "json"
                        
                        # 计算平均长度
                        total_len = 0
                        for item in data[:10]:  # 只取前10个样本估算
                            if "conversations" in item:
                                for conv in item["conversations"]:
                                    total_len += len(conv.get("value", ""))
                            elif "instruction" in item:
                                total_len += len(item.get("instruction", ""))
                                total_len += len(item.get("output", ""))
                        
                        if len(data) > 0:
                            info["avg_length"] = total_len // min(10, len(data))
                            
            elif dataset_path.endswith('.jsonl'):
                count = 0
                total_len = 0
                
                with open(dataset_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip():
                            count += 1
                            if count <= 10:  # 只分析前10行
                                try:
                                    item = json.loads(line)
                                    if "conversations" in item:
                                        for conv in item["conversations"]:
                                            total_len += len(conv.get("value", ""))
                                    elif "instruction" in item:
                                        total_len += len(item.get("instruction", ""))
                                        total_len += len(item.get("output", ""))
                                except:
                                    pass
                
                info["sample_count"] = count
                info["format_type"] = "jsonl"
                if count > 0:
                    info["avg_length"] = total_len // min(10, count)
                    
        except Exception as e:
            logger.warning(f"分析数据集信息失败: {str(e)}")
        
        return info
    
    def _parse_llm_config_response(self, config_text: str) -> Dict[str, Any]:
        """解析大模型返回的配置"""
        
        try:
            # 尝试提取 JSON 内容
            import re
            json_match = re.search(r'```json\s*(\{.*?\})\s*```', config_text, re.DOTALL)
            if json_match:
                config_json = json_match.group(1)
            else:
                # 如果没有找到 JSON 代码块，尝试直接解析
                config_json = config_text.strip()
            
            # 解析 JSON
            config = json.loads(config_json)
            
            # 验证配置参数的合理性
            validated_config = self._validate_llm_config(config)
            
            return validated_config
            
        except Exception as e:
            logger.error(f"解析大模型配置响应失败: {str(e)}")
            logger.debug(f"原始响应: {config_text}")
            return {}
    
    def _validate_llm_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """验证大模型生成的配置参数合理性"""
        
        validated = {}
        
        # 学习率验证
        if "learning_rate" in config:
            lr = float(config["learning_rate"])
            if 1e-6 <= lr <= 1e-3:
                validated["learning_rate"] = lr
            else:
                logger.warning(f"学习率 {lr} 超出合理范围，使用默认值")
        
        # 训练轮数验证
        if "num_train_epochs" in config:
            epochs = int(config["num_train_epochs"])
            if 1 <= epochs <= 10:
                validated["num_train_epochs"] = epochs
        
        # 批次大小验证
        if "per_device_train_batch_size" in config:
            batch_size = int(config["per_device_train_batch_size"])
            if 1 <= batch_size <= 16:
                validated["per_device_train_batch_size"] = batch_size
        
        # 梯度累积步数验证
        if "gradient_accumulation_steps" in config:
            grad_acc = int(config["gradient_accumulation_steps"])
            if 1 <= grad_acc <= 32:
                validated["gradient_accumulation_steps"] = grad_acc
        
        # LoRA 参数验证
        if "lora_r" in config:
            lora_r = int(config["lora_r"])
            if 1 <= lora_r <= 64:
                validated["lora_r"] = lora_r
        
        if "lora_alpha" in config:
            lora_alpha = int(config["lora_alpha"])
            if 1 <= lora_alpha <= 128:
                validated["lora_alpha"] = lora_alpha
        
        if "lora_target" in config:
            validated["lora_target"] = str(config["lora_target"])
        
        # 序列长度验证
        if "cutoff_len" in config:
            cutoff_len = int(config["cutoff_len"])
            if 512 <= cutoff_len <= 8192:
                validated["cutoff_len"] = cutoff_len
        
        # 预热比例验证
        if "warmup_ratio" in config:
            warmup = float(config["warmup_ratio"])
            if 0.0 <= warmup <= 0.3:
                validated["warmup_ratio"] = warmup
        
        return validated
    
    def _customize_config_with_rules(
        self,
        config: Dict[str, Any],
        task_description: str,
        use_swanlab: bool,
        swanlab_project: str
    ) -> Dict[str, Any]:
        """使用规则式方法定制配置（备用方法）"""
        
        task_lower = task_description.lower()
        
        # 调整学习率
        if any(keyword in task_lower for keyword in ["数学", "推理", "复杂", "困难"]):
            config["learning_rate"] = 1e-5
        elif any(keyword in task_lower for keyword in ["对话", "聊天", "简单"]):
            config["learning_rate"] = 5e-5
        
        # 调整训练轮数
        if any(keyword in task_lower for keyword in ["微调", "适应", "few-shot"]):
            config["num_train_epochs"] = 1
        elif any(keyword in task_lower for keyword in ["从头", "完整", "全面"]):
            config["num_train_epochs"] = 5
        
        # LoRA 设置
        if config.get("finetuning_type") == "lora":
            if any(keyword in task_lower for keyword in ["代码", "编程", "code"]):
                config["lora_r"] = 16
                config["lora_alpha"] = 32
                config["lora_target"] = "all"
            elif any(keyword in task_lower for keyword in ["对话", "聊天", "chat"]):
                config["lora_r"] = 8
                config["lora_alpha"] = 16
                config["lora_target"] = "q_proj,v_proj"
        
        # SwanLab 监控设置
        if use_swanlab:
            config["report_to"] = ["swanlab"]
            config["run_name"] = f"llamafactory_{swanlab_project}"
        else:
            config["report_to"] = []
        
        return config
    
    def _get_dataset_name(self, dataset_path: str) -> str:
        """从数据集路径获取数据集名称"""
        return Path(dataset_path).stem
    
    def _get_default_template(self) -> Dict[str, Any]:
        """获取默认配置模板"""
        return {
            # 模型设置
            "model_name": "qwen2.5-7b-instruct",
            "model_revision": "main",
            
            # 数据设置
            "dataset": "custom_dataset",
            "dataset_dir": "./data",
            "template": "qwen",
            "cutoff_len": 2048,
            "max_samples": 10000,
            "overwrite_cache": True,
            "preprocessing_num_workers": 16,
            
            # 训练设置
            "stage": "sft",
            "do_train": True,
            "finetuning_type": "lora",
            "lora_target": "all",
            "lora_r": 8,
            "lora_alpha": 16,
            "lora_dropout": 0.1,
            "create_new_adapter": True,
            
            # 优化器设置
            "learning_rate": 1e-4,
            "num_train_epochs": 3,
            "max_grad_norm": 1.0,
            "per_device_train_batch_size": 2,
            "gradient_accumulation_steps": 8,
            "lr_scheduler_type": "cosine",
            "warmup_ratio": 0.1,
            
            # 评估设置
            "val_size": 0.1,
            "per_device_eval_batch_size": 1,
            "eval_strategy": "steps",
            "eval_steps": 100,
            
            # 保存设置
            "output_dir": "./output",
            "logging_steps": 10,
            "save_steps": 100,
            "save_total_limit": 3,
            "save_only_model": True,
            
            # 其他设置
            "fp16": True,
            "ddp_timeout": 180000000,
            "include_num_input_tokens_seen": True,
            "plot_loss": True,
            
            # 监控设置
            "report_to": ["swanlab"],
            "run_name": "llamafactory_training"
        }


def generate_config_explanation(config: Dict[str, Any], task_description: str) -> str:
    """生成配置说明文档"""
    
    explanation = []
    explanation.append("="*60)
    explanation.append("LlamaFactory 训练配置说明")
    explanation.append("="*60)
    explanation.append("")
    
    explanation.append(f"任务描述: {task_description}")
    explanation.append("")
    
    explanation.append("主要配置参数:")
    explanation.append(f"  模型名称: {config.get('model_name', 'N/A')}")
    explanation.append(f"  微调类型: {config.get('finetuning_type', 'N/A')}")
    explanation.append(f"  学习率: {config.get('learning_rate', 'N/A')}")
    explanation.append(f"  训练轮数: {config.get('num_train_epochs', 'N/A')}")
    explanation.append(f"  批次大小: {config.get('per_device_train_batch_size', 'N/A')}")
    explanation.append(f"  梯度累积步数: {config.get('gradient_accumulation_steps', 'N/A')}")
    explanation.append("")
    
    if config.get('finetuning_type') == 'lora':
        explanation.append("LoRA 配置:")
        explanation.append(f"  LoRA Rank: {config.get('lora_r', 'N/A')}")
        explanation.append(f"  LoRA Alpha: {config.get('lora_alpha', 'N/A')}")
        explanation.append(f"  LoRA Target: {config.get('lora_target', 'N/A')}")
        explanation.append("")
    
    if "swanlab" in config.get('report_to', []):
        explanation.append("SwanLab 监控:")
        explanation.append(f"  项目名称: {config.get('run_name', 'N/A')}")
        explanation.append(f"  日志步数: {config.get('logging_steps', 'N/A')}")
        explanation.append("")
    
    explanation.append("配置调整说明:")
    explanation.append("  - 使用大模型智能分析任务特征")
    explanation.append("  - 根据数据集规模自动调优")
    explanation.append("  - 基于任务复杂度优化参数")
    explanation.append("  - 自动验证参数合理性")
    
    return "\n".join(explanation)
