"""Deploy preparation pipeline for approved candidates."""

import json
import logging
import re
from datetime import datetime, timezone
from io import BytesIO
from time import perf_counter
from urllib.parse import urlparse

from clankandclaw.database.manager import DatabaseManager
from clankandclaw.deployers.clanker import ClankerDeployer
from clankandclaw.models.token import DeployRequest, SignalCandidate
from clankandclaw.utils.extraction import extract_token_identity
from clankandclaw.utils.image_fetcher import fetch_image_bytes
from clankandclaw.utils.ipfs import PinataClient

logger = logging.getLogger(__name__)

_NAME_ALLOWED_CHARS_RE = re.compile(r"[^A-Za-z0-9 ._-]+")
_SYMBOL_ALLOWED_CHARS_RE = re.compile(r"[^A-Z0-9]+")
_URL_RE = re.compile(r"https?://\S+")
_MAX_IMAGE_DIMENSION = 1024
_MIN_IMAGE_DIMENSION = 120
_IMAGE_BAD_HINTS = ("profile_images", "profile_banners", "avatar", "banner", "default_profile")
_IMAGE_EXT_HINTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")
_EVM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_PRIVATE_KEY_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")


class DeployPreparationError(Exception):
    """Error during deploy preparation."""


def _step_error(step: str, exc: Exception | str) -> DeployPreparationError:
    if isinstance(exc, Exception):
        return DeployPreparationError(f"{step}: {exc}")
    return DeployPreparationError(f"{step}: {exc}")


def _normalize_token_name(raw_name: str) -> str:
    name = re.sub(r"\s+", " ", raw_name.strip())
    name = _NAME_ALLOWED_CHARS_RE.sub("", name)
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) < 2:
        raise ValueError("normalized token_name is too short")
    return name[:50]


def _normalize_token_symbol(raw_symbol: str) -> str:
    symbol = raw_symbol.strip().upper()
    if symbol.startswith("$"):
        symbol = symbol[1:]
    symbol = _SYMBOL_ALLOWED_CHARS_RE.sub("", symbol)
    if len(symbol) < 2:
        raise ValueError("normalized token_symbol is too short")
    return symbol[:10]


def _build_context_excerpt(raw_text: str) -> str:
    """Build short natural context excerpt from raw source text."""
    text = _URL_RE.sub("", raw_text or "")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?i)\b(deploy|launch|token|symbol)\b", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -:,.")
    return text[:140]


def _build_natural_description(
    token_name: str,
    token_symbol: str,
    candidate: SignalCandidate,
    excerpt: str,
) -> str:
    source_hint = "social signal" if candidate.source == "x" else "new launch signal"
    base_sentence = (
        f"{token_name} ({token_symbol}) is a Base community token derived from a {source_hint}."
    )
    parts = [base_sentence]
    if candidate.author_handle:
        parts.append(f"Initial narrative was observed from @{candidate.author_handle}.")
    if excerpt:
        parts.append(f"Context: {excerpt}.")
    description = " ".join(parts).strip()
    return description[:280]


def _optimize_image_for_ipfs(raw_image: bytes) -> tuple[bytes, str, str]:
    """
    Normalize images for Pinata storage efficiency.
    Returns (content_bytes, filename, content_type).
    """
    try:
        from PIL import Image
        with Image.open(BytesIO(raw_image)) as img:
            img = img.convert("RGB")
            img.thumbnail((_MAX_IMAGE_DIMENSION, _MAX_IMAGE_DIMENSION))
            out = BytesIO()
            img.save(out, format="WEBP", quality=82, method=6)
            optimized = out.getvalue()
            if len(optimized) < len(raw_image):
                return optimized, "token_image.webp", "image/webp"
    except Exception:
        pass
    return raw_image, "token_image.png", "image/png"


def _normalized_text_tokens(value: str) -> set[str]:
    cleaned = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return {token for token in cleaned.split() if len(token) >= 2}


def _build_image_candidates(candidate: SignalCandidate, token_name: str, token_symbol: str) -> list[str]:
    metadata = candidate.metadata or {}
    urls: list[str] = []

    for key in ("image_url", "logo_url", "token_image_url", "icon_url"):
        value = metadata.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            urls.append(value)

    for key in ("image_candidates", "image_urls"):
        values = metadata.get(key)
        if isinstance(values, list):
            for value in values:
                if isinstance(value, str) and value.startswith(("http://", "https://")):
                    urls.append(value)

    for url in _URL_RE.findall(candidate.raw_text or ""):
        if url.lower().startswith(("http://", "https://")):
            urls.append(url.rstrip(").,;"))

    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        deduped.append(url)

    name_tokens = _normalized_text_tokens(token_name)
    symbol = token_symbol.lower().strip("$")

    def _score(url: str) -> int:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = (parsed.path or "").lower()
        full = f"{host}{path}"
        score = 0

        if any(path.endswith(ext) for ext in _IMAGE_EXT_HINTS):
            score += 8
        if symbol and symbol in full:
            score += 24
        if name_tokens and any(token in full for token in name_tokens):
            score += 16
        if "ipfs" in full:
            score += 6
        if "logo" in full:
            score += 8
        if candidate.source in {"x", "farcaster"} and any(hint in full for hint in _IMAGE_BAD_HINTS):
            score -= 50
        return score

    return sorted(deduped, key=_score, reverse=True)


