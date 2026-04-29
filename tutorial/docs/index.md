---
layout: home

hero:
  name: "LoopAI"
  text: "面向 LLM 自主优化的闭环框架"
  tagline: "把评测、分析、数据获取、数据处理与训练串成一套真正可执行的优化工作流。"
  image:
    src: /logo.svg
    alt: LoopAI
  actions:
    - theme: brand
      text: 快速开始
      link: /guide/quick-start
    - theme: alt
      text: WebUI 教程
      link: /guide/webui-tutorial

features:
  - title: 从对话出发的优化流程
    details: 先和 Starter 对话，再进入 Judger、Analyzer、Obtainer、Constructor、Trainer 等子 Agent 执行具体任务。
  - title: 不只是评测，而是完整闭环
    details: LoopAI 覆盖评测、分析、数据获取、数据后处理和训练，适合做真正可持续迭代的模型优化。
  - title: WebUI 与终端双入口
    details: 既支持可视化演示和团队协作，也支持命令行单独调试整个系统或某个子 Agent。
  - title: 适配真实 GPU 环境
    details: 推理、分析和训练通常依赖不同运行时，因此文档也会说明如何拆分可选环境来配合使用。
---

## LoopAI 是什么

LoopAI 是一个围绕大语言模型持续优化而设计的闭环系统。它不是一个单独的 Agent，也不是一组松散脚本，而是一条从发现问题到修复问题的完整路径：

1. 评估当前模型表现。
2. 分析失败样本与问题模式。
3. 获取并处理更合适的数据。
4. 发起训练并进入下一轮验证。

这套方式尤其适合代码生成、领域问答、行业助手等需要长期迭代质量的场景。

## 建议阅读顺序

如果你是第一次接触 LoopAI，推荐按下面的顺序阅读：

- 先看 [快速开始](/guide/quick-start)，完成基础安装
- 再看 [可选环境](/guide/optional-environments)，理解为什么 Trainer 和评测会依赖额外环境
- 然后重点看 [WebUI 教程](/guide/webui-tutorial)，这是最适合第一次体验完整流程的入口
- 最后看 [终端教程](/guide/cli-tutorial)，用于脚本化调试与单独启动各个 Agent

## 这套文档现在怎么组织

这套教程现在按“先能跑，再会配，最后理解内部结构”的顺序组织，而不是按源码模块展开。这样更贴近第一次上手 LoopAI 时的真实体验。
