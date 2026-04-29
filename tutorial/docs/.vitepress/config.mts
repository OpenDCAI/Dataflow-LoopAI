import { defineConfig } from 'vitepress'

export default defineConfig({
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
      { text: '终端教程', link: '/guide/cli-tutorial' }
    ],
    sidebar: [
      {
        text: '上手指南',
        items: [
          { text: '项目概览', link: '/' },
          { text: '快速开始', link: '/guide/quick-start' },
          { text: '可选环境', link: '/guide/optional-environments' },
          { text: 'WebUI 教程', link: '/guide/webui-tutorial' },
          { text: '终端教程', link: '/guide/cli-tutorial' },
          { text: '架构说明', link: '/guide/architecture' },
          { text: 'Agent 设计', link: '/guide/agents' }
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
