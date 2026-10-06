from argparse import Namespace

import pytest

from app import cli
from app.main import create_app
from tests.conftest import add_user, build_config


def test_create_admin_then_delete_user(tmp_path, monkeypatch, capsys):
    config = build_config(tmp_path, monkeypatch)
    cli.create_admin(Namespace(username="Boss", password="boss-password"))
    app = create_app(config)
    add_user(app, "temp", "temp-password")
    (config.jobs_dir / "orphan").mkdir(parents=True)

    cli.delete_user(Namespace(username="temp"))
    assert "已删除用户 temp" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.delete_user(Namespace(username="temp"))
