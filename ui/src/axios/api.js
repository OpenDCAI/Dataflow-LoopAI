/* eslint-disable */
// More information: https://github.com/minskiter/openapijs
import axios from './config.js'
import * as Axios from 'axios'
import * as UserModel from './model.js'

// fix vite error.
const CancelTokenSource = Axios.CancelTokenSource;


export class config {
 
  /**
  * @summary 获取Starter配置
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async getConfig(cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/config/config',
        data:{},
        params:{},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary 更新Starter配置
  * @param {UserModel.ConfigModel} [configmodel] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async updateConfig(configmodel,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'post',
        url:'/config/config',
        data:configmodel,
        params:{},
        headers:{
          "Content-Type":"application/json"
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary 获取State的配置Schema
  * @param {String} [language] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async getStateSchema(language,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/config/state_schema',
        data:{},
        params:{language},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary 列出目录下的文件, 且判定是否为文件夹
  * @param {String} [path] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async listDir(path,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/config/list_dir',
        data:{},
        params:{path},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
}

// class config static method properties bind
/**
* @description getConfig url链接，包含baseURL
*/
config.getConfig.fullPath=`${axios.defaults.baseURL}/config/config`
/**
* @description getConfig url链接，不包含baseURL
*/
config.getConfig.path=`/config/config`
/**
* @description updateConfig url链接，包含baseURL
*/
config.updateConfig.fullPath=`${axios.defaults.baseURL}/config/config`
/**
* @description updateConfig url链接，不包含baseURL
*/
config.updateConfig.path=`/config/config`
/**
* @description getStateSchema url链接，包含baseURL
*/
config.getStateSchema.fullPath=`${axios.defaults.baseURL}/config/state_schema`
/**
* @description getStateSchema url链接，不包含baseURL
*/
config.getStateSchema.path=`/config/state_schema`
/**
* @description listDir url链接，包含baseURL
*/
config.listDir.fullPath=`${axios.defaults.baseURL}/config/list_dir`
/**
* @description listDir url链接，不包含baseURL
*/
config.listDir.path=`/config/list_dir`

export class responseProxy {
 
  /**
  * @summary Response proxy health
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async responseProxyHealth(cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/responseProxy/health',
        data:{},
        params:{},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary Proxy upstream models
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async responseProxyModels(cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/responseProxy/v1/models',
        data:{},
        params:{},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary Bridge responses API to chat completions
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async responseProxyResponses(cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'post',
        url:'/responseProxy/v1/responses',
        data:{},
        params:{},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
}

// class responseProxy static method properties bind
/**
* @description responseProxyHealth url链接，包含baseURL
*/
responseProxy.responseProxyHealth.fullPath=`${axios.defaults.baseURL}/responseProxy/health`
/**
* @description responseProxyHealth url链接，不包含baseURL
*/
responseProxy.responseProxyHealth.path=`/responseProxy/health`
/**
* @description responseProxyModels url链接，包含baseURL
*/
responseProxy.responseProxyModels.fullPath=`${axios.defaults.baseURL}/responseProxy/v1/models`
/**
* @description responseProxyModels url链接，不包含baseURL
*/
responseProxy.responseProxyModels.path=`/responseProxy/v1/models`
/**
* @description responseProxyResponses url链接，包含baseURL
*/
responseProxy.responseProxyResponses.fullPath=`${axios.defaults.baseURL}/responseProxy/v1/responses`
/**
* @description responseProxyResponses url链接，不包含baseURL
*/
responseProxy.responseProxyResponses.path=`/responseProxy/v1/responses`

export class starter {
 
