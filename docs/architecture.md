# 架构与公共 API

Viewer 单向被 syncronizer 依赖。Viewer 持有声明及配置文件能力，syncronizer 持有安装执行能力。Python >=3.14，无第三方运行时依赖；CLI 与 Python API 共用编辑器、快照应用和部署锁实现。

`dotfile_viewer.api` 的 `API_VERSION=3` 是公共接口：

- `catalog_root(directory=None)`、`load_catalog(directory=None)`、`selection(...)` 定位并验证声明。
- `installation(...)`、`installation_roots(...)`、`select(...)` 供安装器读取规则与选择；不执行安装。
- `Editor(catalog=None, platform=None, roots=None, backup=None)` 提供 `configurations`、`files`、`read`、`save`。
- `export_snapshot(output, catalog=None, profile=None, software=None, platform=None, roots=None, dry=False)` 导出 manifest v2。
- `verify_archive(path)` 返回完整校验后的 Snapshot。
- `SnapshotSession(path, platform=None, roots=None, backup=None, protected=(), lease=None)` 是上下文管理器，固定归档到私有暂存区。`preview()` 返回目标动作及版本，`apply(dry=False, expected_version=None, detach=False, lease=None)` 重新检查并执行。安装器应在同一 session 中先安装、后应用。
- `deployment_lease(path=None, token=None, dry=False)` 返回显式嵌套所有权，既有 shell 包装器复用同一实现。外层持锁期间可用 `SnapshotSession(path, lease=lease)` 借用所有权，实际应用前验证令牌，释放由外层负责。

`CoreError` 表示可向用户解释的失败，`ConflictError` 表示版本冲突，`ConfirmationRequired` 表示需要硬链接独立保存确认。CLI 退出码：0 成功、2 参数/声明错误、3 访问或操作失败、4 冲突/缺少确认。

`manifest` 模块定义快照 v2 的平台与文档类型，校验软件选择闭包及条目树。`snapshot` 模块负责文件采集、ZIP 成员检查、载荷摘要和归档发布。

每次编辑器调用都重新加载并验证声明。列出全部配置时逐项探测文件状态，读取单项时只探测选中的配置。保存时在部署锁内读取一次声明，并用这份声明检查备份目录与全部适用的受管路径是否重叠。

编辑器分块读取目标文件，在同一次读取中计算版本并保留最多 2 MiB 的原始正文。读取结束时复核目标路径和文件身份。

文件保存使用读取时版本、备份、同目录暂存和原子替换。异常时只在目标仍匹配本次输出的情况下尝试恢复当前操作。之前成功文件不整体回滚。权限与文件属性按平台复制能力保留，不承诺 UID/GID、所有 ACL 或扩展属性的跨平台迁移。快照只保存可执行位，恢复为用户可读写并保留该位。

部署锁约束合作写入者，不能排除应用或手工写入。版本检查不是跨进程强制锁，不能承诺消除最后一次检查后的竞争；归档采集也不是多文件瞬时快照。不增加服务探测、软件版本锁定、断电持久性或自动故障恢复保证。

单次调用 `apply_snapshot()` 时，预览固定 ZIP 副本并流式校验全部内容，省去解压写盘，直接返回当次计划，实际应用在部署锁内生成计划。显式构造 `SnapshotSession` 时会先预检目标，之后每次预览或应用都会重新检查。

快照预览与应用按相同的父目录优先顺序处理条目。单文件原子替换成功后若读取或写报告失败，保存流程会核对目标身份、内容和元数据。失败报告的 `recovery` 字段记录本次处理结果。`restored` 表示恢复了原文件，`removed` 表示移除了本次新建的文件，`manual` 表示需要人工检查。命令错误包含报告和备份位置。

`install_schema` 提供安装声明规则和依赖排序。`install_config.read_items()` 负责 TOML 与旧 .conf 格式分派，原入口继续导出 `ROOT`、`rows`、`DeclarationError`、`InstallSchema` 和 `sort_items`。

会话拥有固定归档及解包暂存区。退出上下文或显式调用 `close()` 都会清理暂存区，重复关闭安全。关闭后仍可读取 `session.snapshot`，重新进入、预览和应用会立即抛出 `CoreError`。外层借出的部署租约由外层释放。

