export class AllSwanLabLogsResponse {
  
    /**
     *
     * @param {Number} total 
     * @param {Array} logs 
     */ 
    constructor(total = undefined,logs = undefined){
        this.total = total
        this.logs = logs
    }
       
    /**
     * 
     * @type {Number}
     */
    total=undefined   
    /**
     * 
     * @type {Array}
     */
    logs=undefined
    
}
export class Body_start_training_upload_train_upload_post {
  
    /**
     *
     * @param {String} file 
     */ 
    constructor(file = undefined,task_name = undefined){
        this.file = file
        this.task_name = task_name
    }
       
    /**
     * 
     * @type {String}
     */
    file=undefined
    
}
export class ConfigModel {
  
    /**
     *
     * @param {Number} id 
     * @param {String} config 
     */ 
    constructor(id = undefined,name = undefined,config = undefined){
        this.id = id
        this.name = name
        this.config = config
    }
       
    /**
     * 
     * @type {Number}
     */
    id=undefined   
    /**
     * 
     * @type {String}
     */
    config=undefined
    
}
export class HTTPValidationError {
  
    /**
     *
     * @param {Array} detail 
     */ 
    constructor(detail = undefined){
        this.detail = detail
    }
       
    /**
     * 
     * @type {Array}
     */
    detail=undefined
    
}
export class LogResponse {
  
    /**
     *
     * @param {String} task_id 
     * @param {String} logs 
     * @param {Number} total_lines 
     */ 
    constructor(task_id = undefined,logs = undefined,total_lines = undefined){
        this.task_id = task_id
        this.logs = logs
        this.total_lines = total_lines
    }
       
    /**
     * 
     * @type {String}
     */
    task_id=undefined   
    /**
     * 
     * @type {String}
     */
    logs=undefined   
    /**
     * 
     * @type {Number}
     */
    total_lines=undefined
    
}
export class SwanLabLogFolder {
  
    /**
     *
     * @param {String} folder_name 
     * @param {String} folder_path 
     * @param {String} created_at 
     */ 
    constructor(folder_name = undefined,folder_path = undefined,created_at = undefined){
        this.folder_name = folder_name
        this.folder_path = folder_path
        this.created_at = created_at
    }
       
    /**
     * 
     * @type {String}
     */
    folder_name=undefined   
    /**
     * 
     * @type {String}
     */
    folder_path=undefined   
    /**
     * 
     * @type {String}
     */
    created_at=undefined
    
}
export class SwanLabLogResponse {
  
    /**
     *
     * @param {String} task_id 
     */ 
    constructor(task_id = undefined,log_path = undefined,message = undefined){
        this.task_id = task_id
        this.log_path = log_path
        this.message = message
    }
       
    /**
     * 
     * @type {String}
     */
    task_id=undefined
    
}
export class TaskItem {
  
    /**
     *

     */ 
    constructor(id = undefined,task_id = undefined,name = undefined,config = undefined,state = undefined,createdAt = undefined,updatedAt = undefined){
        this.id = id
        this.task_id = task_id
        this.name = name
        this.config = config
        this.state = state
        this.createdAt = createdAt
        this.updatedAt = updatedAt
    }
    
    
}
export class TaskStatus {
  
    /**
     *

     */ 
    constructor(){
        
    }
    
    
}
export class TaskStatusResponse {
  
    /**
     *
     * @param {String} task_id 
     * @param {TaskStatus} status 
     * @param {String} created_at 
     */ 
    constructor(task_id = undefined,status = undefined,created_at = undefined,started_at = undefined,completed_at = undefined,error_message = undefined){
        this.task_id = task_id
        this.status = status
        this.created_at = created_at
        this.started_at = started_at
        this.completed_at = completed_at
        this.error_message = error_message
    }
       
    /**
     * 
     * @type {String}
     */
    task_id=undefined   
    /**
     * 
     * @type {TaskStatus}
     */
    status=undefined   
    /**
     * 
     * @type {String}
     */
    created_at=undefined
    
}
export class TrainRequest {
  
    /**
     *
     * @param {String} config 
     */ 
    constructor(config = undefined,task_name = undefined){
        this.config = config
        this.task_name = task_name
    }
       
    /**
     * 
     * @type {String}
     */
    config=undefined
    
}
export class TrainResponse {
  
    /**
     *
     * @param {String} task_id 
     * @param {TaskStatus} status 
     */ 
    constructor(task_id = undefined,status = undefined,message = undefined){
        this.task_id = task_id
        this.status = status
        this.message = message
    }
       
    /**
     * 
     * @type {String}
     */
    task_id=undefined   
    /**
     * 
     * @type {TaskStatus}
     */
    status=undefined
    
}
export class ValidationError {
  
    /**
     *
     * @param {Array} loc 
     * @param {String} msg 
     * @param {String} type 
     */ 
    constructor(loc = undefined,msg = undefined,type = undefined){
        this.loc = loc
        this.msg = msg
        this.type = type
    }
       
    /**
     * 
     * @type {Array}
     */
    loc=undefined   
    /**
     * 
     * @type {String}
     */
    msg=undefined   
    /**
     * 
     * @type {String}
     */
    type=undefined
    
}
