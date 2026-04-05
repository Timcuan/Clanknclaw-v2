class Supervisor:
    def __init__(self, workers: list[str]):
        self._workers = workers

    def worker_names(self) -> list[str]:
        return list(self._workers)
