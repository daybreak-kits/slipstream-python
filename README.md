# slipstream-python

驭流（Slipstream）生产网关。开发在 Windows 主仓库（webdev/slipstream）进行，本仓库承载生产运行：git 只背源码与文档，构建产物不入库。

## 运行

    python3 -m venv .venv
    .venv/bin/pip install -r api/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    # UI（首次或前端变更后）
    cd web && npm install && npm run build && cd ..
    # 网关（工作目录必须是 api/）
    cd api && /home/microsoft/slipstream-python/.venv/bin/python -m uvicorn l07_bootstrap.app_factory:create_app --factory --app-dir src --host 127.0.0.1 --port 3680 --log-level info --timeout-keep-alive 75 --env-file .env.prd

Windows 侧双击仓库根的 start-prod.bat 等价于最后一步。

- 入口：api/src/l07_bootstrap/app_factory.py（app.py 为兼容壳）
- 数据库：api/data/app.db（SQLite WAL；不入库）
- UI：web/dist（构建产物，不入库）由网关单端口托管；探活 GET /health
- 计费管线：USAGE_PIPELINE=memory（api/.env.prd，永不提交）

## 同步流程

本地（Windows webdev/slipstream）改完并验证后按相对路径同步并提交：api/src ← api/src、api/tests、api/scripts、api/requirements*.txt、web/ ← web（不含 node_modules/dist），docs/ ← docs（不含 scratch）。生产机 git pull 后：前端变了就在 web/ 里 npm run build；然后重启网关（start-prod.bat）。api/data/ 与 api/.env.prd 永不提交。
