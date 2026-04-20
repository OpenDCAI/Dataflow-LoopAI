import { createRouter, createWebHashHistory } from 'vue-router'

import tool from "./tools";

import Manage from "./Manage";

const AsyncLoad = tool.AsyncLoad;

const router = createRouter({
    history: createWebHashHistory(import.meta.env.BASE_URL),
    routes: [
        {
            path: '/',
            name: 'home',
            component: AsyncLoad(() => import("@/views/client/home/index.vue")),
            meta: {
                title: "LoopAI"
            }
        },
        Manage
    ]
})

router.beforeEach((to, from, next) => {
    if (to.meta.title)
        document.title = to.meta.title
    next()
})

export default router
