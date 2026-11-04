# 减脂网站的 Dockerfile（造箱子的说明书）
# 作用：把 Flask 网站连同它的运行环境打包成镜像，让 Fly 能直接在云端跑起来。
# 之前这个项目没有 Dockerfile，只有 RAG 项目有，所以我们新建一个，结构完全对齐。

# 基础镜像：Python 3.13 精简版（slim=去掉多余系统组件的轻量版，镜像更小）
FROM python:3.13-slim

# 容器内工作目录，后续 COPY / CMD 的路径都相对它
WORKDIR /app

# 先只拷依赖清单并安装 —— 关键的层缓存优化：
# requirements.txt 不变时，改代码重新 build 会直接复用「装依赖」这层，几秒完成
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再拷全部业务代码（app.py、templates/ 模板、static/ 静态资源）
COPY . .

# 声明端口（文档性质，真正开放靠 Fly 的 http_service 配置）
EXPOSE 5000

# 容器启动命令：用 gunicorn 绑定 0.0.0.0:5000 对外服务。
# "app:app" 的意思是：文件 app.py 里的 Flask 实例变量名 app。
# 不能用 Flask 自带的 app.run() 上生产，也绝对不能绑定 127.0.0.1（云端访问不到）。
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "app:app"]