  /**
  * @summary Submit codex prompt
  * @param {UserModel.StarterCodexRequest} [startercodexrequest] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async starterCodexStream(startercodexrequest,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'post',
        url:'/starter/codex/stream',
        data:startercodexrequest,
        params:{},
        headers:{
          "Content-Type":"application/json"
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary Get codex session state
  * @param {String} [pathsession_id] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async starterCodexSession(pathsession_id,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/starter/codex/session/'+pathsession_id+'',
        data:{},
        params:{},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary Stream codex session events
  * @param {String} [pathsession_id] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async starterCodexSessionStream(pathsession_id,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/starter/codex/session/'+pathsession_id+'/stream',
        data:{},
        params:{},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary Reset codex session history
  * @param {String} [pathsession_id] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async starterCodexSessionReset(pathsession_id,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'post',
        url:'/starter/codex/session/'+pathsession_id+'/reset',
        data:{},
        params:{},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary Get agent status by task_id
  * @param {String} [task_id] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async getAgentStatus(task_id,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/starter/agent/status',
        data:{},
        params:{task_id},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary Get the hardware usage
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async getHardwareUsage(cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/starter/agent/hardware_usage',
        data:{},
        params:{},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
}

// class starter static method properties bind
/**
* @description starterCodexStream url链接，包含baseURL
*/
starter.starterCodexStream.fullPath=`${axios.defaults.baseURL}/starter/codex/stream`
/**
* @description starterCodexStream url链接，不包含baseURL
*/
starter.starterCodexStream.path=`/starter/codex/stream`
/**
* @description starterCodexSession url链接，包含baseURL
*/
starter.starterCodexSession.fullPath=`${axios.defaults.baseURL}/starter/codex/session/{session_id}`
/**
* @description starterCodexSession url链接，不包含baseURL
*/
starter.starterCodexSession.path=`/starter/codex/session/{session_id}`
/**
* @description starterCodexSessionStream url链接，包含baseURL
*/
starter.starterCodexSessionStream.fullPath=`${axios.defaults.baseURL}/starter/codex/session/{session_id}/stream`
/**
* @description starterCodexSessionStream url链接，不包含baseURL
*/
starter.starterCodexSessionStream.path=`/starter/codex/session/{session_id}/stream`
/**
* @description starterCodexSessionReset url链接，包含baseURL
*/
starter.starterCodexSessionReset.fullPath=`${axios.defaults.baseURL}/starter/codex/session/{session_id}/reset`
/**
* @description starterCodexSessionReset url链接，不包含baseURL
*/
starter.starterCodexSessionReset.path=`/starter/codex/session/{session_id}/reset`
/**
* @description getAgentStatus url链接，包含baseURL
*/
starter.getAgentStatus.fullPath=`${axios.defaults.baseURL}/starter/agent/status`
/**
* @description getAgentStatus url链接，不包含baseURL
*/
starter.getAgentStatus.path=`/starter/agent/status`
/**
* @description getHardwareUsage url链接，包含baseURL
*/
starter.getHardwareUsage.fullPath=`${axios.defaults.baseURL}/starter/agent/hardware_usage`
/**
* @description getHardwareUsage url链接，不包含baseURL
*/
starter.getHardwareUsage.path=`/starter/agent/hardware_usage`

export class task {
 
