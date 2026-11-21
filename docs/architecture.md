# 架构与公共 API

Viewer 单向被 syncronizer 依赖。Viewer 持有声明及配置文件能力，syncronizer 持有安装执行能力。Python >=3.14，无第三方运行时依赖；CLI 与 Python API 共用编辑器、快照应用和部署锁实现。

`dotfile_viewer.api` 的 `API_VERSION=1` 是公共接口：

- `catalog_root(directory=None)`、`load_catalog(directory=None)`、`selection(...)` 定位并验证声明。
- `installation(...)`、`installation_roots(...)`、`select(...)` 供安装器读取规则与选择；不执行安装。
- `Editor(catalog=None, platform=None, roots=None, backup=None)` 提供 `configurations`、`files`、`read`、`save`。
- `export_snapshot(output, catalog=None, profile=None, software=None, platform=None, roots=None, dry=False)` 导出 manifest v2。
- `verify_archive(path)` 返回完整校验后的 Snapshot。
- `SnapshotSession(path, platform=None, roots=None, backup=None, protected=())` 是上下文管理器，固定归档到私有暂存区。`preview()` 返回目标动作及版本，`apply(dry=False, expected_version=None, detach=False, lease=None)` 重新检查并执行。安装器应在同一 session 中先安装、后应用。
- `deployment_lease(path=None, token=None, dry=False)` 返回显式嵌套所有权，既有 shell 包装器复用同一实现。

`CoreError` 表示可向用户解释的失败，`ConflictError` 表示版本冲突，`ConfirmationRequired` 表示需要硬链接独立保存确认。CLI 退出码：0 成功、2 参数/声明错误、3 访问或操作失败、4 冲突/缺少确认。

文件保存使用读取时版本、备份、同目录暂存和原子替换。异常时只在目标仍匹配本次输出的情况下尝试恢复当前操作。之前成功文件不整体回滚。权限与文件属性按平台复制能力保留，不承诺 UID/GID、所有 ACL 或扩展属性的跨平台迁移。快照只保存可执行位，恢复为用户可读写并保留该位。

部署锁约束合作写入者，不能排除应用或手工写入。版本检查不是跨进程强制锁，不能承诺消除最后一次检查后的竞争；归档采集也不是多文件瞬时快照。不增加服务探测、软件版本锁定、断电持久性或自动故障恢复保证。

快照预览与应用按相同的父目录优先顺序处理条目。单文件原子替换成功后若读取或写报告失败，保存流程会核对目标身份、内容和元数据。失败报告的 `recovery` 字段记录本次处理结果。`restored` 表示恢复了原文件，`removed` 表示移除了本次新建的文件，`manual` 表示需要人工检查。命令错误包含报告和备份位置。
