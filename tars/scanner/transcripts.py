"""Transcript parsing — reads and tails Claude Code JSONL conversation files."""

from __future__ import annotations

import json
from pathlib import Path

from tars.scanner.models import ToolActivity, TranscriptEntry
from tars.scanner.utils import iso_to_dt, read_last_n_lines


def _get_timestamp(entry: dict) -> str:
    ts = entry.get("timestamp", "")
    if not ts:
        msg = entry.get("message", {})
        if msg:
            ts = msg.get("timestamp", "")
    return ts


def _extract_content_text(entry: dict) -> str:
    msg = entry.get("message", {})
    content = msg.get("content", "") if msg else entry.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "")
    return ""


def _extract_tool_uses(entry: dict) -> list[tuple[str, str]]:
    msg = entry.get("message", {})
    content = msg.get("content", []) if msg else []
    tools = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = block.get("name", "unknown")
                inp = block.get("input", {})
                desc = ""
                if isinstance(inp, dict):
                    desc = inp.get("description", "") or inp.get("command", "") or inp.get("prompt", "") or ""
                tools.append((name, desc))
    return tools


def parse_transcript_summary(transcript_file: Path | None) -> dict:
    """Parse transcript for summary stats (message count, tool count, last activity, tokens)."""
    info = {
        "last_activity": "",
        "last_activity_time": None,
        "tool_count": 0,
        "recent_tools": [],
        "message_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_create_tokens": 0,
    }

    if not transcript_file or not transcript_file.exists():
        return info

    lines = read_last_n_lines(transcript_file, 100)
    tools: list[ToolActivity] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        entry_type = entry.get("type", "")
        timestamp = _get_timestamp(entry)

        if entry_type == "user":
            info["message_count"] += 1
            text = _extract_content_text(entry)
            if text:
                info["last_activity"] = text[:120]
            if timestamp:
                info["last_activity_time"] = iso_to_dt(timestamp)

        elif entry_type == "assistant":
            tool_uses = _extract_tool_uses(entry)
            for tool_name, desc in tool_uses:
                tools.append(ToolActivity(tool_name=tool_name, timestamp=timestamp, description=desc))
                info["tool_count"] += 1
                info["last_activity"] = f"⚡ {tool_name}: {desc}"
            if timestamp:
                info["last_activity_time"] = iso_to_dt(timestamp)

        elif entry_type == "tool_use":
            tool_name = entry.get("tool_name", "unknown")
            desc = ""
            tool_input = entry.get("tool_input", {})
            if isinstance(tool_input, dict):
                desc = tool_input.get("description", "") or tool_input.get("command", "") or ""
            tools.append(ToolActivity(tool_name=tool_name, timestamp=timestamp, description=desc))
            info["tool_count"] += 1
            info["last_activity"] = f"⚡ {tool_name}: {desc}"
            if timestamp:
                info["last_activity_time"] = iso_to_dt(timestamp)

    info["recent_tools"] = tools[-5:]
    return info


def aggregate_token_usage(transcript_file: Path | None) -> dict:
    """Scan full transcript for token usage stats. Returns totals."""
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_create_tokens": 0,
    }
    if not transcript_file or not transcript_file.exists():
        return totals
    try:
        with open(transcript_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                # Fast filter — only parse lines that contain "usage"
                if '"usage"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") != "assistant":
                        continue
                    usage = entry.get("message", {}).get("usage", {})
                    if usage:
                        totals["input_tokens"] += usage.get("input_tokens", 0)
                        totals["output_tokens"] += usage.get("output_tokens", 0)
                        totals["cache_read_tokens"] += usage.get("cache_read_input_tokens", 0)
                        totals["cache_create_tokens"] += usage.get("cache_creation_input_tokens", 0)
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return totals


def _parse_raw_to_entries(raw: dict) -> list[TranscriptEntry]:
    """Parse a single JSONL line into TranscriptEntries."""
    entries: list[TranscriptEntry] = []
    entry_type = raw.get("type", "")
    timestamp = _get_timestamp(raw)

    if entry_type in ("agent-color", "permission-mode", "summary"):
        return entries

    if entry_type == "user":
        text = _extract_content_text(raw)
        if text:
            entries.append(TranscriptEntry(entry_type="user", timestamp=timestamp, content=text))

    elif entry_type == "assistant":
        msg = raw.get("message", {})
        content = msg.get("content", []) if msg else raw.get("content", [])
        text_parts = []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        name = block.get("name", "unknown")
                        inp = block.get("input", {})
                        desc = ""
                        if isinstance(inp, dict):
                            desc = inp.get("description", "") or inp.get("command", "") or inp.get("prompt", "") or ""
                        entries.append(TranscriptEntry(
                            entry_type="tool_use", timestamp=timestamp,
                            tool_name=name, tool_desc=desc,
                        ))
                    elif block.get("type") == "tool_result":
                        content_val = block.get("content", "")
                        if isinstance(content_val, list):
                            content_val = " ".join(
                                b.get("text", "") for b in content_val
                                if isinstance(b, dict) and b.get("type") == "text"
                            )
                        entries.append(TranscriptEntry(
                            entry_type="tool_result", timestamp=timestamp,
                            tool_name=block.get("tool_use_id", ""),
                            content=str(content_val),
                        ))
        elif isinstance(content, str):
            text_parts.append(content)

        combined = " ".join(text_parts).strip()
        if combined:
            entries.append(TranscriptEntry(entry_type="assistant", timestamp=timestamp, content=combined))

    elif entry_type == "tool_use":
        tool_input = raw.get("tool_input", {})
        desc = ""
        if isinstance(tool_input, dict):
            desc = tool_input.get("description", "") or tool_input.get("command", "") or ""
        entries.append(TranscriptEntry(
            entry_type="tool_use", timestamp=timestamp,
            tool_name=raw.get("tool_name", "unknown"), tool_desc=desc,
        ))

    elif entry_type == "tool_result":
        entries.append(TranscriptEntry(
            entry_type="tool_result", timestamp=timestamp,
            tool_name=raw.get("tool_name", ""),
            content=str(raw.get("tool_output", "")),
        ))

    return entries


def parse_transcript_entries(transcript_file: Path, last_n: int = 50) -> list[TranscriptEntry]:
    """Parse transcript into structured entries for display."""
    if not transcript_file or not transcript_file.exists():
        return []
    lines = read_last_n_lines(transcript_file, last_n)
    entries: list[TranscriptEntry] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        entries.extend(_parse_raw_to_entries(raw))
    return entries


def tail_transcript(transcript_file: Path, last_position: int = 0) -> tuple[list[TranscriptEntry], int]:
    """Read new entries since last_position. Returns (new_entries, new_position)."""
    if not transcript_file or not transcript_file.exists():
        return [], last_position
    try:
        file_size = transcript_file.stat().st_size
        if file_size <= last_position:
            return [], last_position
        with open(transcript_file, "r", encoding="utf-8", errors="replace") as f:
            f.seek(last_position)
            new_data = f.read()
            new_position = f.tell()
        entries: list[TranscriptEntry] = []
        for line in new_data.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            entries.extend(_parse_raw_to_entries(raw))
        return entries, new_position
    except OSError:
        return [], last_position