def _is_image_content_plausible(raw_image: bytes, image_url: str, source: str) -> bool:
    lower_url = image_url.lower()
    if source in {"x", "farcaster"} and any(hint in lower_url for hint in _IMAGE_BAD_HINTS):
        return False

    try:
        from PIL import Image
        with Image.open(BytesIO(raw_image)) as img:
            width, height = img.size
            if max(width, height) < _MIN_IMAGE_DIMENSION:
                return False
            return True
    except Exception:
        return True


def _build_placeholder_image(token_symbol: str) -> tuple[bytes, str, str]:
    """Build deterministic fallback image so deploy can proceed without wrong context image."""
    import hashlib

    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        symbol = _normalize_token_symbol(token_symbol) if token_symbol else "TKN"
        digest = hashlib.sha256(symbol.encode()).hexdigest()
        color_a = f"#{digest[:6]}"
        color_b = f"#{digest[6:12]}"
        svg = (
            f"<svg xmlns='http://www.w3.org/2000/svg' width='512' height='512'>"
            f"<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
            f"<stop offset='0%' stop-color='{color_a}'/><stop offset='100%' stop-color='{color_b}'/>"
            f"</linearGradient></defs>"
            f"<rect width='512' height='512' fill='url(#g)'/>"
            f"<text x='50%' y='52%' dominant-baseline='middle' text-anchor='middle' "
            f"font-size='96' fill='white' font-family='Arial, sans-serif'>{symbol[:6]}</text>"
            f"</svg>"
        )
        return svg.encode("utf-8"), "token_image.svg", "image/svg+xml"

    symbol = _normalize_token_symbol(token_symbol) if token_symbol else "TKN"
    digest = hashlib.sha256(symbol.encode()).hexdigest()
    c1 = tuple(int(digest[i : i + 2], 16) for i in (0, 2, 4))
    c2 = tuple(int(digest[i : i + 2], 16) for i in (6, 8, 10))

    size = 512
    image = Image.new("RGB", (size, size), c1)
    draw = ImageDraw.Draw(image)
    for y in range(size):
        ratio = y / max(size - 1, 1)
        color = tuple(int((1 - ratio) * c1[i] + ratio * c2[i]) for i in range(3))
        draw.line((0, y, size, y), fill=color)

    font = ImageFont.load_default()
    text = symbol[:6]
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text(((size - tw) / 2, (size - th) / 2), text, fill=(255, 255, 255), font=font)

    out = BytesIO()
    image.save(out, format="WEBP", quality=86, method=6)
    return out.getvalue(), "token_image.webp", "image/webp"


