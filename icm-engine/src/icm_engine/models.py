from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Discriminator, Field, model_validator

_PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")


def _parse_date_strict(s: str) -> date:
    """Parse a date string. Raises ValueError on failure."""
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        from datetime import datetime as _dt
        try:
            return _dt.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {s!r}")


class Period:
    """YYYY-MM period with helpers for ordering and date boundaries."""

    def __init__(self, value: str) -> None:
        self.value = value
        year, month = value.split("-")
        self.year = int(year)
        self.month = int(month)

    @property
    def start_date(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def end_date(self) -> date:
        """Last day of this period's month (inclusive)."""
        if self.month == 12:
            return date(self.year, 12, 31)
        return date(self.year, self.month + 1, 1) - timedelta(days=1)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Period):
            return self.value == other.value
        if isinstance(other, str):
            return self.value == other
        return NotImplemented

    def __lt__(self, other: Period) -> bool:
        return (self.year, self.month) < (other.year, other.month)

    def __le__(self, other: Period) -> bool:
        return (self.year, self.month) <= (other.year, other.month)

    def __gt__(self, other: Period) -> bool:
        return (self.year, self.month) > (other.year, other.month)

    def __ge__(self, other: Period) -> bool:
        return (self.year, self.month) >= (other.year, other.month)

    def __hash__(self) -> int:
        return hash(self.value)

    def __repr__(self) -> str:
        return f"Period({self.value!r})"

    def __str__(self) -> str:
        return self.value


