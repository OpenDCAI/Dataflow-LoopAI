import { createRouter, createWebHashHistory } from 'vue-router'

import Manage from "./Manage";

const router = createRouter({
    history: createWebHashHistory(import.meta.env.BASE_URL),
    routes: [
        // the console is the product; there is no marketing page to land on
        {
            path: '/',
            redirect: '/m'
        },
        Manage,
        {
            path: '/:pathMatch(.*)*',
            redirect: '/m'
        }
    ]
})

router.beforeEach((to, from, next) => {
    if (to.meta.title) document.title = to.meta.title
    next()
})

export default router