class DeployPreparation:
    """Handles preparation of approved candidates for deployment."""

    def __init__(
        self,
        db: DatabaseManager,
        pinata_client: PinataClient,
        deployer: ClankerDeployer,
        signer_wallet: str,
        token_admin: str,
        fee_recipient: str,
        tax_bps: int = 1000,
        clanker_fee_bps: int | None = None,
        paired_fee_bps: int | None = None,
        token_admin_enabled: bool = True,
        token_reward_enabled: bool = True,
    ):
        self.db = db
        self.pinata = pinata_client
        self.deployer = deployer
        self.signer_wallet = signer_wallet
        self.token_admin = token_admin
        self.fee_recipient = fee_recipient
        self.tax_bps = tax_bps
        self.clanker_fee_bps = clanker_fee_bps
        self.paired_fee_bps = paired_fee_bps
        self.token_admin_enabled = token_admin_enabled
        self.token_reward_enabled = token_reward_enabled

    def _resolve_runtime_wallets(self) -> tuple[str, str, str]:
        """Resolve signer/admin/reward wallets with runtime overrides."""
        signer_wallet = self.signer_wallet
        token_admin = self.token_admin
        fee_recipient = self.fee_recipient

        if not hasattr(self.db, "get_runtime_setting"):
            return signer_wallet, token_admin, fee_recipient

        signer_override = self.db.get_runtime_setting("wallet.deployer_signer")
        admin_override = self.db.get_runtime_setting("wallet.token_admin")
        reward_override = self.db.get_runtime_setting("wallet.fee_recipient")

        if signer_override:
            signer_value = signer_override.strip()
            if not (_EVM_ADDRESS_RE.fullmatch(signer_value) or _PRIVATE_KEY_RE.fullmatch(signer_value)):
                raise DeployPreparationError(
                    "wallet_runtime: wallet.deployer_signer must be valid EVM address or private key"
                )
            signer_wallet = signer_value
        if admin_override:
            admin_value = admin_override.strip()
            if not _EVM_ADDRESS_RE.fullmatch(admin_value):
                raise DeployPreparationError("wallet_runtime: wallet.token_admin must be a valid EVM address")
            token_admin = admin_value
        if reward_override:
            reward_value = reward_override.strip()
            if not _EVM_ADDRESS_RE.fullmatch(reward_value):
                raise DeployPreparationError("wallet_runtime: wallet.fee_recipient must be a valid EVM address")
            fee_recipient = reward_value

        return signer_wallet, token_admin, fee_recipient

    async def prepare_deploy_request(
        self,
        candidate: SignalCandidate,
    ) -> DeployRequest:
        """Prepare a deploy request from an approved candidate."""
        logger.info("Preparing deploy request for candidate %s", candidate.id)

        try:
            extract_started = perf_counter()
            token_name, token_symbol = await self._extract_token_identity(candidate)
            logger.info(
                "deploy_prepare.extract_ms=%d candidate=%s",
                int((perf_counter() - extract_started) * 1000),
                candidate.id,
            )

            image_started = perf_counter()
            image_uri = await self._prepare_image(candidate, token_name, token_symbol)
            logger.info(
                "deploy_prepare.image_ms=%d candidate=%s",
                int((perf_counter() - image_started) * 1000),
                candidate.id,
            )

            signer_wallet, token_admin, fee_recipient = self._resolve_runtime_wallets()

            deploy_request = DeployRequest(
                candidate_id=candidate.id,
                platform="clanker",
                signer_wallet=signer_wallet,
                token_name=_normalize_token_name(token_name),
                token_symbol=_normalize_token_symbol(token_symbol),
                image_uri=image_uri,
                tax_bps=self.tax_bps,
                tax_recipient=fee_recipient,
                token_admin_enabled=self.token_admin_enabled,
                token_reward_enabled=self.token_reward_enabled,
                token_admin=token_admin,
                fee_recipient=fee_recipient,
                clanker_fee_bps=self.clanker_fee_bps,
                paired_fee_bps=self.paired_fee_bps,
                source=candidate.source,
                source_event_id=candidate.source_event_id,
                context_url=candidate.context_url,
                author_handle=candidate.author_handle,
                raw_context_excerpt=_build_context_excerpt(candidate.raw_text),
                metadata_description=_build_natural_description(
                    _normalize_token_name(token_name),
                    _normalize_token_symbol(token_symbol),
                    candidate,
                    _build_context_excerpt(candidate.raw_text),
                ),
            )

            preflight_started = perf_counter()
            await self.deployer.preflight(deploy_request)
            logger.info(
                "deploy_prepare.preflight_ms=%d candidate=%s",
                int((perf_counter() - preflight_started) * 1000),
                candidate.id,
            )

            return deploy_request

        except DeployPreparationError:
            raise
        except Exception as exc:
            logger.error("Deploy preparation failed for %s: %s", candidate.id, exc, exc_info=True)
            raise _step_error("prepare_deploy_request", exc) from exc

    async def _extract_token_identity(self, candidate: SignalCandidate) -> tuple[str, str]:
        """Extract token name and symbol from candidate."""
        if candidate.suggested_name and candidate.suggested_symbol:
            return candidate.suggested_name, candidate.suggested_symbol

        try:
            result = extract_token_identity(candidate.raw_text)
            return result.name, result.symbol
        except Exception as exc:
            raise _step_error("extract_identity", exc) from exc

    async def _prepare_image(self, candidate: SignalCandidate, token_name: str, token_symbol: str) -> str:
        """Fetch image and upload to IPFS."""
        image_candidates = _build_image_candidates(candidate, token_name, token_symbol)

        for image_url in image_candidates:
            try:
                image_bytes = await fetch_image_bytes(image_url)
                if not _is_image_content_plausible(image_bytes, image_url, candidate.source):
                    continue
                optimized_bytes, filename, content_type = _optimize_image_for_ipfs(image_bytes)
                ipfs_hash = await self.pinata.upload_file_bytes(
                    filename=filename,
                    content=optimized_bytes,
                    content_type=content_type,
                )
                return f"ipfs://{ipfs_hash}"
            except Exception:
                continue

        logger.warning("No valid context image for %s; using generated placeholder image", candidate.id)
        try:
            placeholder_bytes, filename, content_type = _build_placeholder_image(token_symbol)
            ipfs_hash = await self.pinata.upload_file_bytes(
                filename=filename,
                content=placeholder_bytes,
                content_type=content_type,
            )
            return f"ipfs://{ipfs_hash}"
        except Exception as exc:
            raise _step_error("image_prepare", exc) from exc

    async def get_candidate_by_id(self, candidate_id: str) -> SignalCandidate | None:
        """Retrieve a candidate from the database."""
        row = self.db.get_candidate(candidate_id)
        if not row:
            logger.warning("Candidate %s not found in database", candidate_id)
            return None

        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except Exception:
            metadata = {}

        return SignalCandidate(
            id=row["id"],
            source=row["source"],
            source_event_id=row["source_event_id"],
            observed_at=row["observed_at"] or datetime.now(timezone.utc).isoformat(),
            raw_text=row["raw_text"],
            fingerprint=row["fingerprint"],
            author_handle=metadata.get("author_handle"),
            context_url=metadata.get("context_url"),
            suggested_name=metadata.get("suggested_name"),
            suggested_symbol=metadata.get("suggested_symbol"),
            metadata=metadata,
        )
