import { defineConfig } from 'vitepress'

export default defineConfig({
  base: process.env.DOCS_BASE ?? '/',
  title: 'LoopAI',
  description: 'LoopAI 使用文档与教程',
  lang: 'zh-CN',
  cleanUrls: true,
  lastUpdated: true,
  themeConfig: {
    logo: '/logo.svg',
    nav: [
      { text: '首页', link: '/' },
      { text: '快速开始', link: '/guide/quick-start' },
      { text: 'WebUI 教程', link: '/guide/webui-tutorial' },
      { text: 'TUI 教程', link: '/guide/tui-tutorial' }
    ],
    sidebar: [
      {
        text: '上手指南',
        items: [
          { text: '项目概览', link: '/' },
          { text: '快速开始', link: '/guide/quick-start' },
          { text: '可选环境', link: '/guide/optional-environments' },
          { text: '架构说明', link: '/guide/architecture' },
          { text: 'Node 设计', link: '/guide/agents' }
        ]
      },
      {
        text: 'WebUI 教程',
        items: [
          { text: 'WebUI 总览教程', link: '/guide/webui-tutorial' }
        ]
      },
      {
        text: 'TUI 教程',
        items: [
          { text: 'TUI 总览教程', link: '/guide/tui-tutorial' }
        ]
      },
      {
        text: '详细指南',
        items: [
          { text: 'Judger node 详细指南', link: '/guide/details/judger-agent' },
          { text: 'Analyzer node 详细指南', link: '/guide/details/analyzer-agent' },
          { text: 'ObtainerCLI/DataMixer 详细指南', link: '/guide/details/obtainer-agent' },
          { text: 'Webcrawler node 迁移说明', link: '/guide/details/webcrawler-agent' },
          { text: 'Trainer node 详细指南', link: '/guide/details/trainer-agent' }
        ]
      }
    ],
    socialLinks: [
      { icon: 'github', link: 'https://github.com/' }
    ],
    footer: {
      message: 'Built with VitePress for LoopAI',
      copyright: 'Copyright © LoopAI'
    },
    outline: {
      level: [2, 3],
      label: '本页导航'
    },
    docFooter: {
      prev: '上一页',
      next: '下一页'
    }
  }
})
