# 生产单源部署：网关直接托管前端构建产物

---
status: accepted
date: 2026-11-14
---

## 背景

生产环境若前端静态资源与 API 不同源，会同时招来 CORS、cookie 域、绝对路径三类问题，而且多一个需要单独部署与升级的单元。

## 决策

网关在挂载前端构建产物：`l07_bootstrap/app_factory.py:43 _mount_web_dist()` 挂 `web/dist`（目录由 `_WEB_DIST_DIR` 解析，:40），`/assets` 交给 `StaticFiles`（:56），其余路径走 SPA 兜底路由 `/{full_path:path}` 并做路径逃逸防护（:63 的 `is_relative_to` 校验）。

没有构建产物时（目录里没有 `index.html`）**不挂载**，API 行为不受影响——开发与测试环境因此不需要先构建前端。

## 理由

生产只需要一个进程、一个端口、一份部署单元；前端与 API 同源后，鉴权头、绝对路径、静态资源缓存都不再需要跨域妥协。

## 备选与否决原因

- **独立静态服务器 / 反向代理**：否决——多一个部署单元与一次版本对齐问题，收益仅在 CDN 场景。
- **开发期也挂载 dist**：否决——开发用 Vite dev server 代理即可，挂载反而让「改了前端但没重建」更难察觉。

## 后果与影响

- 构建产物必须先 `npm run build` 生成，且**不入版本库**；漏构建时网关只是不提供前端页面，API 照常工作。
- 本机构建有个坑：esbuild 在系统 TEMP 下会报 `Access is denied`，需要把 `TEMP`/`TMP` 指向仓库内目录再构建。
- 兜底路由是 SPA 通配，必须挂在 API 路由之后，避免把不存在的 `/api/...` 吞成前端页面。

## 相关代码与文档

- 代码：`api/src/l07_bootstrap/app_factory.py:40-66`。
- 测试：`api/tests/test_web_dist_mount.py`。