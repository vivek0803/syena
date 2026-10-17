from dataclasses import asdict, dataclass, field


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    data: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def first_failure(checks: list[Check]) -> Check | None:
    return next((c for c in checks if not c.passed), None)
