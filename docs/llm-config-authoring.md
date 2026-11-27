# 声明维护指南

先读 README、AGENTS、docs/configuration.md 和相关 TOML，再读 `src/resources/install/schema/` 的字段、方法、来源、发现和所有权定义。安装机制实现位于依赖本项目的 syncronizer，执行策略仍归该项目所有。

声明和 profile 是唯一来源；不维护应用正文。只迁移或修改已核实的软件身份、包名、来源、平台路径和排除项，不自动推断其他平台，不通过安装验证包身份。安装字段保持字符串，schema_version 为整数 1，profile 为 schema_version=3。排除为精确相对路径及子树，不是 glob。

修改后使用 `dotfile_viewer.api.load_catalog()` 校验全部平台图，运行 Viewer 隔离测试；安装语义变化另在 syncronizer 做当前主机 dry-run。不得用真实配置制作测试快照。删除 ID 时检查依赖和 profile 引用。记录执行过的检查及未覆盖的平台。
