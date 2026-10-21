"""Model calls with a hard budget guard. Prices are the verified per-million-token rates from weekly/costtest/pricing.yaml (2026-10-20).

Before every call the worst case (counted/estimated prompt + full max_tokens) is reserved against the daily and monthly caps;
a call that could exceed either cap is refused. Free-tier Gemini is charged $0 and its paid-equivalent is logged.
Paid providers run only when margin/models.yaml says `paid_approved: true` — set by the owner, never by the pipeline.
Keys come from the environment (GitHub Secrets) or the macOS Keychain; they are never printed or logged.
"""
import datetime as dt
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PRICES = {   # USD per 1M tokens: input, output (prompts ≤ 100k for Haiku)
    "claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00),
    "gemini-3.1-flash-lite": (0.25, 1.50), "gemini-3.5-flash-lite": (0.30, 2.50),
}
FREE_TIER = {"gemini-3.1-flash-lite", "gemini-3.5-flash-lite"}


class BudgetExceeded(RuntimeError):
    pass


def config():
    return yaml.safe_load((ROOT / "margin" / "models.yaml").read_text())


def secret(env, service):
    if os.environ.get(env):
        return os.environ[env]
    r = subprocess.run(["security", "find-generic-password", "-s", service, "-w"], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"no {env} in the environment and no Keychain item '{service}'")
    return r.stdout.strip()


class Ledger:
    """Append-only cost log (token counts and dollars only — no prompts, no outputs)."""

    def __init__(self, path, daily_cap, monthly_cap, paid_approved):
        self.path, self.daily_cap, self.monthly_cap, self.paid = Path(path), daily_cap, monthly_cap, paid_approved
        self.rows = [json.loads(line) for line in self.path.read_text().splitlines()] if self.path.exists() else []

    def spent(self, since):
        return sum(r["usd"] for r in self.rows if r["at"] >= since)

    def reserve(self, model, prompt_tokens, max_tokens):
        if model not in PRICES:
            raise BudgetExceeded(f"unknown model {model}")
        free = model in FREE_TIER and not os.environ.get("MARGIN_GEMINI_PAID")
        if not free and not self.paid:
            raise BudgetExceeded(f"{model} is paid and paid usage is not approved (margin/models.yaml)")
        pin, pout = PRICES[model]
        worst = 0.0 if free else (prompt_tokens * pin + max_tokens * pout) / 1e6
        now = dt.datetime.now(dt.timezone.utc)
        day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
        if self.spent(day) + worst > self.daily_cap or self.spent(month) + worst > self.monthly_cap:
            raise BudgetExceeded(f"{model} call (worst case ${worst:.4f}) would pass the cap "
                                 f"(today ${self.spent(day):.4f}/{self.daily_cap}, month ${self.spent(month):.4f}/{self.monthly_cap})")
        return free

    def settle(self, model, label, usage, free, secs, status):
        pin, pout = PRICES[model]
        equiv = (usage["input"] * pin + usage["output"] * pout) / 1e6
        row = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "label": label, "model": model, "input": usage["input"],
               "output": usage["output"], "usd": 0.0 if free else round(equiv, 6), "paid_equivalent": round(equiv, 6),
               "free_tier": free, "secs": round(secs, 1), "status": status}
        self.rows.append(row)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(row) + "\n")


def _gemini(model, system, user, schema, max_tokens):
    body = {"contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": schema, "maxOutputTokens": max_tokens}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    for wait in (0, 20, 45, 90, 180):            # free-tier rate limits: wait and retry; these retries cost nothing
        time.sleep(wait)
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"content-type": "application/json", "x-goog-api-key": secret("GEMINI_API_KEY", "margin-gemini")})
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=240).read())
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503):
                continue
            raise RuntimeError(f"gemini HTTP {e.code}")
        except (TimeoutError, urllib.error.URLError):
            continue
    else:
        raise RuntimeError("gemini unavailable after five attempts")
    um = r.get("usageMetadata", {})
    usage = {"input": um.get("promptTokenCount", 0), "output": um.get("candidatesTokenCount", 0) + um.get("thoughtsTokenCount", 0)}
    cand = (r.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []) if not p.get("thought"))
    return text, usage, cand.get("finishReason", "?")


def _claude(model, system, user, schema, max_tokens):
    import anthropic
    client = anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY", "margin-anthropic"))
    params = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": user}],
              "output_config": {"format": {"type": "json_schema", "schema": schema}}}
    if system:
        params["system"] = system
    if model == "claude-haiku-5-5":
        params["thinking"] = {"type": "disabled"}
    m = client.messages.create(**params)
    text = "".join(b.text for b in m.content if b.type == "text")
    return text, {"input": m.usage.input_tokens, "output": m.usage.output_tokens}, m.stop_reason


def call(model, system, user, schema, max_tokens, ledger, label):
    prompt_est = int(len(system + user) / 3.2) + 200            # conservative chars→tokens; the guard reserves the worst case
    free = ledger.reserve(model, prompt_est, max_tokens)
    for attempt in (1, 2):
        t0 = time.time()
        text, usage, stop = (_gemini if model.startswith("gemini") else _claude)(model, system, user, schema, max_tokens)
        try:
            data = json.loads(text)
            status = "ok"
        except json.JSONDecodeError:
            data, status = None, f"invalid JSON (stop={stop})"
        ledger.settle(model, label, usage, free, time.time() - t0, status)
        if data is not None:
            return data
        free = ledger.reserve(model, prompt_est, max_tokens)
    raise RuntimeError(f"{label}: no valid JSON after one retry")
