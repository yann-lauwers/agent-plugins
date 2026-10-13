import json
import os
import re

TAIL_BYTES = 4 * 1024 * 1024

_FENCED = re.compile(r"```.*?(?:```|\Z)", re.S)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_LINK_OR_URL = re.compile(r"\[[^\]\n]*\]\([^)\n]*\)|https?://\S+")
_EMPHASIS_ONLY = re.compile(r"^([*_]{1,3}).+\1$")
_QUESTION = re.compile(r"\?(?=[*_\"'”’)\]]*(?:\s|$))")
_NOT_PROSE = ("#", "|", ">")
_LOCAL_COMMAND_OUTPUT = ("<local-command-stdout>", "<local-command-stderr>")


def _records_backwards(transcript):
    with open(transcript, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - TAIL_BYTES))
        lines = fh.read().splitlines()
    if size > TAIL_BYTES:
        lines = lines[1:]
    for raw in reversed(lines):
        try:
            record = json.loads(raw)
        except ValueError:
            continue
        if isinstance(record, dict):
            yield record


def _typed_text(record):
    content = (record.get("message") or {}).get("content")
    return content.lstrip() if isinstance(content, str) else None


def _closing_message(transcript):
    local_command_ran = False
    for record in _records_backwards(transcript):
        kind = record.get("type")
        if kind == "assistant":
            return (record.get("message") or {}).get("content")
        if kind != "user" or record.get("isMeta") or record.get("isCompactSummary"):
            continue
        text = _typed_text(record)
        if text is None:
            return None
        if text.startswith(_LOCAL_COMMAND_OUTPUT):
            local_command_ran = True
            continue
        if text.startswith("/compact"):
            continue
        if "<command-name>" in text and (
                local_command_ran or "<command-name>/compact</command-name>" in text):
            local_command_ran = False
            continue
        return None
    return None


def asks_a_question(text):
    prose = _LINK_OR_URL.sub("", _INLINE_CODE.sub("", _FENCED.sub("", text)))
    for line in prose.splitlines():
        line = line.strip()
        if not line or line.startswith(_NOT_PROSE) or _EMPHASIS_ONLY.match(line):
            continue
        if _QUESTION.search(line):
            return True
    return False


def owners_turn(transcript):
    if not transcript:
        return False
    try:
        content = _closing_message(transcript)
    except OSError:
        return False
    if not isinstance(content, list):
        return False
    blocks = [b for b in content if isinstance(b, dict)]
    if any(b.get("type") == "tool_use" for b in blocks):
        return False
    return asks_a_question(
        "\n".join(b.get("text") or "" for b in blocks if b.get("type") == "text"))
