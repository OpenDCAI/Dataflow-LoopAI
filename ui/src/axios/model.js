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
export class DataMixerCliRequest {
  
    /**
     *

     */ 
    constructor(argv = undefined,line = undefined,files = undefined,lake = undefined,root = undefined,timeout_seconds = undefined){
        this.argv = argv
        this.line = line
        this.files = files
        this.lake = lake
        this.root = root
        this.timeout_seconds = timeout_seconds
    }
    
    
}
export class DataMixerLakeDeleteRequest {
  
    /**
     *
     * @param {undefined} delete_warehouse 
     * @param {undefined} yes 
     */ 
    constructor(link = undefined,delete_warehouse = undefined,yes = undefined){
        this.link = link
        this.delete_warehouse = delete_warehouse
        this.yes = yes
    }
       
    /**
     * 
     * @type {undefined}
     */
    delete_warehouse=undefined   
    /**
     * 
     * @type {undefined}
     */
    yes=undefined
    
}
export class DataMixerLakeLoadRequest {
  
    /**
     *
     * @param {String} warehouse 
     */ 
    constructor(warehouse = undefined,link = undefined,lake_root = undefined){
        this.warehouse = warehouse
        this.link = link
        this.lake_root = lake_root
    }
       
    /**
     * 
     * @type {String}
     */
    warehouse=undefined
    
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
export class ResourceItem {
  
    /**
     *

     */ 
    constructor(id = undefined,name = undefined,description = undefined,path = undefined,status = undefined,file_type = undefined,res_type = undefined,size = undefined,createdAt = undefined,updatedAt = undefined){
        this.id = id
        this.name = name
        this.description = description
        this.path = path
        this.status = status
        this.file_type = file_type
        this.res_type = res_type
        this.size = size
        this.createdAt = createdAt
        this.updatedAt = updatedAt
    }
    
    
}
export class StarterCodexRequest {
  
    /**
     *
     * @param {String} prompt 
     */ 
    constructor(prompt = undefined,workspace = undefined,session_id = undefined){
        this.prompt = prompt
        this.workspace = workspace
        this.session_id = session_id
    }
       
    /**
     * 
     * @type {String}
     */
    prompt=undefined
    
}
export class TaskItem {
  
    /**
     *

     */ 
    constructor(id = undefined,task_id = undefined,name = undefined,config = undefined,state = undefined,ai_thread_id = undefined,createdAt = undefined,updatedAt = undefined){
        this.id = id
        this.task_id = task_id
        this.name = name
        this.config = config
        this.state = state
        this.ai_thread_id = ai_thread_id
        this.createdAt = createdAt
        this.updatedAt = updatedAt
    }
    
    
}
export class TaskStateConfigModel {
  
    /**
     *

     */ 
    constructor(id = undefined,task_id = undefined,name = undefined,states = undefined){
        this.id = id
        this.task_id = task_id
        this.name = name
        this.states = states
    }
    
    
}
export class ValidationError {
  
    /**
     *
     * @param {Array} loc 
     * @param {String} msg 
     * @param {String} type 
     * @param {undefined} ctx 
     */ 
    constructor(loc = undefined,msg = undefined,type = undefined,input = undefined,ctx = undefined){
        this.loc = loc
        this.msg = msg
        this.type = type
        this.input = input
        this.ctx = ctx
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
    /**
     * 
     * @type {undefined}
     */
    ctx=undefined
    
}
