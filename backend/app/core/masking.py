def mask_secret(value: str) -> str | None:
    """Masks a secret for display -- never returns enough to reconstruct it.

    "sk-abcdef123456" -> "sk-...3456". Empty/unset values return None
    (rendered as "Not configured" by the caller) rather than an empty
    string, and anything too short to mask safely returns a fixed
    placeholder instead of leaking most of a short secret.
    """
    if not value:
        return None
    if len(value) <= 8:
        return "••••"
    return f"{value[:3]}...{value[-4:]}"
