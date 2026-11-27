import sys

import pytest

from cortai import db


@pytest.fixture(autouse=True)
def _banco_temporario(tmp_path_factory, monkeypatch):
    """Nenhum teste usa o banco real (storage/cortai.db): lá ficam as chaves de IA de quem roda os testes.

    Os módulos guardam o objeto `config` da primeira importação, então o caminho é trocado em cada um.
    Testes que criam o próprio banco no setUp continuam livres para sobrescrever.
    """
    db_file = str(tmp_path_factory.mktemp("banco") / "cortai.db")  # fora do tmp_path do teste
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("CORTAI_PREPARO", "0")  # a tela de preparação não intercepta a home nos testes
    for name, module in list(sys.modules.items()):
        if not name.startswith("cortai"):
            continue  # getattr em módulos de terceiros pode disparar imports preguiçosos
        cfg = getattr(module, "config", None)
        if hasattr(cfg, "DB_PATH"):
            monkeypatch.setattr(cfg, "DB_PATH", db_file)
    db.init_db(db_file)
