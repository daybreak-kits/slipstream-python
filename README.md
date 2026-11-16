# slipstream-python

驭流（Slipstream）生产网关（Python 数据面）。开发在 Windows 主仓库进行，本仓库承载生产运行与部署。

## 运行

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    .venv/bin/python -m uvicorn l07_bootstrap.app_factory:create_app --factory --app-dir src --host 127.0.0.1 --port 3680 --log-level info --timeout-keep-alive 75 --env-file .env.prd

- 入口：src/l07_bootstrap/app_factory.py（app.py 为兼容壳）
- 数据库：data/app.db（SQLite WAL；**不入库**，由 .gitignore 屏蔽）
- UI：web/dist 由网关单端口托管（/healthz 探活）
- 计费管线：USAGE_PIPELINE=memory（.env.prd）

## 同步流程

本地（Windows webdev 仓库）改完并验证后，同步 src/ tests/ scripts/ web/dist 与 requirements*.txt 到本仓库提交；data/ 与 .env.prd 永不提交。