class Transaction(BaseModel):
    id: str
    payee_id: str
    deal_id: str = ""
    period: str = ""
    amount: Decimal  # may be negative (refunds/cancellations)
    product: str | None = None
    close_date: date | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    credits: list[Credit] | None = None
    # Margin-based commission (contract/temp staffing desks)
    bill_rate: Decimal | None = None
    pay_rate: Decimal | None = None
    units: Decimal = Decimal("1")
    margin: Decimal | None = None  # direct GP override
    quota_amount: Decimal | None = None  # bookings value for attainment; defaults to amount

    @property
    def quota_value(self) -> Decimal:
        """Amount this deal contributes to quota/attainment (defaults to `amount`).

        Decouples 'counts toward quota' from 'pays commission': set `quota_amount`
        to 0 for non-retiring deals (e.g. SPIF-only), or below `amount` for capped
        quota retirement. Payment is unaffected — rules still pay on `amount`/margin.
        """
        return self.quota_amount if self.quota_amount is not None else self.amount

    @property
    def margin_value(self) -> Decimal | None:
        """Gross profit used as commission base for margin-based plans.

        Priority: explicit `margin` field → computed (bill_rate - pay_rate) × units.
        Returns None when no margin data is present.
        """
        if self.margin is not None:
            return self.margin
        if self.bill_rate is not None and self.pay_rate is not None:
            return (self.bill_rate - self.pay_rate) * self.units
        return None

    @model_validator(mode="before")
    @classmethod
    def _parse_credits_str(cls, data: Any) -> Any:
        # Accept a raw credits string (CSV cell / XLSX mapping) and parse it.
        if isinstance(data, dict) and isinstance(data.get("credits"), str):
            data["credits"] = parse_credits_spec(data["credits"])
        return data

    @model_validator(mode="before")
    @classmethod
    def _derive_period(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        period = data.get("period")
        close_date = data.get("close_date")
        if period and str(period).strip():
            # Validate format but don't default
            p = str(period).strip()
            if not _PERIOD_RE.match(p):
                raise ValueError(f"period must be YYYY-MM format, got: {p!r}")
            data["period"] = p
        elif close_date is not None:
            if isinstance(close_date, date):
                data["period"] = close_date.strftime("%Y-%m")
            elif isinstance(close_date, str) and close_date.strip():
                d = _parse_date_strict(close_date.strip())
                data["period"] = d.strftime("%Y-%m")
                data["close_date"] = d
            else:
                raise ValueError("Transaction must have either period or close_date")
        else:
            raise ValueError("Transaction must have either period or close_date")
        return data

    @model_validator(mode="after")
    def _validate_credits(self) -> Transaction:
        if not self.credits:
            return self
        splits = [c for c in self.credits if c.kind == "split"]
        if splits:
            total = sum(c.split_pct for c in splits)
            if abs(total - Decimal("1.0")) > Decimal("1e-9"):
                raise ValueError(
                    f"Split credits must sum to 1.0, got {total} "
                    f"(from {[(c.payee_id, str(c.split_pct)) for c in splits]})"
                )
        return self

    def model_post_init(self, __context: Any) -> None:
        if not self.deal_id:
            self.deal_id = self.id


class Payee(BaseModel):
    id: str
    name: str
    quota: Decimal = Field(ge=Decimal("0"))
    quotas: dict[str, Decimal] = {}
    plan_id: str = ""
    effective_from: date | None = None
    effective_to: date | None = None
    email: str | None = None
    ramp: RampSchedule | None = None
    draw: Draw | None = None
    category_quotas: dict[str, Decimal] = Field(default_factory=dict)
    manager_id: str = ""
    manager_override: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("1"))
    team_id: str = ""

    def quota_for(self, window_key: str, category: str | None = None,
                  activity_fraction: Decimal = Decimal("1")) -> Decimal:
        """Return the quota for a given window key, falling back to default.

        If a category is provided and category_quotas is set, uses the
        category-specific quota (which may be a flat Decimal or a per-period dict).

        If a ramp schedule is active for this window, the quota is multiplied
        by the corresponding ramp multiplier.

        If activity_fraction < 1, the quota is pro-rated (partial periods).
        """
        if category and category in self.category_quotas:
            base = self.category_quotas[category]
        else:
            base = self.quotas.get(window_key, self.quota)
        if self.ramp and self.effective_from:
            mult = self._ramp_multiplier_for(window_key)
            if mult is not None:
                base = base * mult
        if activity_fraction != Decimal("1"):
            base = base * activity_fraction
        return base

    def _ramp_multiplier_for(self, window_key: str) -> Decimal | None:
        """Return the ramp multiplier for a given window, or None if not in ramp.

        Quarterly windows use the last month of the quarter as the reference
        month (Q2 → June). Annual windows use December.
        """
        if not self.ramp or not self.effective_from:
            return None

        year: int
        month: int
        if "-Q" in window_key:
            year_str, q_str = window_key.split("-Q")
            year = int(year_str)
            month = int(q_str) * 3  # last month of quarter
        elif "-" in window_key:
            yr_str, mo_str = window_key.split("-")
            year = int(yr_str)
            month = int(mo_str)
        else:
            # Annual window: e.g. "2026" → use December
            year = int(window_key)
            month = 12

        eff = self.effective_from
        months_since = (year - eff.year) * 12 + (month - eff.month)

        if months_since < 0:
            return Decimal("0")  # window before start date

        if months_since >= len(self.ramp.schedule):
            return None  # ramp period over, full quota applies

        return self.ramp.schedule[months_since]

    @model_validator(mode="before")
    @classmethod
    def _derive_defaults(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if "plan_id" not in data or not str(data.get("plan_id", "")).strip():
            data["plan_id"] = data.get("id", "default")
        if "effective_from" in data:
            ef = data["effective_from"]
            if isinstance(ef, str) and ef.strip():
                try:
                    data["effective_from"] = _parse_date_strict(ef.strip())
                except ValueError:
                    data["effective_from"] = date.today()
        if data.get("effective_from") is None:
            data["effective_from"] = date.today()
        return data


class Commission(BaseModel):
    transaction_id: str
    payee_id: str
    period: str
    origin_period: str = ""  # deal's close period when different from payout period
    rule_id: str
    base_amount: Decimal  # may be negative (refunds/clawbacks)
    rate: Decimal = Field(ge=Decimal("0"))
    commission_amount: Decimal
    split_pct: Decimal = Decimal("1")
    kind: str = "split"
    notes: str = ""


# --- Plan DSL models ---


class RampSchedule(BaseModel):
    """Quota-relief schedule for new hires.

    schedule[i] is the quota multiplier for calendar-month (i+1) on the job.
    After months elapse, the full (base or time-varying) quota applies.
    """

    months: int = Field(gt=0)
    schedule: list[Decimal]

    @model_validator(mode="after")
    def _check_length(self) -> RampSchedule:
        if len(self.schedule) != self.months:
            raise ValueError(
                f"Ramp schedule length ({len(self.schedule)}) "
                f"must equal months ({self.months})"
            )
        return self


class Credit(BaseModel):
    """A credit allocation: who gets what share of a deal, and how."""

    payee_id: str = Field(min_length=1)
    split_pct: Decimal = Field(gt=Decimal("0"), le=Decimal("1"))
    kind: Literal["split", "overlay"] = "split"


def parse_credits_spec(spec: str) -> list[Credit] | None:
    """Parse a credits cell from a CSV/XLSX file into Credit objects.

    Two forms are accepted:
    - JSON array:  [{"payee_id": "P1", "split_pct": "0.6"}, ...]
    - Compact:     P1:0.6;P2:0.4   or   P1:60%;P2:40%
      An entry may carry an '@overlay' suffix (P3:0.1@overlay) for overlay
      credits; everything else is a split.

    Returns None for blank input. Raises ValueError with a clear message on
    malformed input (never a raw decimal/JSON error).
    """
    s = spec.strip()
    if not s:
        return None
    if s.startswith("["):
        import json

        try:
            raw = json.loads(s)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid credits JSON: {e}") from e
        if not isinstance(raw, list):
            raise ValueError("Credits JSON must be an array of credit objects")
        return [Credit.model_validate(item) for item in raw]

    out: list[Credit] = []
    for part in s.split(";"):
        part = part.strip()
        if not part:
            continue
        kind: Literal["split", "overlay"] = "split"
        if part.endswith("@overlay"):
            kind = "overlay"
            part = part[: -len("@overlay")].strip()
        pid, sep, pct_raw = part.rpartition(":")
        pid = pid.strip()
        pct_s = pct_raw.strip()
        if not sep or not pid or not pct_s:
            raise ValueError(
                f"Invalid credits entry {part!r}: expected 'payee_id:share' "
                f"(e.g. 'P1:0.6;P2:0.4' or 'P1:60%;P2:40%')"
            )
        try:
            if pct_s.endswith("%"):
                pct = Decimal(pct_s[:-1].strip()) / Decimal("100")
            else:
                pct = Decimal(pct_s)
        except ArithmeticError:
            raise ValueError(
                f"Invalid credits share {pct_s!r} for payee {pid!r}"
            ) from None
        out.append(Credit(payee_id=pid, split_pct=pct, kind=kind))
    return out or None


class Tier(BaseModel):
    """A tier boundary: rate applies from the previous threshold up to this one."""

    threshold_pct: Decimal = Field(gt=Decimal("0"))
    rate: Decimal = Field(ge=Decimal("0"))


class FlatRateRule(BaseModel):
    type: Literal["flat_rate"]
    id: str
    filter: str | None = None
    rate: Decimal = Field(ge=Decimal("0"))
    cap: Decimal | None = Field(default=None, ge=Decimal("0"))
    min_attainment_pct: Decimal | None = Field(default=None, ge=Decimal("0"))
    quota_category: str | None = None
    base: Literal["amount", "margin"] = "amount"


class TieredRule(BaseModel):
    type: Literal["tiered"]
    id: str
    filter: str | None = None
    tiers: list[Tier]
    cap: Decimal | None = Field(default=None, ge=Decimal("0"))
    min_attainment_pct: Decimal | None = Field(default=None, ge=Decimal("0"))
    quota_category: str | None = None
    base: Literal["amount", "margin"] = "amount"

    @model_validator(mode="after")
    def _check_tiers_ascending(self) -> TieredRule:
        for i in range(1, len(self.tiers)):
            if self.tiers[i].threshold_pct <= self.tiers[i - 1].threshold_pct:
                raise ValueError(
                    f"Tiers must be sorted ascending by threshold_pct: "
                    f"tier {i} ({self.tiers[i].threshold_pct}) <= "
                    f"tier {i - 1} ({self.tiers[i - 1].threshold_pct})"
                )
        return self


class AcceleratorRule(BaseModel):
    type: Literal["accelerator"]
    id: str
    filter: str | None = None
    rate: Decimal = Field(ge=Decimal("0"))
    threshold_pct: Decimal = Field(gt=Decimal("0"))
    multiplier: Decimal = Field(gt=Decimal("0"))
    cap: Decimal | None = Field(default=None, ge=Decimal("0"))
    min_attainment_pct: Decimal | None = Field(default=None, ge=Decimal("0"))
    quota_category: str | None = None
    base: Literal["amount", "margin"] = "amount"


class FormulaRule(BaseModel):
    """Escape-hatch rule: an arbitrary arithmetic formula evaluated per credited
    transaction, for plans the built-in rule vocabulary can't express.

    The formula sees `amount`, `margin`, `quota`, `attainment_pct`, `bookings`,
    `product`, and any metadata column, plus min/max/abs/round/floor/ceil and
    if(cond, then, else). Syntax is validated at plan-load time; per-row
    evaluation failures (e.g. missing margin) skip that row with a ledger entry.
    """

    type: Literal["formula"]
    id: str
    filter: str | None = None
    formula: str = Field(min_length=1)
    cap: Decimal | None = Field(default=None, ge=Decimal("0"))
    min_attainment_pct: Decimal | None = Field(default=None, ge=Decimal("0"))
    quota_category: str | None = None
    # Chooses which attainment (revenue or gross profit) gates min_attainment_pct.
    base: Literal["amount", "margin"] = "amount"

    @model_validator(mode="after")
    def _check_formula_parses(self) -> FormulaRule:
        from icm_engine.formula import compile_formula

        compile_formula(self.formula)  # raises ValueError with position info
        return self


Rule = Annotated[
    FlatRateRule | TieredRule | AcceleratorRule | FormulaRule,
    Discriminator("type"),
]


class RoundingPolicy(BaseModel):
    """Display-boundary rounding for statements and API output.

    Applied only when presenting amounts — it never feeds back into the
    calculation, which always stays full-precision Decimal.
    """

    mode: Literal["half-up", "half-even", "floor", "ceil", "none"] = "half-up"
    places: int = Field(default=2, ge=0, le=6)


class PlanAssertion(BaseModel):
    """An executable invariant on a plan: a tiny scenario + the payout it must produce.

    Run via `icm check-plan` (or icm_engine.plan_check.check_plan) on every plan
    change, so a mistranscribed rate that silently underpays at quota fails loudly
    instead of at payroll time. The canonical OTE check is an assertion whose deals
    sum to the reference quota (100% attainment) with `expect_total` = the stated OTE.
    """

    name: str = Field(min_length=1)
    quota: Decimal = Field(gt=Decimal("0"))
    deals: list[Decimal] = Field(min_length=1)    # deal values for one synthetic payee
    expect_total: Decimal                          # expected total commission
    base: Literal["amount", "margin"] = "amount"   # treat the deal values as amount or margin
    period: str = Field(default="2026-01", pattern=r"^\d{4}-\d{2}$")
    tolerance: Decimal = Field(default=Decimal("0.01"), ge=Decimal("0"))


class Plan(BaseModel):
    plan_id: str
    name: str
    period_type: str = Field(pattern=r"^(monthly|quarterly|annual)$")
    # "period": attainment resets every window (the default, and v1 behaviour).
    # "cumulative": bookings and quota accumulate across the fiscal year, so a
    # rep who passes quota in August stays above it for the rest of the year.
    # Only affects rules that read attainment - tiered, accelerator, and any
    # min_attainment_pct gate. Flat-rate rules are unchanged.
    attainment_basis: Literal["period", "cumulative"] = "period"
    currency: str
    reporting_currency: str = ""  # blank = display in plan's currency (no conversion)
    rules: list[Rule] = Field(default_factory=list)
    payout_cap: Decimal | None = Field(default=None, ge=Decimal("0"))
    draw: Draw | None = None
    pro_rating: str = Field(default="full", pattern=r"^(full|daily|zero)$")
    rounding: RoundingPolicy | None = None  # None = exact (no display rounding)
    ote: Decimal | None = Field(default=None, ge=Decimal("0"))  # stated on-target earnings (metadata)
    assertions: list[PlanAssertion] = Field(default_factory=list)


# --- Manual adjustments ---


class ManualAdjustment(BaseModel):
    """A manual override added on top of calculated commission (not subject to caps/draws).

    Amount may be negative for clawbacks. Reason is required.
    """

    id: str = ""
    payee_id: str = Field(min_length=1)
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    amount: Decimal
    reason: str = Field(min_length=1)


# --- Draws / guarantees ---


class Draw(BaseModel):
    """Per-period draw/guarantee.

    - amount: the minimum payout per period (>= 0).
    - recoverable: if True, advances are recovered from future earnings.
      If False, the draw is a simple floor (non-recoverable guarantee).
    """

    amount: Decimal = Field(ge=Decimal("0"))
    recoverable: bool = False


# --- MBOs / bonuses ---


class MBO(BaseModel):
    """Non-commission payout — bonus, KPI incentive, or other period-level amount.

    Added to commission total before caps and draws. Does NOT affect attainment.
    """

    id: str = ""
    payee_id: str = Field(min_length=1)
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    amount: Decimal = Field(ge=Decimal("0"))
    label: str = ""
