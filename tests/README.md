# 验证

预先准备 Python 3.14 和 `pip install -e .`，运行 `./tests/check.sh --strict`，Windows 使用 `./tests/check.ps1 -Strict`。也可直接执行 `python -m unittest discover -s tests/viewer -v`。

测试使用标准库 unittest，只使用临时声明与合成配置，覆盖共享目录、编辑、归档、应用、部署锁和 CLI 写入。原生 Linux/Windows 检查须在对应平台执行；其他平台夹具只验证逻辑。迁移后的 Viewer 不再运行旧安装器测试；安装验证位于 syncronizer。

可按功能单独运行测试。例如运行归档组可执行 `python -m unittest discover -s tests/viewer -p "test_archive.py" -v`。其余四组分别为 `test_catalog.py`、`test_editor.py`、`test_application.py` 和 `test_cli.py`。

实际运行记录见 [acceptance.md](acceptance.md)。
