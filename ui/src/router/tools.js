import { defineAsyncComponent } from 'vue'
import loading from "@/components/general/loading.vue";

const AsyncLoad = function (component) {
    return defineAsyncComponent({
        loader: component,
        loadingComponent: loading,
        errorComponent: loading,
        delay: 200,
        timeout: 10000
    });
};

export default {
    AsyncLoad
};
