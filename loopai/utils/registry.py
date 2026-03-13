from loopai.logger import get_logger

class Registry:
    def __init__(self, name):
        self.name = name
        self._registry = {}
        self.logger = get_logger()

    def register(self, name=None):
        """
        Use as a decorator @registry.register() 或 @registry.register("alias")
        """
        def decorator(obj):
            key = name or obj.__name__
            if key in self._registry:
                raise KeyError(f"{key} is already exists in {self.name}")
            self._registry[key] = obj
            return obj
        return decorator

    def get(self, name):
        """获取已注册的类/函数"""
        if name not in self._registry:
            self.logger.error(f"{name} hasn't been applied in {self.name}")
            raise KeyError(f"{name} hasn't been applied in {self.name}")
        return self._registry[name]

    def build(self, name, **kwargs):
        """实例化注册的类"""
        cls = self.get(name)
        return cls(**kwargs)

    def list(self):
        """列出所有注册项"""
        return list(self._registry.keys())
