# `loopai.common`

`loopai.common` 是公共工具集合目录。

## 导入约定

推荐直接从子模块导入，不要依赖 `loopai.common.__init__` 转发符号。

推荐写法：

```python
from loopai.common.prompts import PromptLoader
from loopai.common.exception import emit_success, emit_error, ErrorCode
from loopai.common.db_tool import sqlite_db_session
from loopai.common.log_tool import StreamEvent, get_event_writer
```

## 为什么这样设计

- 避免 `import loopai.common` 时连带加载可选依赖
- 避免因为某个子模块缺依赖，导致整个 `common` 包无法导入
- 每个工具的依赖边界更清晰，调试时更容易定位问题

## 可用子模块

- `loopai.common.prompts`: Prompt 加载工具
- `loopai.common.exception`: 统一 success/error JSON 返回
- `loopai.common.db_tool`: SQLite 与配置表读写
- `loopai.common.log_tool`: 按 `context_id/agent_name` 写 pickle 事件数组
- `loopai.common.i18n`: 国际化工具
- `loopai.common.jsonl_dataset_sampling`: JSONL 数据采样工具

## 子模块文档

- [db_tool/README.md](/home/lpc/repos/Dataflow-LoopAI/loopai/common/db_tool/README.md)
- [exception/README.md](/home/lpc/repos/Dataflow-LoopAI/loopai/common/exception/README.md)
- [log_tool/README.md](/home/lpc/repos/Dataflow-LoopAI/loopai/common/log_tool/README.md)
