"""CareLoop configuration — every knob comes from the environment (§20.1)."""
import os
import re

# Faults that can be injected for the game-day demo. Each one has a real call site:
#   bedrock_throttle  careloop/extract.py   model pass raises -> rules fallback
#   verify_down       careloop/extract.py   second pass skipped -> items flagged for confirmation
#   latency_800ms     careloop/mcp_server.py  tool call sleeps inside its timer -> p95 SLO fails
#   db_locked         careloop/store.py     every cell write raises -> reads still work
CHAOS_FLAGS = ("bedrock_throttle", "verify_down", "latency_800ms", "db_locked")


def _bool(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self, env=None):
        e = env if env is not None else os.environ
        self.auth_mode = e.get("CARELOOP_AUTH", "oauth")  # off | token | oauth
        self.demo = _bool_env(e, "CARELOOP_DEMO", "1")
        self.db_root = e.get("CARELOOP_DB_ROOT", "data")
        self.cell_keyring = os.path.join(self.db_root, "keyring")
        self.global_db = os.path.join(self.db_root, "careloop_global.db")
        self.public_url = (e.get("CARELOOP_PUBLIC_URL") or "http://localhost:8080").rstrip("/")
        self.mcp_url = self.public_url + "/mcp"
        self.client_id = e.get("CARELOOP_CLIENT_ID", "careloop-web-client")
        self.client_secret = e.get("CARELOOP_CLIENT_SECRET", "change-me-frontend")
        self.alexa_client_id = e.get("ALEXA_CLIENT_ID", "alexa-client-placeholder")
        self.alexa_client_secret = e.get("ALEXA_CLIENT_SECRET", "change-me-alexa")
        self.family_pin = e.get("CARELOOP_FAMILY_PIN", "481542")
        self.token = e.get("CARELOOP_TOKEN", "dev-token")
        extra = [x.strip() for x in e.get("CARELOOP_ALLOWED_ORIGINS", "").split(",") if x.strip()]
        dev = ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:4200",
               "http://127.0.0.1:4200", "http://localhost:4321", "http://127.0.0.1:4321"]
        self.allowed_origins = set(
            ["http://localhost:8080", "http://127.0.0.1:8080", "http://localhost", "http://127.0.0.1",
             "chrome-extension://ghcp-copilot-chat", "mcp-inspector", "http://mcp-inspector.localhost"]
            + dev + extra)
        self.redirect_uris = set(
            [x.strip() for x in e.get("CARELOOP_REDIRECT_URIS", "").split(",") if x.strip()])
        # Only loopback redirect URIs are ever defaulted in; anything else a real client
        # registers must appear in CARELOOP_REDIRECT_URIS and match exactly.
        self.redirect_uris |= {"http://localhost:8080/callback", self.public_url + "/callback"}
        self.bedrock_model = e.get("CARELOOP_BEDROCK_MODEL", "")
        self.bedrock_small_model = e.get("CARELOOP_BEDROCK_SMALL_MODEL", "")
        self.aws_region = e.get("AWS_REGION", "us-east-1")
        self.host = e.get("HOST", "127.0.0.1")
        self.port = int(e.get("PORT", "8080"))
        self.max_body_bytes = 1_000_000
        self.chaos = set(x.strip() for x in e.get("CARELOOP_CHAOS", "").split(",") if x.strip())
        self.chaos_unknown = sorted(self.chaos - set(CHAOS_FLAGS))
        self.record_dir = e.get("CARELOOP_RECORD_DIR", "")
        self.replay_dir = e.get("CARELOOP_REPLAY_DIR", "")
        self.encryption = _bool_env(e, "CARELOOP_ENCRYPT_FIELDS", "1")
        self.service_ttl = min(int(e.get("CARELOOP_SERVICE_TTL", "3600")), 3600)
        self.access_ttl = 3600
        self.refresh_ttl = 30 * 24 * 3600
        self.context_ttl = int(e.get("CARELOOP_CONTEXT_TTL", "1800"))
        self.log_dir = os.path.join(db_root_of(e), "logs")

    @staticmethod
    def from_env():
        return Settings()


def _bool_env(e, name, default="0"):
    return e.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def db_root_of(e):
    return e.get("CARELOOP_DB_ROOT", "data")


settings = Settings()

# Canonical enums (closed vocabulary — N1)
KINDS = ("medication", "appointment", "avoid", "task", "monitor", "diet")
STATUS = ("pending", "done", "superseded", "deleted")
SCHEDULE_ENUM = ("MORNING", "AFTER_FOOD", "BEFORE_FOOD", "MIDDAY", "AFTERNOON",
                 "EVENING", "BEDTIME", "NIGHTWAKING", "DAILY", "WEEKLY", "MONTHLY",
                 "ONCE", "ASNEEDED", "UNKNOWN")
ROLES = ("caregiver", "elder", "viewer")


def sanitize_person(name: str, maxlen: int = 40) -> str:
    name = re.sub(r"[\x00-\x1f<>\"'|]", "", str(name or ""))
    return name.strip()[:maxlen]
