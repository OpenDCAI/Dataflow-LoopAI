import axios from 'axios'

let ax = axios.create();

const devURL = '/api'
const prodURL = '/'

const getBaseURL = () => {
    if (process.env.NODE_ENV == 'production') {
        return prodURL;
    } else {
        return devURL;
    }
}

// config here
if (process.env.NODE_ENV == 'production') {
    ax.defaults.baseURL = prodURL;
} else {
    ax.defaults.baseURL = devURL;
}

// ax.interceptors.request.use(
//     config => {
//         if (
//             config.headers["Content-Type"].includes("x-www-form-urlencoded") ||
//             config.headers["Content-Type"].includes("multipart/form-data")
//         ) {
//             let formData = new FormData();
//             for (let item in config.data) {
//                 if (config.data[item]) {
//                     if (
//                         Array.isArray(config.data[item])
//                     ) {
//                         for (let i of config.data[item]) {
//                             formData.append(item, i);
//                         }
//                     }
//                     else formData.append(item, config.data[item]);
//                 }
//             }
//             config.data = formData;
//         }
//         return config;
//     },
//     error => {
//         return Promise.reject(error)
//     }
// )

export default ax;
export { devURL, prodURL, getBaseURL }
