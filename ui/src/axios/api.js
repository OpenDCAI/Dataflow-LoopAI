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
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async getStateSchema(cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/config/state_schema',
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

export class starter {
 
  /**
  * @summary Start the agent
  * @param {String} [task_id] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async startAgent(task_id,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'post',
        url:'/starter/agent/start',
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
  * @summary Send input to the agent
  * @param {String} [text] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async agentInput(text,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'post',
        url:'/starter/agent/input',
        data:{},
        params:{text},
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
  * @summary Stop the agent
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async stopAgent(cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'post',
        url:'/starter/agent/stop',
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
  * @summary Get the agent status
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async getAgentStatus(cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/starter/agent/status',
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
 
  /**
  * @summary Get the agent messages
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async getAgentMessages(cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/starter/agent/messages',
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
  * @summary Get the agent message stream
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async getAgentMessageStream(cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/starter/agent/message/stream',
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
* @description startAgent url链接，包含baseURL
*/
starter.startAgent.fullPath=`${axios.defaults.baseURL}/starter/agent/start`
/**
* @description startAgent url链接，不包含baseURL
*/
starter.startAgent.path=`/starter/agent/start`
/**
* @description agentInput url链接，包含baseURL
*/
starter.agentInput.fullPath=`${axios.defaults.baseURL}/starter/agent/input`
/**
* @description agentInput url链接，不包含baseURL
*/
starter.agentInput.path=`/starter/agent/input`
/**
* @description stopAgent url链接，包含baseURL
*/
starter.stopAgent.fullPath=`${axios.defaults.baseURL}/starter/agent/stop`
/**
* @description stopAgent url链接，不包含baseURL
*/
starter.stopAgent.path=`/starter/agent/stop`
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
/**
* @description getAgentMessages url链接，包含baseURL
*/
starter.getAgentMessages.fullPath=`${axios.defaults.baseURL}/starter/agent/messages`
/**
* @description getAgentMessages url链接，不包含baseURL
*/
starter.getAgentMessages.path=`/starter/agent/messages`
/**
* @description getAgentMessageStream url链接，包含baseURL
*/
starter.getAgentMessageStream.fullPath=`${axios.defaults.baseURL}/starter/agent/message/stream`
/**
* @description getAgentMessageStream url链接，不包含baseURL
*/
starter.getAgentMessageStream.path=`/starter/agent/message/stream`

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
* @description delTask url链接，包含baseURL
*/
task.delTask.fullPath=`${axios.defaults.baseURL}/task/task/{id}`
/**
* @description delTask url链接，不包含baseURL
*/
task.delTask.path=`/task/task/{id}`
/**
* @description getTrainStatus url链接，包含baseURL
*/
task.getTrainStatus.fullPath=`${axios.defaults.baseURL}/task/train_status`
/**
* @description getTrainStatus url链接，不包含baseURL
*/
task.getTrainStatus.path=`/task/train_status`

export class train {
 
  /**
  * @summary Start Training
  * @param {UserModel.TrainRequest} [trainrequest] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async start_training_train__post(trainrequest,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'post',
        url:'/train/',
        data:trainrequest,
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
  * @summary Start Training Upload
  * @param {UserModel.Body_start_training_upload_train_upload_post} [body_start_training_upload_train_upload_post] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async start_training_upload_train_upload_post(body_start_training_upload_train_upload_post,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'post',
        url:'/train/upload',
        data:body_start_training_upload_train_upload_post,
        params:{},
        headers:{
          "Content-Type":"multipart/form-data"
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
  * @summary Get Task Status
  * @param {String} [pathtask_id] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async get_task_status_train_status__task_id__get(pathtask_id,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/train/status/'+pathtask_id+'',
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
  * @summary Get Task Logs
  * @param {String} [pathtask_id] 
  * @param {undefined} [max_lines] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async get_task_logs_train_logs__task_id__get(pathtask_id,max_lines,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/train/logs/'+pathtask_id+'',
        data:{},
        params:{max_lines},
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
  * @summary Get All Tasks
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async get_all_tasks_train_tasks_get(cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/train/tasks',
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
  * @summary Cancel Task
  * @param {String} [pathtask_id] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async cancel_task_train_tasks__task_id__delete(pathtask_id,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'delete',
        url:'/train/tasks/'+pathtask_id+'',
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
  * @summary Get Train Output Swanlab Log Path
  * @param {String} [pathtask_id] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async get_train_output_swanlab_log_path_train_swanlab_logs__task_id__get(pathtask_id,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/train/swanlab-logs/'+pathtask_id+'',
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
  * @summary Get All Swanlab Logs
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async get_all_swanlab_logs_train_swanlab_logs_get(cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/train/swanlab-logs',
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
  * @summary Get Task Metrics
  * @param {String} [pathtask_id] 
  * @param {undefined} [count] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async get_task_metrics_train_metrics__task_id__get(pathtask_id,count,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/train/metrics/'+pathtask_id+'',
        data:{},
        params:{count},
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
  * @summary Delete Task Metrics
  * @param {String} [pathtask_id] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async delete_task_metrics_train_metrics__task_id__delete(pathtask_id,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'delete',
        url:'/train/metrics/'+pathtask_id+'',
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
  * @summary Get Task Metrics File
  * @param {String} [pathtask_id] 
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async get_task_metrics_file_train_metrics__task_id__file_get(pathtask_id,cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/train/metrics/'+pathtask_id+'/file',
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

// class train static method properties bind
/**
* @description start_training_train__post url链接，包含baseURL
*/
train.start_training_train__post.fullPath=`${axios.defaults.baseURL}/train/`
/**
* @description start_training_train__post url链接，不包含baseURL
*/
train.start_training_train__post.path=`/train/`
/**
* @description start_training_upload_train_upload_post url链接，包含baseURL
*/
train.start_training_upload_train_upload_post.fullPath=`${axios.defaults.baseURL}/train/upload`
/**
* @description start_training_upload_train_upload_post url链接，不包含baseURL
*/
train.start_training_upload_train_upload_post.path=`/train/upload`
/**
* @description get_task_status_train_status__task_id__get url链接，包含baseURL
*/
train.get_task_status_train_status__task_id__get.fullPath=`${axios.defaults.baseURL}/train/status/{task_id}`
/**
* @description get_task_status_train_status__task_id__get url链接，不包含baseURL
*/
train.get_task_status_train_status__task_id__get.path=`/train/status/{task_id}`
/**
* @description get_task_logs_train_logs__task_id__get url链接，包含baseURL
*/
train.get_task_logs_train_logs__task_id__get.fullPath=`${axios.defaults.baseURL}/train/logs/{task_id}`
/**
* @description get_task_logs_train_logs__task_id__get url链接，不包含baseURL
*/
train.get_task_logs_train_logs__task_id__get.path=`/train/logs/{task_id}`
/**
* @description get_all_tasks_train_tasks_get url链接，包含baseURL
*/
train.get_all_tasks_train_tasks_get.fullPath=`${axios.defaults.baseURL}/train/tasks`
/**
* @description get_all_tasks_train_tasks_get url链接，不包含baseURL
*/
train.get_all_tasks_train_tasks_get.path=`/train/tasks`
/**
* @description cancel_task_train_tasks__task_id__delete url链接，包含baseURL
*/
train.cancel_task_train_tasks__task_id__delete.fullPath=`${axios.defaults.baseURL}/train/tasks/{task_id}`
/**
* @description cancel_task_train_tasks__task_id__delete url链接，不包含baseURL
*/
train.cancel_task_train_tasks__task_id__delete.path=`/train/tasks/{task_id}`
/**
* @description get_train_output_swanlab_log_path_train_swanlab_logs__task_id__get url链接，包含baseURL
*/
train.get_train_output_swanlab_log_path_train_swanlab_logs__task_id__get.fullPath=`${axios.defaults.baseURL}/train/swanlab-logs/{task_id}`
/**
* @description get_train_output_swanlab_log_path_train_swanlab_logs__task_id__get url链接，不包含baseURL
*/
train.get_train_output_swanlab_log_path_train_swanlab_logs__task_id__get.path=`/train/swanlab-logs/{task_id}`
/**
* @description get_all_swanlab_logs_train_swanlab_logs_get url链接，包含baseURL
*/
train.get_all_swanlab_logs_train_swanlab_logs_get.fullPath=`${axios.defaults.baseURL}/train/swanlab-logs`
/**
* @description get_all_swanlab_logs_train_swanlab_logs_get url链接，不包含baseURL
*/
train.get_all_swanlab_logs_train_swanlab_logs_get.path=`/train/swanlab-logs`
/**
* @description get_task_metrics_train_metrics__task_id__get url链接，包含baseURL
*/
train.get_task_metrics_train_metrics__task_id__get.fullPath=`${axios.defaults.baseURL}/train/metrics/{task_id}`
/**
* @description get_task_metrics_train_metrics__task_id__get url链接，不包含baseURL
*/
train.get_task_metrics_train_metrics__task_id__get.path=`/train/metrics/{task_id}`
/**
* @description delete_task_metrics_train_metrics__task_id__delete url链接，包含baseURL
*/
train.delete_task_metrics_train_metrics__task_id__delete.fullPath=`${axios.defaults.baseURL}/train/metrics/{task_id}`
/**
* @description delete_task_metrics_train_metrics__task_id__delete url链接，不包含baseURL
*/
train.delete_task_metrics_train_metrics__task_id__delete.path=`/train/metrics/{task_id}`
/**
* @description get_task_metrics_file_train_metrics__task_id__file_get url链接，包含baseURL
*/
train.get_task_metrics_file_train_metrics__task_id__file_get.fullPath=`${axios.defaults.baseURL}/train/metrics/{task_id}/file`
/**
* @description get_task_metrics_file_train_metrics__task_id__file_get url链接，不包含baseURL
*/
train.get_task_metrics_file_train_metrics__task_id__file_get.path=`/train/metrics/{task_id}/file`

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

export class common {
 
  /**
  * @summary Root
  * @param {CancelTokenSource} [cancelSource] Axios Cancel Source 对象，可以取消该请求
  * @param {Function} [uploadProgress] 上传回调函数
  * @param {Function} [downloadProgress] 下载回调函数
  */
  static async root__get(cancelSource,uploadProgress,downloadProgress){
    return await new Promise((resolve,reject)=>{
      let responseType = "json";
      let options = {
        method:'get',
        url:'/',
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
* @description root__get url链接，包含baseURL
*/
common.root__get.fullPath=`${axios.defaults.baseURL}/`
/**
* @description root__get url链接，不包含baseURL
*/
common.root__get.path=`/`
/**
* @description health_check_health_get url链接，包含baseURL
*/
common.health_check_health_get.fullPath=`${axios.defaults.baseURL}/health`
/**
* @description health_check_health_get url链接，不包含baseURL
*/
common.health_check_health_get.path=`/health`
