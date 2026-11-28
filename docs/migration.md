# 从安装器迁移

本项目改名为 dotfile-viewer；本机 checkout 目录无需重命名。

GitHub 公开仓库从经过审核的 0.2.0 源码开始，首个提交没有父提交。旧仓库及其提交历史保留在私有仓库中。已有 checkout 如需跟踪公开版本，请按 README 克隆到新目录。

## 从 dotfiles-viewer 更名

发行包和 CLI 改为 `dotfile-viewer`，Python 导入及模块入口改为 `dotfile_viewer`。不保留旧命令、旧包或导入兼容层。syncronizer 的依赖和所有调用入口同步更新；两个项目须一并升级。

在各自运行环境中先执行 `python -m pip uninstall dotfiles-viewer`，再按 README 安装新包；仅安装新名称不会自动卸载旧发行包。公共 API 版本仍为 1，软件声明 v1、profile v3、快照 v2 均不变。

`DOTFILES_*` 环境变量、共享部署锁路径和默认 `dotfiles-viewer/backups` 状态目录保持不变，避免拆分锁或遗失既有备份位置。它们是持久数据和跨项目协议，不是旧名称的代码兼容层；不迁移或删除已有备份、journal。

## 既有安装器迁移

- 删除旧 `configs/`、安装、部署、镜像和 workflow 入口。仅删除仓库目录项，不部署、补齐、删除或改写本机实际配置。旧硬链接的其他路径继续存在。
- 软件声明与 profile 从 syncronizer 迁入 Viewer 的包资源。syncronizer 必须预先安装兼容的 Viewer 0.2.x 包，不能读取旧声明副本。
- Web UI、HTTP API 和 `serve` 命令已移除；配置编辑与快照操作使用 CLI 或公共 Python API。不再需要 FastAPI、Pydantic、AnyIO、Uvicorn 或 Web 测试依赖 HTTPX；开发安装改用 `pip install -e .`。
- 两项目最低 Python 版本改为 3.14，验证使用 3.14。既有虚拟环境须在运行外重新创建并安装依赖。
- 软件声明仍是 v1，profile 仍是 v3。快照格式改为 v2；不转换、不接受旧 v1 ZIP，请从本机实际文件重新导出。
- 不保留旧 journal 恢复命令。既有备份、journal 和会话不删除；保留旧版本的 Git 历史供人工查阅。
- syncronizer 的快照/配置命令作为兼容包装器，调用 Viewer；完整 restore 仍先安装再应用。Viewer 的 apply 只应用配置。

保存仍与其他路径共享硬链接的文件需要显式确认独立保存；不会悄悄更改其他链接。快照应用也使用相同确认规则。未知目标额外文件保留。

## 快照会话 API v2

使用 viewer.scratch 或 viewer.destinations 的 syncronizer 版本须先迁移，再配套发布。编排方用 destination_roots 检查报告目录重叠，用自己的临时目录保存编排上下文。租约在构造时绑定。

```python
from tempfile import TemporaryDirectory
from dotfile_viewer import api

with api.deployment_lease(dry=dry) as lease, api.SnapshotSession(path, lease=lease) as viewer:
    with api.snapshot_catalog(viewer.snapshot) as catalog, TemporaryDirectory() as context:
        targets = viewer.destination_roots
        plan = viewer.preview() if dry else viewer.preflight(detach=detach)
        # 编排方在此检查报告路径，并使用 catalog 和 context 预演或执行安装。
        result = viewer.apply(dry=dry, detach=detach)
```

显式或环境传入的非空部署令牌表示借用既有锁。外层释放后应丢弃凭据。向快照 API 传入 lease 时须同时提供 path 和 token，包括 dry-run。
