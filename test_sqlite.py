# %%
import asyncio
from tortoise import Tortoise, fields
from tortoise.models import Model

# 定义模型
class User(Model):
    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=50)
    age = fields.IntField()

    def __str__(self):
        return f"User(id={self.id}, name={self.name}, age={self.age})"

# 主逻辑
async def main():
    # 1️⃣ 初始化 SQLite 数据库
    await Tortoise.init(
        db_url='sqlite://db.sqlite3',  # 数据库文件名
        modules={'models': ['__main__']}  # 指定模型模块
    )

    # 2️⃣ 自动创建表结构
    await Tortoise.generate_schemas()

    # 3️⃣ CRUD 操作示例
    # 插入数据
    user = await User.create(name="Alice", age=25)

    # 查询数据
    all_users = await User.all()
    print("All users:", all_users)

    # 更新
    user.age = 26
    await user.save()

    # 查询单个对象
    u = await User.get(name="Alice")
    print("Found user:", u)

    # 删除
    await u.delete()

    # 4️⃣ 关闭连接
    await Tortoise.close_connections()

# 启动
await main()
