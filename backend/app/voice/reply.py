"""What the agent says next: lines to speak, and whether to wait for a reply.

Transport-neutral. The SIP gateway speaks the lines and does the listening;
the AI Call Simulator speaks them through its own speech provider.
"""


class Reply:
    def __init__(self, lines: list[str], *, expect_reply: bool):
        self.lines = lines
        self.expect_reply = expect_reply

    @property
    def hangup(self) -> bool:
        return not self.expect_reply

    @property
    def text(self) -> str:
        return " ".join(self.lines).strip()


def ask(prompt: str) -> Reply:
    """Speak a prompt and wait for the caller's reply."""
    return Reply([prompt], expect_reply=True)


def say_and_hangup(*lines: str) -> Reply:
    """Speak one or more final lines, then end the call."""
    return Reply([line for line in lines if line], expect_reply=False)


def say_then_ask(statement: str, prompt: str) -> Reply:
    """Speak a statement, then ask a question in the same turn.

    Used to acknowledge what the caller said before asking the next question,
    which keeps the exchange from feeling like an interrogation.
    """
    return Reply([line for line in (statement, prompt) if line], expect_reply=True)
