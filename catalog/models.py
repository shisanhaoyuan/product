from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator, model_validator
from urllib.parse import urlparse
import re


def safe_url(value: str) -> str:
    if not value:
        return ""
    u = urlparse(value)
    if u.scheme not in {"http", "https"} or not u.hostname or u.username or u.password:
        raise ValueError("只接受不含凭证的 HTTP/HTTPS 链接")
    return value


class Copy(BaseModel):
    title: str = ""
    description: str = ""
    features: list[str] = Field(default_factory=list)


class Variant(BaseModel):
    sku: str = Field(min_length=1, max_length=200)
    asin: str = ""
    source_url: str = ""
    seller: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)
    images: list[str] = Field(default_factory=list, max_length=100)
    price: str | None = None
    stock: str | None = None
    ean: str = ""
    copy: Copy = Field(default_factory=Copy)
    source_facts: dict[str, Any] = Field(default_factory=dict)
    price_check: dict[str, Any] = Field(default_factory=dict)
    stock_check: dict[str, Any] = Field(default_factory=dict)
    raw_cells: dict[str, Any] = Field(default_factory=dict)
    identity_note: str = ""

    @field_validator("asin")
    @classmethod
    def valid_asin(cls, v):
        if v and not re.fullmatch(r"[A-Z0-9]{10}", v):
            raise ValueError("ASIN 必须是 10 位大写字母或数字")
        return v

    @field_validator("source_url")
    @classmethod
    def valid_url(cls, v):
        return safe_url(v)

    @field_validator("images")
    @classmethod
    def valid_images(cls, values):
        return list(dict.fromkeys(safe_url(v) for v in values if v))


class Listing(BaseModel):
    external_id: str = Field(min_length=1, max_length=300)
    asin: str
    sku: str = Field(min_length=1, max_length=200)
    platform_sku: str = ""
    source: str = "Amazon"
    source_url: str = ""
    seller: str = ""
    store: str = Field(min_length=1)
    brand: str = ""
    template: str = Field(min_length=1)
    record_id: str = ""
    creator: str = ""
    uploaded_at: str = ""
    observed_at: str
    multiplier: str = ""
    evidence_kind: Literal["persisted_readback", "editor_snapshot", "source_only"]
    save_succeeded: bool = False
    fill_complete: bool = False
    readback_verified: bool = False
    copy: Copy = Field(default_factory=Copy)
    images: list[str] = Field(default_factory=list, max_length=100)
    price: str | None = None
    stock: str | None = None
    msrp: str | None = None
    ean: str = ""
    fields: dict[str, Any] = Field(default_factory=dict)
    management: list[dict[str, Any]] = Field(default_factory=list)
    variants: list[Variant] = Field(default_factory=list, max_length=5000)
    source_facts: dict[str, Any] = Field(default_factory=dict)
    issues: list[dict[str, Any]] = Field(default_factory=list)
    checks: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)

    _asin = field_validator("asin")(Variant.valid_asin.__func__)
    _url = field_validator("source_url")(Variant.valid_url.__func__)
    _images = field_validator("images")(Variant.valid_images.__func__)

    @field_validator("observed_at")
    @classmethod
    def timestamp(cls, v):
        if datetime.fromisoformat(v).tzinfo is None:
            raise ValueError("observed_at 必须包含时区")
        return v

    @model_validator(mode="after")
    def identities(self):
        skus = [v.sku for v in self.variants]
        if len(skus) != len(set(skus)):
            raise ValueError("同一模板内子 SKU 必须唯一")
        if self.evidence_kind != "persisted_readback" and (self.readback_verified or self.fill_complete):
            raise ValueError("未经持久化回读的快照不能声称验证通过或填写完整")
        if self.fill_complete and not (self.readback_verified and self.save_succeeded):
            raise ValueError("填写完整需要双保存和回读验证")
        return self


class ImportBundle(BaseModel):
    schema_version: Literal[1] = 1
    namespace: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    label: str = Field(min_length=1, max_length=200)
    listings: list[Listing] = Field(max_length=5000)