  /**
  * @summary 更新任务项
  * @param {UserModel.TaskItem} [taskitem] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async updateTask(taskitem,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'put',
        url:'/task/task',
        data:taskitem,
        params:{},
        headers:{
          "Content-Type":"application/json"
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary 创建任务项
  * @param {UserModel.TaskItem} [taskitem] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async createTask(taskitem,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'post',
        url:'/task/task',
        data:taskitem,
        params:{},
        headers:{
          "Content-Type":"application/json"
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary 获取任务项
  * @param {String} [pathtask_id] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async getTask(pathtask_id,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/task/task/'+pathtask_id+'',
        data:{},
        params:{},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary 获取所有任务项
  * @param {String} [search] 
  * @param {Number} [offset] 
  * @param {Number} [limit] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async getTasks(search,offset,limit,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/task/list_tasks',
        data:{},
        params:{search,offset,limit},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary 获取任务State配置
  * @param {String} [pathtask_id] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async getTaskStateConfig(pathtask_id,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/task/task/'+pathtask_id+'/state_config',
        data:{},
        params:{},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary 更新任务State配置
  * @param {String} [pathtask_id] 
  * @param {UserModel.TaskStateConfigModel} [taskstateconfigmodel] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async updateTaskStateConfig(pathtask_id,taskstateconfigmodel,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'post',
        url:'/task/task/'+pathtask_id+'/state_config',
        data:taskstateconfigmodel,
        params:{},
        headers:{
          "Content-Type":"application/json"
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary 删除任务项
  * @param {String} [pathid] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async delTask(pathid,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'delete',
        url:'/task/task/'+pathid+'',
        data:{},
        params:{},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary 获取节点最新运行状态
  * @param {String} [pathtask_id] 
  * @param {String} [pathnode_name] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async getLatestTaskRuntime(pathtask_id,pathnode_name,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/task/task/runtime/'+pathtask_id+'/'+pathnode_name+'/latest',
        data:{},
        params:{},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary 获取节点历史运行状态
  * @param {String} [pathtask_id] 
  * @param {String} [pathnode_name] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async getTaskRuntimeHistory(pathtask_id,pathnode_name,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/task/task/runtime/'+pathtask_id+'/'+pathnode_name+'/history',
        data:{},
        params:{},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary 获取任务下所有节点最新运行状态
  * @param {String} [pathtask_id] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async getLatestTaskRuntimes(pathtask_id,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/task/task/runtime/'+pathtask_id+'/latest',
        data:{},
        params:{},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary 获取训练状态
  * @param {String} [output_dir] 
  * @param {String} [task_id] 
  * @param {String} [train_task_id] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async getTrainStatus(output_dir,task_id,train_task_id,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/task/train_status',
        data:{},
        params:{output_dir,task_id,train_task_id},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
}

// class task static method properties bind
/**
* @description updateTask url链接，包含baseURL
*/
task.updateTask.fullPath=`${axios.defaults.baseURL}/task/task`
/**
* @description updateTask url链接，不包含baseURL
*/
task.updateTask.path=`/task/task`
/**
* @description createTask url链接，包含baseURL
*/
task.createTask.fullPath=`${axios.defaults.baseURL}/task/task`
/**
* @description createTask url链接，不包含baseURL
*/
task.createTask.path=`/task/task`
/**
* @description getTask url链接，包含baseURL
*/
task.getTask.fullPath=`${axios.defaults.baseURL}/task/task/{task_id}`
/**
* @description getTask url链接，不包含baseURL
*/
task.getTask.path=`/task/task/{task_id}`
/**
* @description getTasks url链接，包含baseURL
*/
task.getTasks.fullPath=`${axios.defaults.baseURL}/task/list_tasks`
/**
* @description getTasks url链接，不包含baseURL
*/
task.getTasks.path=`/task/list_tasks`
/**
* @description getTaskStateConfig url链接，包含baseURL
*/
task.getTaskStateConfig.fullPath=`${axios.defaults.baseURL}/task/task/{task_id}/state_config`
/**
* @description getTaskStateConfig url链接，不包含baseURL
*/
task.getTaskStateConfig.path=`/task/task/{task_id}/state_config`
/**
* @description updateTaskStateConfig url链接，包含baseURL
*/
task.updateTaskStateConfig.fullPath=`${axios.defaults.baseURL}/task/task/{task_id}/state_config`
/**
* @description updateTaskStateConfig url链接，不包含baseURL
*/
task.updateTaskStateConfig.path=`/task/task/{task_id}/state_config`
/**
* @description delTask url链接，包含baseURL
*/
task.delTask.fullPath=`${axios.defaults.baseURL}/task/task/{id}`
/**
* @description delTask url链接，不包含baseURL
*/
task.delTask.path=`/task/task/{id}`
/**
* @description getLatestTaskRuntime url链接，包含baseURL
*/
task.getLatestTaskRuntime.fullPath=`${axios.defaults.baseURL}/task/task/runtime/{task_id}/{node_name}/latest`
/**
* @description getLatestTaskRuntime url链接，不包含baseURL
*/
task.getLatestTaskRuntime.path=`/task/task/runtime/{task_id}/{node_name}/latest`
/**
* @description getTaskRuntimeHistory url链接，包含baseURL
*/
task.getTaskRuntimeHistory.fullPath=`${axios.defaults.baseURL}/task/task/runtime/{task_id}/{node_name}/history`
/**
* @description getTaskRuntimeHistory url链接，不包含baseURL
*/
task.getTaskRuntimeHistory.path=`/task/task/runtime/{task_id}/{node_name}/history`
/**
* @description getLatestTaskRuntimes url链接，包含baseURL
*/
task.getLatestTaskRuntimes.fullPath=`${axios.defaults.baseURL}/task/task/runtime/{task_id}/latest`
/**
* @description getLatestTaskRuntimes url链接，不包含baseURL
*/
task.getLatestTaskRuntimes.path=`/task/task/runtime/{task_id}/latest`
/**
* @description getTrainStatus url链接，包含baseURL
*/
task.getTrainStatus.fullPath=`${axios.defaults.baseURL}/task/train_status`
/**
* @description getTrainStatus url链接，不包含baseURL
*/
task.getTrainStatus.path=`/task/train_status`

export class resource {
 
