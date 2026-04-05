from clankandclaw.core.supervisor import Supervisor


def test_supervisor_exposes_worker_names():
    supervisor = Supervisor(workers=["x", "gmgn", "pipeline", "telegram", "clanker"])
    assert supervisor.worker_names() == ["x", "gmgn", "pipeline", "telegram", "clanker"]
