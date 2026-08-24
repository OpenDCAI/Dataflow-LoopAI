import tool from "../tools";

const AsyncLoad = tool.AsyncLoad;

export default {
    path: "/m",
    component: AsyncLoad(() => import("@/views/manage/index.vue")),
    children: [
        {
            path: '',
            component: AsyncLoad(() => import("@/views/manage/loopaiFlow/index.vue"))
        },
        {
            path: 'config',
            component: AsyncLoad(() => import("@/views/manage/config/index.vue"))
        },
        {
            path: 'datamixer',
            component: AsyncLoad(() => import("@/views/manage/obtainerLake/index.vue"))
        },
        {
            path: 'datamixer/lakes',
            component: AsyncLoad(() => import("@/views/manage/obtainerLake/index.vue"))
        },
        {
            path: 'datamixer/datasets',
            component: AsyncLoad(() => import("@/views/manage/obtainerLake/index.vue"))
        },
        {
            path: 'obtainer-lake',
            redirect: '/m/datamixer'
        }
    ]
};
