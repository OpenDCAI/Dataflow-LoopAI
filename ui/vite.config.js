import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vitejs.dev/config/
export default defineConfig({
    base: './',
    plugins: [
        vue(),
    ],
    resolve: {
        alias: {
            '@': fileURLToPath(new URL('./src', import.meta.url))
        }
    },
    css: {
        preprocessorOptions: {
            scss: {
                additionalData: `@use "@/style/global.scss" as *;`
            }
        }
    },
    server: {
        host: '0.0.0.0',
        port: 5174,
        proxy: {
            '/api': {
<<<<<<< HEAD
                target: 'http://localhost:8855/', // 后端 FastAPI 地址
=======
		    target: 'http://127.0.0.1:8855/', // 后端 FastAPI 地址
>>>>>>> xbr/FunctionOptimization/WebExploration
                changeOrigin: true,
                rewrite: path => path.replace(/^\/api/, '') // 如果后端没有 /api 前缀
            }
        }
    }
})