  /**
  * @summary 获取资源列表
  * @param {String} [search] 
  * @param {Number} [offset] 
  * @param {Number} [limit] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async getResource(search,offset,limit,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/resource/resource',
        data:{},
        params:{search,offset,limit},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary 创建资源
  * @param {String} [name] 
  * @param {String} [description] 
  * @param {String} [path] 
  * @param {String} [res_type] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async createResource(name,description,path,res_type,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'post',
        url:'/resource/resource',
        data:{},
        params:{name,description,path,res_type},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary 获取资源总数
  * @param {String} [search] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async getResourceCount(search,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/resource/resource/count',
        data:{},
        params:{search},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary 更新资源
  * @param {Number} [pathresource_id] 
  * @param {UserModel.ResourceItem} [resourceitem] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async updateResource(pathresource_id,resourceitem,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'put',
        url:'/resource/resource/'+pathresource_id+'',
        data:resourceitem,
        params:{},
        headers:{
          "Content-Type":"application/json"
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary 删除资源
  * @param {Number} [pathresource_id] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async deleteResource(pathresource_id,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'delete',
        url:'/resource/resource/'+pathresource_id+'',
        data:{},
        params:{},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary 预览资源
  * @param {String} [resource_id] 
  * @param {Number} [offset] 
  * @param {Number} [limit] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async previewResource(resource_id,offset,limit,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'post',
        url:'/resource/resource/preview',
        data:{},
        params:{resource_id,offset,limit},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
}

// class resource static method properties bind
/**
* @description getResource url链接，包含baseURL
*/
resource.getResource.fullPath=`${axios.defaults.baseURL}/resource/resource`
/**
* @description getResource url链接，不包含baseURL
*/
resource.getResource.path=`/resource/resource`
/**
* @description createResource url链接，包含baseURL
*/
resource.createResource.fullPath=`${axios.defaults.baseURL}/resource/resource`
/**
* @description createResource url链接，不包含baseURL
*/
resource.createResource.path=`/resource/resource`
/**
* @description getResourceCount url链接，包含baseURL
*/
resource.getResourceCount.fullPath=`${axios.defaults.baseURL}/resource/resource/count`
/**
* @description getResourceCount url链接，不包含baseURL
*/
resource.getResourceCount.path=`/resource/resource/count`
/**
* @description updateResource url链接，包含baseURL
*/
resource.updateResource.fullPath=`${axios.defaults.baseURL}/resource/resource/{resource_id}`
/**
* @description updateResource url链接，不包含baseURL
*/
resource.updateResource.path=`/resource/resource/{resource_id}`
/**
* @description deleteResource url链接，包含baseURL
*/
resource.deleteResource.fullPath=`${axios.defaults.baseURL}/resource/resource/{resource_id}`
/**
* @description deleteResource url链接，不包含baseURL
*/
resource.deleteResource.path=`/resource/resource/{resource_id}`
/**
* @description previewResource url链接，包含baseURL
*/
resource.previewResource.fullPath=`${axios.defaults.baseURL}/resource/resource/preview`
/**
* @description previewResource url链接，不包含baseURL
*/
resource.previewResource.path=`/resource/resource/preview`

export class obtainer {
 
  /**
  * @summary 获取 Obtainer 数据湖监控数据
  * @param {undefined} [lake] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async getObtainerLakeMonitor(lake,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/obtainer/lake/monitor',
        data:{},
        params:{lake},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary 探测 Obtainer embedding 服务状态
  * @param {undefined} [lake] 
  * @param {undefined} [timeout_seconds] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async getObtainerEmbeddingHealth(lake,timeout_seconds,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/obtainer/lake/embedding_health',
        data:{},
        params:{lake,timeout_seconds},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
}

// class obtainer static method properties bind
/**
* @description getObtainerLakeMonitor url链接，包含baseURL
*/
obtainer.getObtainerLakeMonitor.fullPath=`${axios.defaults.baseURL}/obtainer/lake/monitor`
/**
* @description getObtainerLakeMonitor url链接，不包含baseURL
*/
obtainer.getObtainerLakeMonitor.path=`/obtainer/lake/monitor`
/**
* @description getObtainerEmbeddingHealth url链接，包含baseURL
*/
obtainer.getObtainerEmbeddingHealth.fullPath=`${axios.defaults.baseURL}/obtainer/lake/embedding_health`
/**
* @description getObtainerEmbeddingHealth url链接，不包含baseURL
*/
obtainer.getObtainerEmbeddingHealth.path=`/obtainer/lake/embedding_health`

export class common {
 
  /**
  * @summary Root
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async root_info_get(cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/info',
        data:{},
        params:{},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
 
  /**
  * @summary Health Check
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async health_check_health_get(cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/health',
        data:{},
        params:{},
        headers:{
          "Content-Type":""
        },
        onUploadProgress:uploadProgress,
        onDownloadProgress:downloadProgress
      }
      // support wechat mini program
      if (cancelSource!=undefined){
        options.cancelToken = cancelSource.token
      }
      if (responseType != "json"){
        options.responseType = responseType;
      }
      axios(options)
      .then(res=>{
        if (res.config.responseType=="blob"){
          resolve(new Blob([res.data],{
            type: res.headers["content-type"].split(";")[0]
          }))
        }else{
          resolve(res.data);
          return res.data
        }
      }).catch(err=>{
        if (err.response){
          if (err.response.data)
            reject(err.response.data)
          else
            reject(err.response);
        }else{
          reject(err)
        }
      })
    })
  }
}

// class common static method properties bind
/**
* @description root_info_get url链接，包含baseURL
*/
common.root_info_get.fullPath=`${axios.defaults.baseURL}/info`
/**
* @description root_info_get url链接，不包含baseURL
*/
common.root_info_get.path=`/info`
/**
* @description health_check_health_get url链接，包含baseURL
*/
common.health_check_health_get.fullPath=`${axios.defaults.baseURL}/health`
/**
* @description health_check_health_get url链接，不包含baseURL
*/
common.health_check_health_get.path=`/health`
