import time
import threading
from multiprocessing import Process, Queue
from typing import Optional
from .worker import agent_worker as _worker_entry


class StarterManager:
    def __init__(self, sg_init_args: dict):
        self.cmd_q = Queue()
        self.state_q = Queue()

        self.process: Optional[Process] = None
        self.sg_init_args = sg_init_args
        self.cmd_cache = Queue()
        self.states = {}
        threading.Thread(target=self._state_cache_updater, daemon=True).start()
        threading.Thread(target=self._flush_cache_loop, daemon=True).start()
    
    def _state_cache_updater(self):
        while True:
            # 尝试读取 Manager.dict 非阻塞
            try:
                self.states = self.state_q.get_nowait()
            except:
                pass
            time.sleep(0.05)  # 每 50ms 更新一次
    
    def _flush_cache_loop(self):
        while True:
            try:
                # 取出缓存的消息
                msg = self.cmd_cache.get(timeout=0.05)
            except:
                continue

            # 尝试放到 multiprocessing.Queue
            while True:
                if not (self.process and self.process.is_alive()):
                    break  # agent 已经退出，丢掉消息

                try:
                    self.cmd_q.put(msg, timeout=0.05)
                    break  # 成功放入
                except:
                    continue  # queue 满了，重试


    def start(self, default_state: dict):
        self._kill_if_running()

        self.process = Process(
            target=_worker_entry,
            args=(self.cmd_q, self.state_q, self.sg_init_args),
            daemon=True
        )
        self.process.start()

        self.cmd_q.put({
            "type": "START",
            "default_state": default_state
        })

    def send_input(self, text: str):
        self.cmd_cache.put({
            "type": "INPUT",
            "text": text
        })

    def stop(self):
        if self.process and self.process.is_alive():
            self.cmd_q.put({"type": "STOP"})
            self.process.join(timeout=1)
            if self.process.is_alive():
                self.process.terminate()
            self.process = None

    def restart(self, query: str, default_state: dict):
        self.stop()
        self.start(query, default_state)

    def poll_state(self):
        return self.states

    def _kill_if_running(self):
        if self.process and self.process.is_alive():
            self.process.terminate()
            self.process.join()
