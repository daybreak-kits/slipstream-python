# slipstream-python

驭流（Slipstream）生产网关（Python 数据面）。开发在 Windows 主仓库进行，本仓库承载生产运行与部署，目录布局与开发仓对齐（api/ 前缀），便于逐路径同步。

## 运行

    python3 -m venv .venv
    .venv/bin/pip install -r api/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    cd api cd api && ../.venv/bin/pythoncd api && ../.venv/bin/python /home/microsoft/slipstream-python/.venv/bin/python -m uvicorn l07_bootstrap.app_factory:create_app --factory --app-dir src --host 127.0.0.1 --port 3680 --log-level info --timeout-keep-alive 75 --env-file .env.prd

Windows 侧双击仓库根的 start-prod.bat 即可（等价于上面的 cd api && ...）。

- 入口：api/src/l07_bootstrap/app_factory.py（app.py 为兼容壳，工作目录必须是 api/）
- 数据库：api/data/app.db（SQLite WAL；**不入库**，.gitignore 屏蔽）
- UI：web/dist 由网关单端口托管（探活 GET /health）
- 计费管线：USAGE_PIPELINE=memory（api/.env.prd，永不提交）

## 同步流程

本地（Windows webdev 仓库）改完并验证后，按相对路径同步：api/src → api/src、api/tests → api/tests、api/scripts → api/scripts、web/dist → web/dist、api/requirements*.txt → api/。api/data/ 与 api/.env.prd 永不提交。