安装器可在开始安装前调用一次 `preflight(detach=...)`。它对每个目标观测一次，返回与 `preview()` 相同的计划；替换多硬链接文件需要显式确认。安装后，`apply()` 在租约内重新生成计划并先核对预期版本。只读的 `preview()` 和 `apply(dry=True)` 均可查看待确认项。

```python
from dotfile_viewer import api

with api.deployment_lease() as lease:
    with api.SnapshotSession(path, lease=lease) as session:
        plan = session.preflight(detach=detach)
        # 调用方在此执行安装。
        session.apply(detach=detach, expected_version=plan["version"])
```

`snapshot_catalog(snapshot)` 接受 `verify_archive()` 返回的文档或 `session.snapshot`。每次进入上下文都校验文档并生成独立的临时声明目录。调用方可以在上下文内把该路径传给安装器。目录的生命周期独立于会话暂存区，退出或写入失败时由 Viewer 清理。

```python
snapshot = api.verify_archive(path)
with api.snapshot_catalog(snapshot) as root:
    catalog = api.load_catalog(root)
    names = api.selection(root, profile="snapshot")
    install_roots = api.installation_roots(catalog, snapshot.platform.branch,
                                           snapshot.platform.environment, names)
```

生成的 profile 保留显式选择顺序或单独的 `all`，软件声明包含依赖闭包。

编辑器和 CLI 原文输出使用内部 `text_codec` 模块。读取时识别 UTF-8 BOM 和统一的 LF 或 CRLF，混合换行会返回不可编辑原因。保存时先规范化候选正文中的 CRLF，再按目标格式编码，2 MiB 上限按编码后的字节数计算。CLI `show` 从本次读取的正文和格式重建原始字节。

会话独占暂存载荷和已验证文档。每次读取 snapshot 返回独立副本，destination_roots 返回按声明映射顺序排列的不可变路径元组。两者在关闭后仍可读取。

导出预演与正式导出共用源树采集和复核流程。正式导出在同目录临时 ZIP 关闭并通过回读验证后以硬链接发布。临时文件由最外层清理，覆盖 ZIP 关闭失败的路径。

会话在租约中生成计划并核对版本与硬链接确认，随后逐项执行并原子更新批次报告。若应用失败后连失败报告也无法写入，API 在原异常的异常注释中保留报告路径及次生错误。CLI 同行显示两层诊断，并按原异常给出退出码。

部署锁入口收到令牌时验证 owner 并借用，缺少或失配的 owner 会使借用失败。只有无令牌的获取操作可创建锁。预演校验传入的凭据结构并跳过磁盘锁检查。

read_items 在每次调用内构造一份指定规则。TOML 读取在全平台校验中保留请求平台的排序结果，后续调用重新读取规则与声明。TOML 和旧 .conf 共用 schema_directory 参数。

Editor.files 使用显式栈，按父目录优先和兄弟名称顺序列出路径。目录枚举完成后才返回该路径的结果行。枚举失败会返回一条含 error 的 unavailable 记录，并继续浏览可访问的兄弟项。

manifest.json 的上限为 16 MiB，按未压缩 UTF-8 字节计算。预演与正式导出共用增量 JSON 编码，回读按同一上限检查。

快照两次源树盘点均用显式栈依次访问配置根和子项，按父目录优先、同级名称升序处理。必需路径缺失或不可读会中止采集。

edit 的外部草稿与 write 的文件及标准输入共用候选字节解码。解码保留原始换行字符，保存操作在版本检查后规范化 CRLF 并校验正文。

一次性入口 apply_snapshot(path, *, dry=False, detach=False, expected_version=None, lease=None, platform=None, roots=None, backup=None, protected=()) 与会话共用版本校验。例如 api.apply_snapshot(path, expected_version=plan["version"])。只有 None 省略检查。

显式选择可传单个 ID（如 api.selection(software="demo")），也可传有序列表、元组或迭代器。多个 ID 保留原始顺序，all 单独使用。profile 与 software 按是否传值互斥。
