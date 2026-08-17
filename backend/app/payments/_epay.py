"""Administrator-managed New API compatible EPay gateway adapter."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from hashlib import md5
from hmac import compare_digest
from threading import RLock
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    insert,
    select,
    update,
)
from sqlalchemy.orm import Session, sessionmaker

from app.model_routing import ProviderSecrets
from app.orders import RechargeOrder
from app.payments.models import (
    InvalidPaymentNotification,
    InvalidPaymentSettings,
    PaymentCheckout,
    PaymentGatewayUnavailable,
    PaymentMethod,
    PaymentSettingsSnapshot,
    PaymentSettingsUpdate,
    RechargeQuote,
    RechargeRateSnapshot,
    UnsupportedPaymentMethod,
    VerifiedPaymentNotification,
)

_METHOD_CODE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_MONEY_QUANTUM = Decimal("0.01")
_CREDIT_QUANTUM = Decimal("0.0001")
_DEFAULT_CREDITS_PER_CNY = "1.0000"
_PRESET_PAYMENT_CNY = ("1.00", "2.00", "5.00", "10.00", "100.00")


@dataclass(frozen=True, slots=True)
class _RuntimeSettings:
    gateway_url: str
    public_base_url: str
    merchant_id: str
    merchant_key: str
    methods: tuple[PaymentMethod, ...]


class InMemoryEpayPayments:
    """Thread-safe EPay configuration and protocol implementation for tests."""

    def __init__(self) -> None:
        self._snapshot = _empty_snapshot()
        self._merchant_key = ""
        self._recharge_rate = RechargeRateSnapshot(_DEFAULT_CREDITS_PER_CNY, _PRESET_PAYMENT_CNY, None)
        self._lock = RLock()

    def current(self) -> PaymentSettingsSnapshot:
        """Return the administrator-safe current settings."""
        with self._lock:
            return self._snapshot

    def update(self, command: PaymentSettingsUpdate) -> PaymentSettingsSnapshot:
        """Validate settings and optionally rotate the in-memory key."""
        values = _validated(command)
        with self._lock:
            merchant_key = command.merchant_key.strip() or self._merchant_key
            if values.enabled and not merchant_key:
                raise InvalidPaymentSettings("启用支付前必须配置商户密钥")
            self._merchant_key = merchant_key
            self._snapshot = _snapshot(values, bool(merchant_key), datetime.now(UTC))
            return self._snapshot

    def available(self) -> tuple[PaymentMethod, ...]:
        """Expose methods only while a complete configuration is enabled."""
        snapshot = self.current()
        return snapshot.methods if snapshot.enabled and snapshot.configured else ()

    def current_recharge_rate(self) -> RechargeRateSnapshot:
        with self._lock:
            return self._recharge_rate

    def update_recharge_rate(self, credits_per_cny: str) -> RechargeRateSnapshot:
        normalized = _credits_per_cny(credits_per_cny)
        with self._lock:
            self._recharge_rate = RechargeRateSnapshot(normalized, _PRESET_PAYMENT_CNY, datetime.now(UTC))
            return self._recharge_rate

    def quote_recharge(self, payment_cny: str) -> RechargeQuote:
        return _quote_recharge(payment_cny, self.current_recharge_rate().credits_per_cny)

    def create_checkout(self, order: RechargeOrder) -> PaymentCheckout:
        """Build a signed EPay submit form for an immutable recharge order."""
        return _create_checkout(self._runtime(require_enabled=True), order)

    def verify_notification(self, parameters: Mapping[str, str]) -> VerifiedPaymentNotification:
        """Verify an EPay callback using the current merchant key."""
        return _verify_notification(self._runtime(require_enabled=False), parameters)

    def _runtime(self, *, require_enabled: bool) -> _RuntimeSettings:
        with self._lock:
            snapshot = self._snapshot
            merchant_key = self._merchant_key
        return _runtime_from_snapshot(snapshot, merchant_key, require_enabled=require_enabled)


_metadata = MetaData()
_settings = Table(
    "platform_payment_settings",
    _metadata,
    Column("settings_key", String(32), primary_key=True),
    Column("enabled", Boolean, nullable=False),
    Column("gateway_url", String(1024), nullable=False),
    Column("public_base_url", String(1024), nullable=False),
    Column("merchant_id", String(128), nullable=False),
    Column("merchant_key_secret_ref", String(1024), nullable=True),
    Column("methods_json", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=True),
)
_recharge_settings = Table(
    "platform_recharge_settings",
    _metadata,
    Column("settings_key", String(32), primary_key=True),
    Column("credits_per_cny_units", BigInteger, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=True),
)


class SqlAlchemyEpayPayments:
    """Persist EPay metadata in SQL while isolating the merchant key."""

    def __init__(self, sessions: sessionmaker[Session], secrets: ProviderSecrets) -> None:
        self._sessions = sessions
        self._secrets = secrets

    @classmethod
    def for_database_url(
        cls,
        database_url: str,
        secrets: ProviderSecrets,
        *,
        initialize_schema: bool = False,
    ) -> SqlAlchemyEpayPayments:
        """Create an adapter for tests or an already-migrated database."""
        engine = create_engine(database_url)
        if initialize_schema:
            _metadata.create_all(engine)
        return cls(sessionmaker(engine, expire_on_commit=False), secrets)

    def current(self) -> PaymentSettingsSnapshot:
        """Read current settings without resolving or returning the merchant key."""
        with self._sessions() as database:
            row = database.execute(
                select(_settings).where(_settings.c.settings_key == "global")
            ).mappings().one_or_none()
        return _snapshot_from_row(row) if row is not None else _empty_snapshot()

    def update(self, command: PaymentSettingsUpdate) -> PaymentSettingsSnapshot:
        """Persist validated metadata and rotate the isolated merchant key when supplied."""
        values = _validated(command)
        new_secret_ref: str | None = None
        old_secret_ref: str | None = None
        committed_secret_ref: str | None = None
        now = datetime.now(UTC)
        try:
            with self._sessions.begin() as database:
                row = database.execute(
                    select(_settings).where(_settings.c.settings_key == "global").with_for_update()
                ).mappings().one_or_none()
                old_secret_ref = str(row["merchant_key_secret_ref"]) if row and row["merchant_key_secret_ref"] else None
                secret_ref = old_secret_ref
                if command.merchant_key.strip():
                    stored = self._secrets.store(f"platform-epay-{uuid4()}", command.merchant_key)
                    new_secret_ref = stored.secret_ref
                    secret_ref = new_secret_ref
                if values.enabled and not secret_ref:
                    raise InvalidPaymentSettings("启用支付前必须配置商户密钥")
                committed_secret_ref = secret_ref
                payload = {
                    "enabled": values.enabled,
                    "gateway_url": values.gateway_url,
                    "public_base_url": values.public_base_url,
                    "merchant_id": values.merchant_id,
                    "merchant_key_secret_ref": secret_ref,
                    "methods_json": _methods_json(values.methods),
                    "updated_at": now,
                }
                if row is None:
                    database.execute(insert(_settings).values(settings_key="global", **payload))
                else:
                    database.execute(update(_settings).where(_settings.c.settings_key == "global").values(**payload))
        except BaseException:
            if new_secret_ref is not None:
                self._secrets.delete(new_secret_ref)
            raise
        if old_secret_ref is not None and old_secret_ref != committed_secret_ref:
            self._secrets.delete(old_secret_ref)
        return self.current()

    def available(self) -> tuple[PaymentMethod, ...]:
        """Expose methods only while a complete configuration is enabled."""
        snapshot = self.current()
        return snapshot.methods if snapshot.enabled and snapshot.configured else ()

    def current_recharge_rate(self) -> RechargeRateSnapshot:
        with self._sessions() as database:
            row = database.execute(
                select(_recharge_settings).where(_recharge_settings.c.settings_key == "global")
            ).mappings().one_or_none()
        if row is None:
            return RechargeRateSnapshot(_DEFAULT_CREDITS_PER_CNY, _PRESET_PAYMENT_CNY, None)
        updated_at = row["updated_at"]
        if isinstance(updated_at, datetime) and updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        return RechargeRateSnapshot(
            f"{Decimal(int(row['credits_per_cny_units'])) / 10_000:.4f}",
            _PRESET_PAYMENT_CNY,
            updated_at,
        )

    def update_recharge_rate(self, credits_per_cny: str) -> RechargeRateSnapshot:
        normalized = _credits_per_cny(credits_per_cny)
        units = int(Decimal(normalized) * 10_000)
        now = datetime.now(UTC)
        with self._sessions.begin() as database:
            row = database.execute(
                select(_recharge_settings).where(_recharge_settings.c.settings_key == "global").with_for_update()
            ).mappings().one_or_none()
            values = {"credits_per_cny_units": units, "updated_at": now}
            if row is None:
                database.execute(insert(_recharge_settings).values(settings_key="global", **values))
            else:
                database.execute(
                    update(_recharge_settings).where(_recharge_settings.c.settings_key == "global").values(**values)
                )
        return self.current_recharge_rate()

    def quote_recharge(self, payment_cny: str) -> RechargeQuote:
        return _quote_recharge(payment_cny, self.current_recharge_rate().credits_per_cny)

    def create_checkout(self, order: RechargeOrder) -> PaymentCheckout:
        """Build a signed EPay submit form with the isolated merchant key."""
        return _create_checkout(self._runtime(require_enabled=True), order)

    def verify_notification(self, parameters: Mapping[str, str]) -> VerifiedPaymentNotification:
        """Resolve the key only for callback verification."""
        return _verify_notification(self._runtime(require_enabled=False), parameters)

    def _runtime(self, *, require_enabled: bool) -> _RuntimeSettings:
        with self._sessions() as database:
            row = database.execute(
                select(_settings).where(_settings.c.settings_key == "global")
            ).mappings().one_or_none()
        if row is None:
            raise PaymentGatewayUnavailable("支付网关尚未配置")
        snapshot = _snapshot_from_row(row)
        secret_ref = str(row["merchant_key_secret_ref"] or "")
        merchant_key = self._secrets.read(secret_ref) if secret_ref else ""
        return _runtime_from_snapshot(snapshot, merchant_key, require_enabled=require_enabled)


def _validated(command: PaymentSettingsUpdate) -> PaymentSettingsUpdate:
    gateway_url = _https_url(command.gateway_url, allow_path=True, label="易支付网关地址")
    public_base_url = _https_url(command.public_base_url, allow_path=False, label="公开站点地址")
    merchant_id = command.merchant_id.strip()
    if not merchant_id or len(merchant_id) > 128:
        raise InvalidPaymentSettings("商户 ID 不能为空且最长为 128 个字符")
    methods: list[PaymentMethod] = []
    seen: set[str] = set()
    for method in command.methods:
        code = method.payment_provider.strip().casefold()
        display_name = method.display_name.strip()
        if not _METHOD_CODE.fullmatch(code):
            raise InvalidPaymentSettings("支付方式标识只能包含小写字母、数字、下划线或连字符")
        if not display_name or len(display_name) > 64:
            raise InvalidPaymentSettings("支付方式名称不能为空且最长为 64 个字符")
        if code in seen:
            raise InvalidPaymentSettings("支付方式标识不能重复")
        seen.add(code)
        methods.append(PaymentMethod(code, display_name))
    if not methods:
        raise InvalidPaymentSettings("至少需要配置一种支付方式")
    return PaymentSettingsUpdate(
        enabled=command.enabled,
        gateway_url=gateway_url,
        public_base_url=public_base_url,
        merchant_id=merchant_id,
        merchant_key=command.merchant_key,
        methods=tuple(methods),
    )


def _https_url(value: str, *, allow_path: bool, label: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    invalid_path = not allow_path and parsed.path not in {"", "/"}
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or invalid_path
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        path_rule = "且不能包含路径" if not allow_path else ""
        raise InvalidPaymentSettings(f"{label}必须是不含查询、片段或凭据的 HTTPS 地址{path_rule}")
    return normalized


def _snapshot(
    values: PaymentSettingsUpdate,
    merchant_key_configured: bool,
    updated_at: datetime | None,
) -> PaymentSettingsSnapshot:
    configured = bool(
        values.gateway_url
        and values.public_base_url
        and values.merchant_id
        and values.methods
        and merchant_key_configured
    )
    return PaymentSettingsSnapshot(
        configured=configured,
        enabled=values.enabled,
        gateway_url=values.gateway_url,
        public_base_url=values.public_base_url,
        merchant_id=values.merchant_id,
        merchant_key_configured=merchant_key_configured,
        methods=values.methods,
        updated_at=updated_at,
    )


def _snapshot_from_row(row: object) -> PaymentSettingsSnapshot:
    mapping = row
    updated_at = mapping["updated_at"]  # type: ignore[index]
    if isinstance(updated_at, datetime) and updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    methods = _methods_from_json(str(mapping["methods_json"]))  # type: ignore[index]
    values = PaymentSettingsUpdate(
        enabled=bool(mapping["enabled"]),  # type: ignore[index]
        gateway_url=str(mapping["gateway_url"]),  # type: ignore[index]
        public_base_url=str(mapping["public_base_url"]),  # type: ignore[index]
        merchant_id=str(mapping["merchant_id"]),  # type: ignore[index]
        methods=methods,
    )
    return _snapshot(values, bool(mapping["merchant_key_secret_ref"]), updated_at)  # type: ignore[index]


def _empty_snapshot() -> PaymentSettingsSnapshot:
    return PaymentSettingsSnapshot(False, False, "", "", "", False, (), None)


def _methods_json(methods: tuple[PaymentMethod, ...]) -> str:
    return json.dumps(
        [{"payment_provider": method.payment_provider, "display_name": method.display_name} for method in methods],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _methods_from_json(value: str) -> tuple[PaymentMethod, ...]:
    try:
        items = json.loads(value)
        return tuple(PaymentMethod(str(item["payment_provider"]), str(item["display_name"])) for item in items)
    except (KeyError, TypeError, ValueError) as exc:
        raise PaymentGatewayUnavailable("支付方式配置无法读取") from exc


def _runtime_from_snapshot(
    snapshot: PaymentSettingsSnapshot,
    merchant_key: str,
    *,
    require_enabled: bool,
) -> _RuntimeSettings:
    if (require_enabled and not snapshot.enabled) or not snapshot.configured or not merchant_key:
        raise PaymentGatewayUnavailable("支付网关尚未启用或配置不完整")
    return _RuntimeSettings(
        gateway_url=snapshot.gateway_url,
        public_base_url=snapshot.public_base_url,
        merchant_id=snapshot.merchant_id,
        merchant_key=merchant_key,
        methods=snapshot.methods,
    )


def _create_checkout(settings: _RuntimeSettings, order: RechargeOrder) -> PaymentCheckout:
    allowed = {method.payment_provider for method in settings.methods}
    if order.payment_provider not in allowed:
        raise UnsupportedPaymentMethod(order.payment_provider)
    parsed = urlsplit(settings.gateway_url)
    path = parsed.path.rstrip("/")
    if not path.endswith("/submit.php"):
        path = f"{path}/submit.php"
    action_url = urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    parameters = {
        "pid": settings.merchant_id,
        "type": order.payment_provider,
        "out_trade_no": order.order_id,
        "notify_url": f"{settings.public_base_url}/api/v1/payments/epay/notify",
        "return_url": f"{settings.public_base_url}/workspace/wallet",
        "name": "普通充值" if order.package_version_id is None else f"{order.package_code} 充值",
        "money": order.payment_cny,
        "device": "pc",
        "sign_type": "MD5",
    }
    parameters["sign"] = _signature(parameters, settings.merchant_key)
    return PaymentCheckout(action_url=action_url, method="POST", parameters=parameters)


def _verify_notification(
    settings: _RuntimeSettings,
    parameters: Mapping[str, str],
) -> VerifiedPaymentNotification:
    values = {str(key): str(value) for key, value in parameters.items()}
    signature = values.get("sign", "").casefold()
    if values.get("pid") != settings.merchant_id:
        raise InvalidPaymentNotification("支付通知商户 ID 不匹配")
    if values.get("sign_type", "").upper() != "MD5" or len(signature) != 32:
        raise InvalidPaymentNotification("支付通知签名类型无效")
    if not compare_digest(signature, _signature(values, settings.merchant_key)):
        raise InvalidPaymentNotification("支付通知验签失败")
    order_id = values.get("out_trade_no", "").strip()
    provider_event_id = values.get("trade_no", "").strip()
    payment_provider = values.get("type", "").strip().casefold()
    trade_status = values.get("trade_status", "").strip()
    if not order_id or not provider_event_id or not trade_status:
        raise InvalidPaymentNotification("支付通知缺少订单或交易字段")
    if not _METHOD_CODE.fullmatch(payment_provider):
        raise InvalidPaymentNotification("支付通知的支付方式无效")
    money = _money(values.get("money", ""))
    return VerifiedPaymentNotification(
        order_id=order_id,
        payment_provider=payment_provider,
        provider_event_id=provider_event_id,
        paid_payment_cny=money,
        trade_status=trade_status,
    )


def _money(value: str) -> str:
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise InvalidPaymentNotification("支付通知金额无效") from exc
    if not amount.is_finite() or amount <= 0 or amount.quantize(_MONEY_QUANTUM) != amount:
        raise InvalidPaymentNotification("支付通知金额无效")
    return f"{amount:.2f}"


def _credits_per_cny(value: str) -> str:
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise InvalidPaymentSettings("普通充值换算比例无效") from exc
    if (
        not amount.is_finite()
        or amount <= 0
        or amount > Decimal("1000000")
        or amount.quantize(_CREDIT_QUANTUM) != amount
    ):
        raise InvalidPaymentSettings("每 1 元兑换额度必须是 0.0001 至 1000000 之间、最多四位小数的正数")
    return f"{amount:.4f}"


def _quote_recharge(payment_cny: str, credits_per_cny: str) -> RechargeQuote:
    try:
        payment = Decimal(payment_cny)
    except InvalidOperation as exc:
        raise InvalidPaymentSettings("普通充值金额无效") from exc
    if (
        not payment.is_finite()
        or payment < Decimal("0.01")
        or payment > Decimal("1000000")
        or payment.quantize(_MONEY_QUANTUM) != payment
    ):
        raise InvalidPaymentSettings("普通充值金额必须是 0.01 至 1000000 元之间、最多两位小数的正数")
    rate = Decimal(_credits_per_cny(credits_per_cny))
    credits = (payment * rate).quantize(_CREDIT_QUANTUM, rounding=ROUND_HALF_UP)
    if credits <= 0:
        raise InvalidPaymentSettings("该充值金额按当前比例计算后不足 0.0001 额度")
    return RechargeQuote(f"{payment:.2f}", f"{credits:.4f}", f"{rate:.4f}")


def _signature(parameters: Mapping[str, str], merchant_key: str) -> str:
    filtered = {
        str(key): str(value)
        for key, value in parameters.items()
        if key not in {"sign", "sign_type"} and str(value) != ""
    }
    unsigned = "&".join(f"{key}={filtered[key]}" for key in sorted(filtered))
    return md5(f"{unsigned}{merchant_key}".encode(), usedforsecurity=False).hexdigest()
