from dataclasses import dataclass, field


@dataclass
class ParsedTxn:
    posted_date: str          # ISO date
    amount_cents: int         # negative = money out
    payee_raw: str
    memo: str | None
    fingerprint: str          # stable id used for de-duplication
    raw: dict = field(default_factory=dict)
