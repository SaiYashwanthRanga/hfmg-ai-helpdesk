from functools import lru_cache
from typing import Any

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://hfmg:hfmg_dev_local@localhost:5432/hfmg_helpdesk"
    # SQLAlchemy's defaults. A voice turn (SIP or simulator) holds a pooled
    # connection for its whole NLU round trip, so concurrent calls beyond
    # pool_size + max_overflow queue for a connection. See
    # docs/reviews/VOICE_SIMULATOR_PERFORMANCE.md before raising these.
    db_pool_size: int = 5
    db_max_overflow: int = 10

    cors_origins: str = "http://localhost:5173"
    # Folder holding the built dashboard (frontend/dist). When set, the backend
    # serves it at "/" so one process serves the UI and the API. Empty = API only.
    frontend_dist_dir: str = ""

    # Surfaced read-only by GET /settings/status (DESIGN.md §12) so the
    # dashboard never has to guess which deployment it's looking at.
    environment: str = "development"

    enable_email_notifications: bool = True

    # --- Email provider (Twilio SendGrid, via its API -- not SMTP) ---
    email_provider: str = "sendgrid"
    sendgrid_api_key: str = ""
    sendgrid_timeout_seconds: float = 10.0
    sendgrid_max_retries: int = 2

    # --- HFMG internal mail API (EMAIL_PROVIDER=hfmg_internal) ---
    # ORG_BASE is the API's base URL, e.g. http://172.22.6.188:177.
    # DEFAULT_FROM_EMAIL is the mailbox it sends as; when set it takes
    # precedence over EMAIL_FROM for this provider.
    org_base: str = ""
    default_from_email: str = ""
    hfmg_mail_timeout_seconds: float = 10.0
    hfmg_mail_max_retries: int = 2
    # Fixed sender/recipient by design: every ticket notification goes from
    # EMAIL_FROM to HELPDESK_EMAIL, not a per-call choice.
    email_from: str = "reminder@hfmg.net"
    helpdesk_email: str = "helpdesk@hfmg.net"

    enable_ai_summary: bool = False

    # --- LLM provider ---
    llm_provider: str = "openai"
    openai_api_key: str = ""
    openai_model: str = "gpt-5-nano"
    # Point at a compatible gateway or proxy without code changes.
    openai_base_url: str = ""
    openai_timeout_seconds: float = 20.0
    openai_max_retries: int = 2
    openai_max_output_tokens: int = 2000
    # Both are omitted from requests unless set. Reasoning models (the gpt-5
    # family) reject `temperature` outright, so sending it by default would
    # break every call; leaving these unset keeps new models working as they
    # ship, while older models can still be tuned via env vars.
    openai_temperature: float | None = None
    openai_reasoning_effort: str = ""

    # --- Voice agent (SIP: Nextiva -> SIPSorcery gateway -> this backend) ---
    voice_language: str = "en-US"
    # Seconds the simulator waits for the caller to start speaking.
    voice_gather_timeout: int = 6
    voice_nlu_timeout_seconds: float = 4.0
    # Retries share the timeout above, since a caller is waiting on the line.
    # Keep low: the gateway waits on this response while the caller is on the line.
    voice_nlu_max_retries: int = 1
    voice_max_misunderstandings: int = 3
    voice_max_email_attempts: int = 2
    # Model for the voice agent's NLU, separate from OPENAI_MODEL (summaries)
    # because a caller is waiting on every NLU call. Empty = OPENAI_MODEL.
    # See docs/reviews/VOICE_LATENCY_ACCURACY_REPORT.md for the measurements.
    # gpt-4.1-mini: the only model tested that classified every eval
    # description correctly (9/9 vs 5-6/9), at the same latency -- the
    # latency is network-bound, not model-bound.
    voice_nlu_model: str = "gpt-4.1-mini"
    # Send an identical second NLU request if the first hasn't answered by
    # then; first usable answer wins. 0 disables.
    voice_nlu_hedge_after_seconds: float = 2.5
    # Strict extraction of the impact facts (work_blocked, patient_care_affected,
    # affected_scope): the model must quote the caller's own words, the code checks the
    # quote, and anything not explicitly stated is unknown and asked about. Off = the
    # original inferring behaviour. See app/voice/strict_extraction.py.
    voice_strict_extraction: bool = False
    # With strict extraction, how many extra clarification questions (can you still work,
    # is anyone else affected, is patient care blocked) may be asked after the details
    # question. Each fact is asked about at most twice; unknown after that is final.
    voice_max_clarification_turns: int = 2
    # Read the caller's name back spelled when confidence in it is low
    # (unfamiliar name, low recognizer confidence), and let them correct it.
    voice_confirm_name: bool = True
    # Read the whole intake back (who, what, since when, impact, priority)
    # and let the caller correct it before the ticket is created.
    voice_confirm_summary: bool = True
    # Callback-number attempts before the agent carries on without one.
    voice_max_phone_attempts: int = 2
    # HFMG's departments, comma-separated. Empty = the built-in placeholder list
    # in app/voice/departments.py, which is NOT HFMG's real org chart.
    voice_departments: str = ""

    # --- AI Call Simulator (VOICE_SIMULATOR_DESIGN.md) ---
    # Off by default. The API has no authentication, so when on, anyone who
    # can reach it can spend OpenAI credit and create (SIMULATOR) tickets.
    # Refused outright when ENVIRONMENT=production -- see the validator below.
    enable_voice_simulator: bool = False
    simulator_max_audio_bytes: int = 2_000_000
    simulator_max_turns_per_session: int = 40
    simulator_max_concurrent_sessions: int = 30
    simulator_max_sessions_per_hour: int = 300
    simulator_session_idle_timeout_seconds: int = 600
    simulator_retention_days: int = 14
    # Whether a simulator session may opt in to the real "ticket created"
    # email. Off by default: with it on, anyone who can reach the API could
    # send the help desk inbox one email per simulated ticket.
    simulator_allow_notifications: bool = False

    # --- Speech (simulator only; on the phone path the SIP gateway does its own STT/TTS) ---
    speech_provider: str = "openai"
    speech_stt_model: str = "gpt-4o-mini-transcribe"
    # tts-1, not gpt-4o-mini-tts: measured first-audio p95 1.9 s vs 34-161 s
    # stalls (docs/reviews/VOICE_LATENCY_ACCURACY_REPORT.md).
    speech_tts_model: str = "tts-1"
    speech_tts_voice: str = "alloy"
    speech_tts_format: str = "mp3"
    speech_timeout_seconds: float = 10.0
    # Bias transcription with HFMG vocabulary and the expected answer shape.
    speech_stt_context: bool = True
    # Used when the caller is spelling (name correction, email) and to
    # re-transcribe anything that came back in a non-English script.
    # whisper-1 writes spelled letters literally; gpt-4o-*-transcribe
    # "corrects" them into words (eval/spelling_bench.py: 34/36 vs 29/36 names).
    speech_stt_spelling_model: str = "whisper-1"
    # Synthesized replies kept in memory by exact text (greeting, retries,
    # fixed questions repeat across calls). 0 disables.
    speech_tts_cache_entries: int = 256

    # LLM_PROVIDER=fake only (load tests, demos): simulated model latency.
    fake_llm_latency_ms: int = 300

    @model_validator(mode="after")
    def _simulator_never_in_production(self) -> "Settings":
        """Fail startup rather than expose the simulator on a production API."""
        if self.enable_voice_simulator and self.environment.strip().lower() in ("production", "prod"):
            raise ValueError(
                "ENABLE_VOICE_SIMULATOR=true is not allowed when ENVIRONMENT=production. "
                "The simulator has no authentication and spends OpenAI credit."
            )
        return self

    # Shared secret the SIP voice gateway (Sorcery) sends as a Bearer token to
    # /api/v1/voice/sip/*. Empty disables those endpoints entirely.
    voice_sip_gateway_token: str = ""

    @field_validator("openai_temperature", mode="before")
    @classmethod
    def _blank_temperature_is_unset(cls, value: Any) -> Any:
        """Treat `OPENAI_TEMPERATURE=` in a .env file as "don't send it".

        Without this, shipping the variable blank (which is the default, since
        reasoning models reject temperature) fails validation on startup.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
