from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class AIUsageRecord:
    model: str
    task: str
    input_tokens: int
    output_tokens: int
    created_at: datetime

    @classmethod
    def now(cls, model: str, task: str, input_tokens: int, output_tokens: int):
        return cls(
            model=model,
            task=task,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            created_at=datetime.now(timezone.utc),
        )
